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

        query_param_str = ("?" + urllib.parse.urlencode(query_params)) if query_params else ""
        body = ""
        if payload is not None:
            body = json.dumps(payload, separators=(',', ':'))

        path = endpoint
        # Delta Exchange API pre-hash: method + timestamp + path + query_string + body
        timestamp, signature = self._generate_signature(method, path, query_param_str, body)

        url = self.base_url + path + query_param_str
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
        """Returns (available_usd, inr_equivalent, details_str)"""
        res = self.get_wallet_balances()
        if res.get("success"):
            meta = res.get("meta", {})
            net_equity = float(meta.get("net_equity", 0.0)) if meta.get("net_equity") else 0.0
            
            balances = res.get("result", [])
            inr_bal = 0.0
            usd_bal = 0.0
            usdt_bal = 0.0
            
            for b in balances:
                sym = str(b.get("asset_symbol", "")).upper()
                avail = float(b.get("available_balance", 0.0))
                if sym == "INR":
                    inr_bal += avail
                elif sym == "USD":
                    usd_bal += avail
                elif sym == "USDT":
                    usdt_bal += avail
                    
            if inr_bal > 0:
                # If net_equity is roughly equal to inr_bal (within 10%), it's reported in INR
                if net_equity > 0 and abs(net_equity - inr_bal) / inr_bal < 0.10:
                    calc_usd = inr_bal / 84.0
                elif net_equity > 0 and abs(net_equity - (inr_bal / 84.0)) / (inr_bal / 84.0) < 0.20:
                    calc_usd = net_equity
                elif usd_bal > 0 or usdt_bal > 0:
                    calc_usd = (usd_bal + usdt_bal) + (inr_bal / 84.0)
                else:
                    calc_usd = inr_bal / 84.0
            elif usd_bal > 0 or usdt_bal > 0:
                calc_usd = usd_bal + usdt_bal
            elif net_equity > 0:
                calc_usd = net_equity
            else:
                calc_usd = 0.0
                
            inr_val = inr_bal if inr_bal > 0 else (calc_usd * 84.0)
            desc = f"${calc_usd:,.2f} USD (~₹{inr_val:,.0f} INR)"
            return calc_usd, inr_val, desc
        else:
            err = res.get("error", "Unknown API error")
            if isinstance(err, dict):
                err_msg = err.get("message") or err.get("error", {}).get("message") or json.dumps(err)
            else:
                err_msg = str(err)
            print(f"[WALLET BALANCE ERROR] Delta API returned: {err_msg}", flush=True)
            return 0.0, 0.0, f"Error: {err_msg}"

    def set_leverage(self, product_id=27, leverage=40.0):
        """Configures order leverage for BTCUSD (Product ID 27)."""
        payload = {"leverage": str(int(leverage))}
        return self._request("POST", f"/v2/products/{product_id}/orders/leverage", payload=payload)

    def place_market_order(self, product_id=27, size_contracts=1, side="buy", reduce_only=False):
        """
        Places a market order on Delta Exchange India.
        side: 'buy' or 'sell'
        size: integer number of contracts (1 contract = 0.001 BTC)
        reduce_only: if True, only closes/reduces existing position
        """
        payload = {
            "product_id": int(product_id),
            "size": int(size_contracts),
            "side": side.lower(),
            "order_type": "market_order"
        }
        if reduce_only:
            payload["reduce_only"] = True
        return self._request("POST", "/v2/orders", payload=payload)

    def close_position(self, product_id=27, size_contracts=1, side_to_close="buy"):
        """
        Closes position by sending an opposing market order with reduce_only=True.
        If currently LONG (buy), closing side is 'sell'.
        If currently SHORT (sell), closing side is 'buy'.
        """
        if size_contracts <= 0:
            return {"success": True, "message": "Nothing to close"}
        close_side = "sell" if side_to_close.lower() == "buy" else "buy"
        return self.place_market_order(product_id=product_id, size_contracts=size_contracts, side=close_side, reduce_only=True)

    def get_open_positions(self, product_id=27):
        """Fetches active open positions for the product."""
        res = self._request("GET", "/v2/positions")
        if res.get("success"):
            positions = res.get("result", [])
            return [p for p in positions if p.get("product_id") == product_id and int(p.get("size", 0)) != 0]
        return []

    def verify_api_connection(self):
        """Tests live API key permissions against Delta Exchange India."""
        res = self.get_wallet_balances()
        if not res.get("success"):
            err = res.get("error", "Unauthorized or invalid credentials")
            err_msg = err.get("message") if isinstance(err, dict) else str(err)
            return False, f"Auth Error: {err_msg}"
        return True, "API Key authenticated and operational"
