import os
import sys
import time
import json
import hmac
import hashlib
import urllib.request
import urllib.parse

class DeltaExchangeLiveClient:
    def __init__(self, api_key=None, api_secret=None, base_url="https://api.india.delta.exchange"):
        self.api_key = api_key or os.environ.get("DELTA_API_KEY", "").strip()
        self.api_secret = api_secret or os.environ.get("DELTA_API_SECRET", "").strip()
        self.base_url = base_url.rstrip("/")

    def _generate_signature(self, method, path, query_string="", body=""):
        timestamp = str(int(time.time()))
        # Signature: METHOD + TIMESTAMP + PATH + QUERY_STRING + BODY
        signature_data = method + timestamp + path + query_string + body
        sig = hmac.new(
            self.api_secret.encode("utf-8"),
            signature_data.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return timestamp, sig

    def _request(self, method, endpoint, query_params=None, payload=None):
        if not self.api_key or not self.api_secret:
            return {"success": False, "error": "DELTA_API_KEY or DELTA_API_SECRET not configured"}

        query_string = ""
        if query_params:
            query_string = "?" + urllib.parse.urlencode(query_params)
            
        body = ""
        if payload is not None:
            body = json.dumps(payload, separators=(',', ':'))

        path = endpoint
        timestamp, signature = self._generate_signature(method, path, query_string, body)

        url = self.base_url + path + query_string
        headers = {
            "api-key": self.api_key,
            "signature": signature,
            "timestamp": timestamp,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ICT-TradingBot/2.0"
        }

        req = urllib.request.Request(
            url,
            data=body.encode("utf-8") if body else None,
            headers=headers,
            method=method
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                return res_data
        except urllib.error.HTTPError as e:
            try:
                err_content = json.loads(e.read().decode("utf-8"))
                return {"success": False, "status_code": e.code, "error": err_content}
            except Exception:
                return {"success": False, "status_code": e.code, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_wallet_balances(self):
        """Fetches live wallet balance across all assets."""
        return self._request("GET", "/v2/wallet/balances")

    def get_available_balance_usd(self):
        """Returns the total available margin balance in USD."""
        res = self.get_wallet_balances()
        if res.get("success"):
            balances = res.get("result", [])
            for b in balances:
                # Delta Exchange settles BTCUSD in USD
                if b.get("asset_symbol") in ["USD", "USDT"]:
                    return float(b.get("available_balance", 0.0))
            if balances:
                return float(balances[0].get("available_balance", 0.0))
        return 0.0

    def set_leverage(self, product_id=27, leverage=50.0):
        """Configures order leverage for BTCUSD (Product ID 27)."""
        payload = {"leverage": str(int(leverage))}
        return self._request("POST", f"/v2/products/{product_id}/orders/leverage", payload=payload)

    def place_market_order(self, product_id=27, size_contracts=1, side="buy"):
        """
        Places a market order on Delta Exchange India.
        side: 'buy' or 'sell'
        size: integer number of contracts (1 contract = 0.001 BTC)
        """
        payload = {
            "product_id": int(product_id),
            "size": int(size_contracts),
            "side": side.lower(),
            "order_type": "market_order"
        }
        return self._request("POST", "/v2/orders", payload=payload)

    def close_position(self, product_id=27, size_contracts=1, side_to_close="buy"):
        """
        Closes position by sending an opposing market order.
        If currently LONG (buy), closing side is 'sell'.
        If currently SHORT (sell), closing side is 'buy'.
        """
        close_side = "sell" if side_to_close.lower() == "buy" else "buy"
        return self.place_market_order(product_id=product_id, size_contracts=size_contracts, side=close_side)

    def get_open_positions(self, product_id=27):
        """Fetches active open positions for the product."""
        res = self._request("GET", "/v2/positions")
        if res.get("success"):
            positions = res.get("result", [])
            return [p for p in positions if p.get("product_id") == product_id and int(p.get("size", 0)) != 0]
        return []
