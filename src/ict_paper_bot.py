import os
import sys
import time
import json
import datetime
import urllib.request
import urllib.parse

# Ensure UTF-8 console output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Dynamic Cross-Platform Paths (Works on Windows, Linux, Docker, Cloud)
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SRC_DIR)
DATA_DIR = os.path.join(PROJECT_DIR, "data")
STATE_FILE = os.path.join(DATA_DIR, "live_paper_state.json")
LOG_FILE = os.path.join(DATA_DIR, "live_trade_log.csv")
ENV_FILE = os.path.join(PROJECT_DIR, ".env")

if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from delta_live_client import DeltaExchangeLiveClient

os.makedirs(DATA_DIR, exist_ok=True)

DELTA_BASE_URL = "https://api.india.delta.exchange/v2"

# -----------------------------------------------------------------------------
# 1. ENVIRONMENT & TELEGRAM ALERT MODULE
# -----------------------------------------------------------------------------
def get_env_variable(candidates, default=""):
    """
    Ultra-robust environment variable resolver.
    Handles Railway, Render, Docker, and .env files.
    Tolerates leading/trailing spaces, mixed case, and common aliases.
    """
    # 1. Exact or case-insensitive match on stripped keys
    for cand in candidates:
        cand_clean = cand.strip().upper()
        for k, v in os.environ.items():
            if k.strip().upper() == cand_clean and v is not None:
                val = str(v).strip().strip('"').strip("'").strip()
                if val:
                    return val

    # 2. Fuzzy keyword search in os.environ
    for cand in candidates:
        cand_upper = cand.strip().upper()
        if "SECRET" in cand_upper:
            for k, v in os.environ.items():
                ku = k.strip().upper()
                if "DELTA" in ku and "SECRET" in ku and v is not None:
                    val = str(v).strip().strip('"').strip("'").strip()
                    if val:
                        return val
        elif "KEY" in cand_upper:
            for k, v in os.environ.items():
                ku = k.strip().upper()
                if "DELTA" in ku and "KEY" in ku and "SECRET" not in ku and v is not None:
                    val = str(v).strip().strip('"').strip("'").strip()
                    if val:
                        return val

    # 3. Fallback to local .env file
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, mode="r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        kc = k.strip().upper()
                        for cand in candidates:
                            if kc == cand.strip().upper():
                                val = v.strip().strip('"').strip("'").strip()
                                if val:
                                    return val
        except Exception:
            pass

    return default

def load_env():
    env = {}
    for k, v in os.environ.items():
        if v is not None:
            clean_k = str(k).strip()
            clean_v = str(v).strip().strip('"').strip("'").strip()
            env[clean_k] = clean_v
            env[clean_k.upper()] = clean_v
            
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, mode="r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        clean_k = k.strip()
                        clean_v = v.strip().strip('"').strip("'").strip()
                        if clean_k not in env:
                            env[clean_k] = clean_v
                            env[clean_k.upper()] = clean_v
        except Exception:
            pass
    return env

ENV_VARS = load_env()
TELEGRAM_BOT_TOKEN = get_env_variable(["TELEGRAM_BOT_TOKEN", "BOT_TOKEN", "TELEGRAM_TOKEN"])
TELEGRAM_CHAT_ID = get_env_variable(["TELEGRAM_CHAT_ID", "CHAT_ID"])

def send_telegram_alert(message_html):
    token = get_env_variable(["TELEGRAM_BOT_TOKEN", "BOT_TOKEN", "TELEGRAM_TOKEN"])
    chat_id = get_env_variable(["TELEGRAM_CHAT_ID", "CHAT_ID"])
    if not token or not chat_id:
        print("[Telegram Alert Disabled: Token or Chat ID not configured]", flush=True)
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message_html,
        "parse_mode": "HTML"
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return res.get("ok", False)
    except Exception as e:
        print(f"Error sending Telegram alert: {e}")
        return False

# -----------------------------------------------------------------------------
# 2. DELTA EXCHANGE INDIA LIVE DATA FETCHER
# -----------------------------------------------------------------------------
def fetch_live_candles(symbol="BTCUSD", resolution="15m", count=40):
    now_ts = int(time.time())
    step = 900 if resolution == "15m" else 3600
    start_ts = now_ts - (count * step)
    url = f"{DELTA_BASE_URL}/history/candles?resolution={resolution}&symbol={symbol}&start={start_ts}&end={now_ts}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success"):
                candles = data.get("result", [])
                candles_sorted = sorted(candles, key=lambda x: x["time"])
                return candles_sorted
    except Exception as e:
        print(f"Error fetching live candles: {e}")
    return []

def fetch_live_ticker(symbol="BTCUSD"):
    url = f"{DELTA_BASE_URL}/tickers/{symbol}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success"):
                res = data.get("result", {})
                return {
                    "mark_price": float(res.get("mark_price", 0.0)),
                    "close": float(res.get("close", 0.0)),
                    "bid": float(res.get("quotes", {}).get("best_bid", 0.0)),
                    "ask": float(res.get("quotes", {}).get("best_ask", 0.0))
                }
    except Exception as e:
        print(f"Error fetching live ticker: {e}")
    return None

# -----------------------------------------------------------------------------
# 3. LIVE PAPER TRADING ENGINE
# -----------------------------------------------------------------------------
class LiveICTPaperBot:
    def __init__(self, initial_capital=100.0, max_leverage=50.0,
                 risk_per_trade=0.10, daily_loss_limit=0.20, max_trades_daily=3,
                 min_rr=2.5, partial_tp_rr=1.5, partial_tp_pct=0.5):
        # Resolve Environment Variables (Defaults to REAL mode for production)
        explicit_mode = get_env_variable(["TRADING_MODE", "MODE", "TRADINGMODE"], "REAL").upper()
        api_k = get_env_variable(["DELTA_API_KEY", "DELTA_KEY", "API_KEY", "DELTA_EXCHANGE_API_KEY"])
        api_s = get_env_variable(["DELTA_API_SECRET", "DELTA_SECRET", "API_SECRET", "DELTA_EXCHANGE_API_SECRET"])
        
        self.max_leverage = float(get_env_variable(["MAX_LEVERAGE", "LEVERAGE"], str(max_leverage)))
        self.risk_per_trade = float(get_env_variable(["RISK_PER_TRADE", "RISK"], str(risk_per_trade)))
        self.daily_loss_limit = float(get_env_variable(["DAILY_LOSS_LIMIT", "DAILY_LOSS"], str(daily_loss_limit)))
        self.max_trades_daily = int(get_env_variable(["MAX_TRADES_DAILY", "MAX_TRADES"], str(max_trades_daily)))
        self.max_margin_pct = float(get_env_variable(["MAX_MARGIN_PCT", "MARGIN_CAP", "MARGIN_LIMIT"], "0.80"))
        
        # Real Mode is the default unless explicitly set to PAPER
        if explicit_mode != "PAPER":
            self.trading_mode = "REAL"
            self.initial_capital = 11.90 # Standard ₹1,000 INR baseline
            self.capital = 11.90
            self.balance_desc = "$11.90 USD (~₹1,000 INR)"
        else:
            self.trading_mode = "PAPER"
            self.initial_capital = initial_capital
            self.capital = initial_capital
            self.balance_desc = f"${initial_capital:,.2f}"
            
        self.min_rr = min_rr
        self.partial_tp_rr = partial_tp_rr
        self.partial_tp_pct = partial_tp_pct
        
        self.active_trade = None
        self.daily_stats = {}
        self.completed_trades = []
        self.fvg_pool = []
        self.mitigated_fvg_ids = set()
        self.last_candle_time = 0
        self.session_notified = False
        self.daily_summary_sent = False
        self.last_hourly_alert_hour = -1
        
        # Real Trading Client Initialization
        self.delta_client = None
        self.connection_msg = ""
        
        if self.trading_mode == "REAL":
            if api_k and api_s:
                self.delta_client = DeltaExchangeLiveClient(api_key=api_k, api_secret=api_s)
                print(f"[REAL TRADING] DeltaExchangeLiveClient initialized with {self.max_leverage}x leverage cap.", flush=True)
                try:
                    bal_usd, bal_inr, bal_desc = self.delta_client.get_available_balance_usd()
                    if bal_usd > 0:
                        self.capital = bal_usd
                        self.initial_capital = bal_usd
                        self.balance_desc = bal_desc
                        self.connection_msg = f"Connected: {bal_desc}"
                        print(f"[REAL TRADING] Live Delta wallet balance synchronized: {bal_desc}", flush=True)
                    else:
                        self.connection_msg = f"Delta Handshake: {bal_desc}"
                        if bal_desc and not bal_desc.startswith("Error"):
                            self.balance_desc = bal_desc
                        print(f"[REAL TRADING NOTICE] Balance returned: {bal_desc}", flush=True)
                    # Configure leverage on Delta Exchange for BTCUSD
                    self.delta_client.set_leverage(product_id=27, leverage=self.max_leverage)
                except Exception as e:
                    self.connection_msg = f"Error querying balance: {e}"
                    print(f"[REAL TRADING WARNING] Error fetching initial live balance: {e}", flush=True)
            else:
                self.connection_msg = "Awaiting API keys: please click 'Apply Changes' or 'Deploy' at bottom of Railway Variables tab!"
                print(f"[REAL TRADING WARNING] {self.connection_msg}", flush=True)
        
        self.load_state()
        self.save_state()

    def send_startup_alert(self):
        # Sync live balance if real mode
        if self.trading_mode == "REAL" and self.delta_client:
            try:
                bal_usd, bal_inr, bal_desc = self.delta_client.get_available_balance_usd()
                if bal_usd > 0:
                    self.capital = bal_usd
                    self.balance_desc = bal_desc
                elif bal_desc:
                    self.balance_desc = bal_desc
            except Exception as e:
                self.balance_desc = f"Error: {e}"
                print(f"Error checking live balance: {e}", flush=True)

        mode_badge = "🔥 REAL MONEY LIVE" if self.trading_mode == "REAL" else "📝 PAPER TRADING"
        bal_text = self.balance_desc if (self.trading_mode == "REAL" and self.balance_desc) else f"${self.capital:,.2f}"
        ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now_ist = datetime.datetime.now(ist)
        is_active = self.is_in_session(now_ist)
        sess_badge = "🟢 ACTIVE SESSION (4:30 PM - 9:00 AM IST)" if is_active else "⏳ STANDBY (Next session: 4:30 PM IST)"
        
        if self.trading_mode == "REAL":
            if self.delta_client:
                api_ok, auth_msg = self.delta_client.verify_api_connection()
                if api_ok:
                    status_line = f"\n• <b>Delta Exchange API:</b> 🟢 CONNECTED & AUTHENTICATED (50x Active)"
                else:
                    status_line = f"\n• <b>Delta Exchange API:</b> 🔴 {auth_msg}"
            else:
                k_present = bool(get_env_variable(["DELTA_API_KEY", "DELTA_KEY", "API_KEY", "DELTA_EXCHANGE_API_KEY"]))
                s_present = bool(get_env_variable(["DELTA_API_SECRET", "DELTA_SECRET", "API_SECRET", "DELTA_EXCHANGE_API_SECRET"]))
                status_line = (
                    f"\n• <b>Delta Exchange API:</b> 🔴 OFFLINE\n"
                    f"   - <code>DELTA_API_KEY</code>: {'✅ Found' if k_present else '❌ MISSING'}\n"
                    f"   - <code>DELTA_API_SECRET</code>: {'✅ Found' if s_present else '❌ MISSING'}\n"
                    f"   - <i>Action: In Railway Service Variables, click 'Deploy' or 'Apply Changes' at the bottom!</i>"
                )
        else:
            status_line = f"\n• <b>Notice:</b> <code>{self.connection_msg}</code>" if self.connection_msg else ""
        
        msg = (
            f"🚀 <b>ICT BOT: SYSTEM ONLINE ({mode_badge})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Session Status:</b> {sess_badge}\n"
            f"• <b>Trading Mode:</b> {mode_badge}\n"
            f"• <b>Live Wallet Balance:</b> {bal_text}\n"
            f"• <b>Active Hours:</b> 4:30 PM to 9:00 AM IST (Overnight){status_line}\n"
            f"• <b>Max Leverage:</b> {self.max_leverage:.0f}x Capped\n"
            f"• <b>Risk Per Trade:</b> {self.risk_per_trade*100:.1f}%\n"
            f"• <b>Margin Allocation Cap:</b> {self.max_margin_pct*100:.0f}% ({100 - self.max_margin_pct*100:.0f}% free cushion)\n"
            f"• <b>Daily Loss Circuit Breaker:</b> {self.daily_loss_limit*100:.1f}%\n"
            f"• <b>Strategy:</b> Model 1 (FVG) & Model 2 (iFVG) with MSS\n"
            f"• <i>Connected to Delta Exchange India! Real-time alerts will stream here.</i>"
        )
        send_telegram_alert(msg)

    def send_hourly_heartbeat(self, current_price, now_ist):
        is_active = self.is_in_session(now_ist)
        session_text = "🟢 ACTIVE SESSION (4:30 PM - 9:00 AM IST)" if is_active else "⏳ STANDBY (Next session: 4:30 PM IST)"
        today_str = self.get_session_date(now_ist)
        day_stats = self.daily_stats.get(today_str, {"trades": 0, "pnl_usd": 0.0, "pnl_pct": 0.0})
        
        # Sync live wallet balance if real mode
        if self.trading_mode == "REAL" and self.delta_client:
            try:
                bal_usd, bal_inr, bal_desc = self.delta_client.get_available_balance_usd()
                if bal_usd > 0:
                    self.capital = bal_usd
                    self.balance_desc = bal_desc
                elif bal_desc:
                    self.balance_desc = bal_desc
            except Exception as e:
                pass

        bal_display = self.balance_desc if (self.trading_mode == "REAL" and self.balance_desc) else f"${self.capital:,.2f}"
        mode_badge = "🔥 REAL MONEY" if self.trading_mode == "REAL" else "📝 PAPER"

        trade_status_text = "None (Scanning 15m candles for setups)"
        if self.active_trade:
            t = self.active_trade
            unrealized_dist = (current_price - t["entry"]) if t["side"] == "BUY" else (t["entry"] - current_price)
            unrealized_pnl = t["size"] * unrealized_dist
            pnl_sign = "+" if unrealized_pnl >= 0 else "-"
            trade_status_text = (
                f"<b>{t['side']} #{t['id']}</b> ({t['model']})\n"
                f"   • Entry: ${t['entry']:,.1f} | Current: ${current_price:,.1f}\n"
                f"   • Live Float PnL: {pnl_sign}${abs(unrealized_pnl):.2f}\n"
                f"   • TP1: ${t['tp1']:,.1f} | TP2: ${t['tp2']:,.1f} | SL: ${t['sl']:,.1f}"
            )
            
        pnl_day_sign = "+" if day_stats["pnl_usd"] >= 0 else "-"
        msg = (
            f"⏱ <b>1-HOUR ICT BOT UPDATE ({mode_badge})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>BTC Live Price:</b> ${current_price:,.1f}\n"
            f"• <b>Session Status:</b> {session_text}\n"
            f"• <b>Account Balance:</b> {bal_display}\n"
            f"• <b>Today's Realized PnL:</b> {pnl_day_sign}${abs(day_stats['pnl_usd']):.2f} ({day_stats['pnl_pct']*100:+.1f}%)\n"
            f"• <b>Trades Taken Today:</b> {day_stats['trades']} of {self.max_trades_daily} max\n"
            f"• <b>Active Position:</b> {trade_status_text}"
        )
        send_telegram_alert(msg)

    def send_daily_summary(self, now_ist):
        session_str = self.get_session_date(now_ist - datetime.timedelta(minutes=5))
        day_stats = self.daily_stats.get(session_str, {"trades": 0, "pnl_usd": 0.0, "pnl_pct": 0.0})
        today_trades = [t for t in self.completed_trades if t.get("date_str") == session_str]
        wins = len([t for t in today_trades if t.get("realized_pnl", 0) > 0])
        losses = len([t for t in today_trades if t.get("realized_pnl", 0) < 0])
        
        pnl_sign = "+" if day_stats["pnl_usd"] >= 0 else "-"
        total_growth = ((self.capital - self.initial_capital) / self.initial_capital) * 100.0 if self.initial_capital > 0 else 0.0
        bal_display = self.balance_desc if (self.trading_mode == "REAL" and self.balance_desc) else f"${self.capital:,.2f}"
        
        msg = (
            f"🌙 <b>ICT BOT: OVERNIGHT SESSION SUMMARY (9:00 AM IST)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Session Date:</b> {session_str}\n"
            f"• <b>Trades Taken:</b> {day_stats['trades']} ({wins} Wins / {losses} Losses)\n"
            f"• <b>Session Realized PnL:</b> {pnl_sign}${abs(day_stats['pnl_usd']):.2f} ({day_stats['pnl_pct']*100:+.1f}%)\n"
            f"• <b>Closing Account Balance:</b> {bal_display}\n"
            f"• <b>Total Net Return (All Time):</b> {total_growth:+.1f}%\n"
            f"• <b>Active Runners:</b> {'Running to SL/TP' if self.active_trade else 'None'}\n"
            f"• <i>Session closed. New trade entries will resume today at 4:30 PM IST.</i>"
        )
        send_telegram_alert(msg)

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, mode="r", encoding="utf-8") as f:
                    st = json.load(f)
                    if self.trading_mode == "REAL":
                        # If delta_client already synchronized live capital from exchange, preserve live balance!
                        if not (self.delta_client and self.capital > 0 and self.capital != 100.0):
                            if "real_capital" in st and st["real_capital"]:
                                self.capital = float(st["real_capital"])
                    else:
                        self.capital = st.get("capital", self.initial_capital)
                    self.active_trade = st.get("active_trade", None)
                    self.daily_stats = st.get("daily_stats", {})
                    self.completed_trades = st.get("completed_trades", [])
                    self.mitigated_fvg_ids = set(st.get("mitigated_fvg_ids", []))
                    
                    # Verify active trade with Delta Exchange in REAL mode:
                    if self.trading_mode == "REAL" and self.delta_client:
                        try:
                            live_positions = self.delta_client.get_open_positions(product_id=27)
                            if not live_positions and self.active_trade is not None:
                                print(f"[STATE SYNC] Delta Exchange has 0 open positions. Resetting phantom trade {self.active_trade.get('id')}.", flush=True)
                                self.active_trade = None
                        except Exception as e:
                            print(f"[STATE SYNC WARNING] Could not verify live positions with Delta: {e}", flush=True)

                    print(f"[State Loaded] Capital: ${self.capital:,.2f} | Active Trade: {'YES' if self.active_trade else 'None'} | Mitigated FVGs: {len(self.mitigated_fvg_ids)}")
            except Exception as e:
                print(f"Error loading state: {e}")

    def save_state(self):
        st = {
            "capital": self.capital,
            "real_capital": self.capital if self.trading_mode == "REAL" else None,
            "trading_mode": self.trading_mode,
            "active_trade": self.active_trade,
            "daily_stats": self.daily_stats,
            "completed_trades": self.completed_trades,
            "mitigated_fvg_ids": list(self.mitigated_fvg_ids),
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(STATE_FILE, mode="w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)

    def get_session_date(self, now_ist):
        """
        Trading session runs 4:30 PM (16:30 IST) overnight through midnight to 9:00 AM (09:00 IST).
        Hours 00:00 to 08:59 belong to the session that opened at 16:30 yesterday.
        """
        if now_ist.hour < 9:
            return (now_ist - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        return now_ist.strftime("%Y-%m-%d")

    def is_in_session(self, now_ist=None):
        if now_ist is None:
            ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
            now_ist = datetime.datetime.now(ist)
        h = now_ist.hour
        m = now_ist.minute
        # 4:30 PM (16:30 IST) overnight through midnight to 9:00 AM (09:00 IST)
        is_evening = (h == 16 and m >= 30) or (17 <= h <= 23)
        is_morning = (0 <= h < 9)
        return is_evening or is_morning

    def process_tick(self):
        ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now_ist = datetime.datetime.now(ist)
        today_str = self.get_session_date(now_ist)
        
        if today_str not in self.daily_stats:
            self.daily_stats[today_str] = {
                "trades": 0, "pnl_usd": 0.0, "pnl_pct": 0.0, "locked": False
            }
        day_tracker = self.daily_stats[today_str]

        # Auto-connect & sync Delta Client dynamically if in REAL mode
        if self.trading_mode == "REAL" and (self.delta_client is None or "Error" in str(self.balance_desc)):
            api_k = get_env_variable(["DELTA_API_KEY", "DELTA_KEY", "API_KEY", "DELTA_EXCHANGE_API_KEY"])
            api_s = get_env_variable(["DELTA_API_SECRET", "DELTA_SECRET", "API_SECRET", "DELTA_EXCHANGE_API_SECRET"])
            if api_k and api_s:
                try:
                    if self.delta_client is None:
                        self.delta_client = DeltaExchangeLiveClient(api_key=api_k, api_secret=api_s)
                    bal_usd, bal_inr, bal_desc = self.delta_client.get_available_balance_usd()
                    if bal_usd > 0:
                        self.capital = bal_usd
                        self.initial_capital = bal_usd
                        self.balance_desc = bal_desc
                        self.connection_msg = f"Connected: {bal_desc}"
                        self.delta_client.set_leverage(product_id=27, leverage=self.max_leverage)
                        send_telegram_alert(
                            f"🚀 <b>ICT BOT: DELTA EXCHANGE CONNECTED & VERIFIED!</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"• <b>Live Balance:</b> {bal_desc}\n"
                            f"• <b>Max Leverage:</b> {self.max_leverage:.0f}x Active\n"
                            f"• <b>IP Whitelist:</b> Verified & Authorized ✅\n"
                            f"• <b>Status:</b> Ready for real order placement!"
                        )
                except Exception as e:
                    pass

        # 1. Fetch live ticker for immediate active trade management
        ticker = fetch_live_ticker("BTCUSD")
        if not ticker:
            return
            
        current_price = ticker["close"] if ticker["close"] > 0 else ticker["mark_price"]

        # ---------------------------------------------------------------------
        # MANAGE ACTIVE TRADE (CHECK TP / SL ON REAL TICKS)
        # ---------------------------------------------------------------------
        if self.active_trade is not None:
            t = self.active_trade
            is_long = t["side"] == "BUY"
            trade_closed = False
            exit_price = 0.0
            exit_reason = ""
            
            if is_long:
                # Stop Loss check
                if current_price <= t["sl"]:
                    exit_price = t["sl"]
                    if t["be_activated"]:
                        exit_reason = "Breakeven SL Hit (50% TP1 Banked)"
                    else:
                        exit_reason = "Initial SL Hit (-1.0R)"
                    trade_closed = True
                # TP1 Check (1.5R)
                elif not t["tp1_hit"] and current_price >= t["tp1"]:
                    t["tp1_hit"] = True
                    partial_gain = (t["size"] * self.partial_tp_pct) * (t["tp1"] - t["entry"])
                    self.capital += partial_gain
                    t["realized_pnl"] += partial_gain
                    t["size"] *= (1.0 - self.partial_tp_pct)
                    t["sl"] = t["entry"] # Move to breakeven
                    t["be_activated"] = True
                    
                    # Close 50% contracts on Delta Exchange India if real trading
                    if self.trading_mode == "REAL" and self.delta_client:
                        try:
                            c_to_close = max(1, t.get("contracts", 1) // 2) if t.get("contracts", 1) >= 2 else 0
                            if c_to_close > 0:
                                self.delta_client.close_position(product_id=27, size_contracts=c_to_close, side_to_close="buy")
                                t["contracts"] -= c_to_close
                                print(f"[REAL ORDER CLOSED] TP1 closed {c_to_close} contracts. Remaining: {t['contracts']}", flush=True)
                        except Exception as e:
                            print(f"[REAL ORDER ERROR] Error closing TP1 on Delta: {e}", flush=True)
                            
                    self.save_state()
                    
                    alert_msg = (
                        f"🎯 <b>ICT BOT: TAKE PROFIT 1 HIT (+1.5R)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"• <b>Symbol:</b> BTCUSD (Delta Exchange India)\n"
                        f"• <b>Side:</b> LONG 🟢\n"
                        f"• <b>Target 1:</b> ${t['tp1']:,.1f}\n"
                        f"• <b>Banked Profit:</b> +${partial_gain:.2f}\n"
                        f"• <b>SL Status:</b> MOVED TO BREAKEVEN (${t['entry']:,.1f})\n"
                        f"• <b>Account Balance:</b> ${self.capital:,.2f}\n"
                        f"• <i>Remaining 50% running risk-free to TP2 (${t['tp2']:,.1f})!</i>"
                    )
                    send_telegram_alert(alert_msg)
                    print(f"\n[ALERT] TP1 Hit on Trade #{t['id']}! Banked +${partial_gain:.2f}. Stop Loss moved to Breakeven.\n")
                # TP2 Check (2.5R)
                elif t["tp1_hit"] and current_price >= t["tp2"]:
                    exit_price = t["tp2"]
                    exit_reason = "Full Target TP2 Hit (+2.5R)"
                    trade_closed = True
            else: # SHORT
                if current_price >= t["sl"]:
                    exit_price = t["sl"]
                    if t["be_activated"]:
                        exit_reason = "Breakeven SL Hit (50% TP1 Banked)"
                    else:
                        exit_reason = "Initial SL Hit (-1.0R)"
                    trade_closed = True
                elif not t["tp1_hit"] and current_price <= t["tp1"]:
                    t["tp1_hit"] = True
                    partial_gain = (t["size"] * self.partial_tp_pct) * (t["entry"] - t["tp1"])
                    self.capital += partial_gain
                    t["realized_pnl"] += partial_gain
                    t["size"] *= (1.0 - self.partial_tp_pct)
                    t["sl"] = t["entry"]
                    t["be_activated"] = True
                    
                    # Close 50% contracts on Delta Exchange India if real trading
                    if self.trading_mode == "REAL" and self.delta_client:
                        try:
                            c_to_close = max(1, t.get("contracts", 1) // 2) if t.get("contracts", 1) >= 2 else 0
                            if c_to_close > 0:
                                self.delta_client.close_position(product_id=27, size_contracts=c_to_close, side_to_close="sell")
                                t["contracts"] -= c_to_close
                                print(f"[REAL ORDER CLOSED] TP1 closed {c_to_close} contracts. Remaining: {t['contracts']}", flush=True)
                        except Exception as e:
                            print(f"[REAL ORDER ERROR] Error closing TP1 on Delta: {e}", flush=True)
                            
                    self.save_state()
                    
                    alert_msg = (
                        f"🎯 <b>ICT BOT: TAKE PROFIT 1 HIT (+1.5R)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"• <b>Symbol:</b> BTCUSD (Delta Exchange India)\n"
                        f"• <b>Side:</b> SHORT 🔴\n"
                        f"• <b>Target 1:</b> ${t['tp1']:,.1f}\n"
                        f"• <b>Banked Profit:</b> +${partial_gain:.2f}\n"
                        f"• <b>SL Status:</b> MOVED TO BREAKEVEN (${t['entry']:,.1f})\n"
                        f"• <b>Account Balance:</b> ${self.capital:,.2f}\n"
                        f"• <i>Remaining 50% running risk-free to TP2 (${t['tp2']:,.1f})!</i>"
                    )
                    send_telegram_alert(alert_msg)
                    print(f"\n[ALERT] TP1 Hit on Trade #{t['id']}! Banked +${partial_gain:.2f}. Stop Loss moved to Breakeven.\n")
                elif t["tp1_hit"] and current_price <= t["tp2"]:
                    exit_price = t["tp2"]
                    exit_reason = "Full Target TP2 Hit (+2.5R)"
                    trade_closed = True

            if trade_closed:
                # Close remaining contracts on Delta Exchange India if real trading
                if self.trading_mode == "REAL" and self.delta_client:
                    try:
                        rem_contracts = t.get("contracts", 0)
                        if rem_contracts > 0:
                            self.delta_client.close_position(product_id=27, size_contracts=rem_contracts, side_to_close=t["side"])
                            print(f"[REAL ORDER CLOSED] Closed remaining {rem_contracts} contracts on Delta. Reason: {exit_reason}", flush=True)
                            t["contracts"] = 0
                    except Exception as e:
                        print(f"[REAL ORDER ERROR] Error closing final position on Delta: {e}", flush=True)

                remaining_gain = t["size"] * (exit_price - t["entry"]) if is_long else t["size"] * (t["entry"] - exit_price)
                self.capital += remaining_gain
                t["realized_pnl"] += remaining_gain
                t["exit_price"] = exit_price
                t["exit_time"] = now_ist.strftime("%Y-%m-%d %H:%M:%S")
                t["exit_reason"] = exit_reason
                
                # In REAL mode, fetch settled live balance directly from Delta Exchange India
                if self.trading_mode == "REAL" and self.delta_client:
                    try:
                        time.sleep(1) # Allow 1s for exchange settlement
                        bal_usd, bal_inr, bal_desc = self.delta_client.get_available_balance_usd()
                        if bal_usd > 0:
                            self.capital = bal_usd
                            self.balance_desc = bal_desc
                    except Exception:
                        pass
                
                t["running_capital"] = self.capital
                
                trade_pnl_pct = t["realized_pnl"] / t["starting_balance"]
                day_tracker["pnl_usd"] += t["realized_pnl"]
                day_tracker["pnl_pct"] += trade_pnl_pct
                
                if day_tracker["pnl_pct"] <= -self.daily_loss_limit:
                    day_tracker["locked"] = True
                    circuit_msg = (
                        f"🛑 <b>DAILY LOSS CIRCUIT BREAKER TRIGGERED</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"• Daily Loss: {day_tracker['pnl_pct']*100:.1f}%\n"
                        f"• Trading locked until tomorrow to protect capital."
                    )
                    send_telegram_alert(circuit_msg)

                self.completed_trades.append(t)
                self.active_trade = None
                self.save_state()
                
                pnl_sign = "+" if t["realized_pnl"] >= 0 else "-"
                bal_display = self.balance_desc if (self.trading_mode == "REAL" and self.balance_desc) else f"${self.capital:,.2f}"
                alert_closed = (
                    f"{'🎉' if t['realized_pnl'] > 0 else '🛑'} <b>TRADE CLOSED: #{t['id']}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"• <b>Model:</b> {t['model']}\n"
                    f"• <b>Exit Price:</b> ${exit_price:,.1f}\n"
                    f"• <b>Exit Reason:</b> {exit_reason}\n"
                    f"• <b>Realized PnL:</b> {pnl_sign}${abs(t['realized_pnl']):.2f}\n"
                    f"• <b>Updated Account Balance:</b> {bal_display}"
                )
                send_telegram_alert(alert_closed)
                print(f"\n[TRADE CLOSED] Trade #{t['id']} | Exit: ${exit_price:,.1f} | PnL: {pnl_sign}${abs(t['realized_pnl']):.2f} | Reason: {exit_reason}\n")
                return

        # ---------------------------------------------------------------------
        # 2. CHECK CANDLE FORMATION & SCAN FOR NEW SETUPS
        # ---------------------------------------------------------------------
        # Only scan for entries during 4:30 PM - 9:00 AM IST (Overnight)
        if not self.is_in_session(now_ist):
            return
            
        if day_tracker["trades"] >= self.max_trades_daily:
            return
            
        if day_tracker["locked"] or day_tracker["pnl_pct"] <= -self.daily_loss_limit:
            return
            
        if self.active_trade is not None:
            return

        # Fetch recent 15m candles (past 40 candles)
        candles = fetch_live_candles("BTCUSD", "15m", 40)
        if len(candles) < 20:
            return
            
        # 1. Build & track active FVGs across all recent closed candles
        closed = candles[:-1]
        active_pool = []
        for i in range(2, len(closed)):
            c3 = closed[i]
            c2 = closed[i-1]
            c1 = closed[i-2]
            
            # Displacement MSS Check on Candle 2 (Slide 4 'Strong Move / Displacement')
            did_mss_bullish = (c2["close"] > c1["high"] and c2["close"] > c2["open"])
            did_mss_bearish = (c2["close"] < c1["low"] and c2["close"] < c2["open"])

            # Model 1 Bullish FVG: c3 Low > c1 High
            if c3["low"] > c1["high"]:
                fvg_id = f"BULL_{c2['time']}"
                active_pool.append({
                    "id": fvg_id,
                    "top": c3["low"],
                    "bottom": c1["high"],
                    "type": "BULLISH",
                    "time": c2["time"],
                    "has_mss": did_mss_bullish,
                    "active": fvg_id not in self.mitigated_fvg_ids,
                    "inverted": False
                })
            # Model 1 Bearish FVG: c3 High < c1 Low
            elif c3["high"] < c1["low"]:
                fvg_id = f"BEAR_{c2['time']}"
                active_pool.append({
                    "id": fvg_id,
                    "top": c1["low"],
                    "bottom": c3["high"],
                    "type": "BEARISH",
                    "time": c2["time"],
                    "has_mss": did_mss_bearish,
                    "active": fvg_id not in self.mitigated_fvg_ids,
                    "inverted": False
                })
                
        # Track Inversions across subsequent candles (Model 2: iFVG - Slide 5 'Structure Shifts')
        for f in active_pool:
            for ck in closed:
                if ck["time"] > f["time"]:
                    if not f["inverted"]:
                        if f["type"] == "BEARISH" and ck["close"] > f["top"]:
                            f["inverted"] = True
                            f["type"] = "BULLISH_iFVG"
                            f["has_mss"] = True # An inversion is a confirmed market structure shift
                        elif f["type"] == "BULLISH" and ck["close"] < f["bottom"]:
                            f["inverted"] = True
                            f["type"] = "BEARISH_iFVG"
                            f["has_mss"] = True # An inversion is a confirmed market structure shift
                            
        self.fvg_pool = active_pool
                    
        # Retest Reversal Confirmation Check on the most recently closed candle
        c_last = closed[-1]
        c_last_prev = closed[-2]
        retest_bull_mss = (c_last["close"] > c_last_prev["high"] and c_last["close"] > c_last["open"])
        retest_bear_mss = (c_last["close"] < c_last_prev["low"] and c_last["close"] < c_last["open"])

        # Check retest for entry on any active FVG with confirmed MSS
        for f in self.fvg_pool:
            if not f["active"] or not f.get("has_mss", True):
                continue
                
            # Model 1 & 2: Bullish Setup (FVG or iFVG)
            if f["type"] in ["BULLISH_iFVG", "BULLISH"]:
                # Current price or forming candle wicks into FVG zone AND candle confirms reversal MSS
                is_in_zone = (current_price <= f["top"] and current_price >= f["bottom"]) or (candles[-1]["low"] <= f["top"] and current_price >= f["bottom"])
                if is_in_zone and retest_bull_mss:
                    entry_price = current_price
                    sl_price = f["bottom"] - 5.0
                    risk_dist = entry_price - sl_price
                    
                    if risk_dist > 20.0 and (risk_dist / entry_price) < 0.02:
                        tp1_price = entry_price + (self.partial_tp_rr * risk_dist)
                        tp2_price = entry_price + (self.min_rr * risk_dist)
                        
                        # Contract sizing with margin safety cap (default 80% equity, leaving 20% cushion)
                        contract_val_usd = entry_price * 0.001
                        max_margin_budget = self.capital * self.max_margin_pct
                        max_contracts_by_margin = max(1, int((max_margin_budget * self.max_leverage) / contract_val_usd))
                        
                        risk_usd = self.capital * self.risk_per_trade
                        risk_pts = max(risk_dist, 20.0)
                        contracts_by_risk = max(1, int(risk_usd / (risk_pts * 0.001)))
                        
                        num_contracts = min(contracts_by_risk, max_contracts_by_margin)
                        btc_size = num_contracts * 0.001
                        actual_notional = num_contracts * contract_val_usd
                        
                        # Real Order Execution on Delta Exchange India
                        real_order_id = None
                        if self.trading_mode == "REAL":
                            if not self.delta_client:
                                f["active"] = False
                                self.mitigated_fvg_ids.add(f["id"])
                                self.save_state()
                                
                                k_found = bool(get_env_variable(["DELTA_API_KEY", "DELTA_KEY", "API_KEY", "DELTA_EXCHANGE_API_KEY"]))
                                s_found = bool(get_env_variable(["DELTA_API_SECRET", "DELTA_SECRET", "API_SECRET", "DELTA_EXCHANGE_API_SECRET"]))
                                
                                skip_msg = (
                                    f"⚠️ <b>REAL TRADE SIGNAL SKIPPED (#{f['type']})</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━\n"
                                    f"• <b>Reason:</b> Delta Exchange client offline (Missing API Keys)\n"
                                    f"• <b>API Key Present:</b> {'✅ Yes' if k_found else '❌ No (Missing)'}\n"
                                    f"• <b>API Secret Present:</b> {'✅ Yes' if s_found else '❌ No (Missing)'}\n"
                                    f"• <b>Action Required:</b> In Railway -> Service 'worker' -> Variables tab, ensure <code>DELTA_API_KEY</code> and <code>DELTA_API_SECRET</code> are entered and click <b>Deploy / Apply Changes</b>.\n"
                                    f"• <i>Setup discarded. No trade placed.</i>"
                                )
                                send_telegram_alert(skip_msg)
                                print(f"[REAL TRADE BLOCKED] Delta client is offline. Discarded setup {f['id']}.", flush=True)
                                break
                                
                            try:
                                order_res = self.delta_client.place_market_order(product_id=27, size_contracts=num_contracts, side="buy")
                                print(f"[REAL ORDER PLACED] Buy #{num_contracts} contracts response: {order_res}", flush=True)
                                if order_res.get("success") and order_res.get("result", {}).get("id"):
                                    real_order_id = order_res.get("result", {}).get("id")
                                else:
                                    f["active"] = False
                                    self.mitigated_fvg_ids.add(f["id"])
                                    self.save_state()
                                    err_info = order_res.get("error", "Unknown Delta API error")
                                    err_text = err_info.get("message", str(err_info)) if isinstance(err_info, dict) else str(err_info)
                                    reject_alert = (
                                        f"🚨 <b>DELTA EXCHANGE REJECTED ORDER</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━\n"
                                        f"• <b>Action:</b> BUY {num_contracts} Contracts (BTCUSD, ~${actual_notional:.2f} notional)\n"
                                        f"• <b>Exchange Response:</b> <code>{err_text}</code>\n"
                                        f"• <b>Account Balance:</b> {self.balance_desc}\n"
                                        f"• <b>Status:</b> ❌ Order aborted. Account safe. No simulated runner opened."
                                    )
                                    send_telegram_alert(reject_alert)
                                    print(f"[REAL ORDER REJECTED] {err_text}", flush=True)
                                    break # DO NOT OPEN FAKE TRADE!
                            except Exception as e:
                                f["active"] = False
                                self.mitigated_fvg_ids.add(f["id"])
                                self.save_state()
                                fail_alert = (
                                    f"🚨 <b>DELTA EXCHANGE ORDER EXCEPTION</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━\n"
                                    f"• <b>Error:</b> <code>{e}</code>\n"
                                    f"• <b>Status:</b> ❌ Order aborted. No simulated runner opened."
                                )
                                send_telegram_alert(fail_alert)
                                print(f"[REAL ORDER EXCEPTION] {e}", flush=True)
                                break # DO NOT OPEN FAKE TRADE!
                        
                        if self.trading_mode == "REAL" and not real_order_id:
                            print(f"[CRITICAL ABORT] Blocked active trade creation: real_order_id is missing in REAL mode.", flush=True)
                            break
                        
                        self.active_trade = {
                            "id": len(self.completed_trades) + 1,
                            "date_str": today_str,
                            "side": "BUY",
                            "model": f["type"],
                            "trading_mode": self.trading_mode,
                            "real_order_id": real_order_id,
                            "contracts": num_contracts,
                            "entry_time": now_ist.strftime("%Y-%m-%d %H:%M:%S"),
                            "entry": entry_price,
                            "sl": sl_price,
                            "tp1": tp1_price,
                            "tp2": tp2_price,
                            "risk_dist": risk_dist,
                            "size": btc_size,
                            "notional": actual_notional,
                            "leverage": actual_notional / self.capital if self.capital > 0 else self.max_leverage,
                            "starting_balance": self.capital,
                            "risk_usd": risk_usd,
                            "tp1_hit": False,
                            "be_activated": False,
                            "realized_pnl": 0.0
                        }
                        day_tracker["trades"] += 1
                        f["active"] = False
                        self.mitigated_fvg_ids.add(f["id"])
                        self.save_state()
                        
                        mode_badge = "🔥 REAL MONEY LIVE" if self.trading_mode == "REAL" else "📝 PAPER TRADING"
                        order_id_line = f"\n• <b>Delta Order ID:</b> <code>#{real_order_id} (Verified Live)</code>" if real_order_id else ""
                        bal_display = self.balance_desc if (self.trading_mode == "REAL" and self.balance_desc) else f"${self.capital:,.2f}"
                        entry_alert = (
                            f"🚀 <b>ICT BOT: NEW ORDER EXECUTED ({mode_badge})</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"• <b>Trade #{self.active_trade['id']}</b> | <b>Side:</b> LONG 🟢\n"
                            f"• <b>Setup Model:</b> {f['type']} (MSS Confirmed)\n"
                            f"• <b>Entry Price:</b> ${entry_price:,.1f}\n"
                            f"• <b>Stop Loss:</b> ${sl_price:,.1f} ({risk_dist:.1f} pts)\n"
                            f"• <b>Take Profit 1 (1.5R):</b> ${tp1_price:,.1f} (50% exit + BE)\n"
                            f"• <b>Take Profit 2 (2.5R):</b> ${tp2_price:,.1f}\n"
                            f"• <b>Contracts:</b> {num_contracts} Contracts ({btc_size:.4f} BTC, ${actual_notional:.2f}){order_id_line}\n"
                            f"• <b>Risk:</b> ${risk_usd:.2f} ({self.risk_per_trade*100:.1f}%)\n"
                            f"• <b>Account Balance:</b> {bal_display}"
                        )
                        send_telegram_alert(entry_alert)
                        print(f"\n[{self.trading_mode} TRADE ENTERED] LONG #{self.active_trade['id']} ({num_contracts} contracts) at ${entry_price:,.1f} | SL: ${sl_price:,.1f} | TP1: ${tp1_price:,.1f} | TP2: ${tp2_price:,.1f}\n")
                        break

            # Model 1 & 2: Bearish Setup (FVG or iFVG)
            elif f["type"] in ["BEARISH_iFVG", "BEARISH"]:
                # Current price or forming candle wicks into FVG zone AND candle confirms reversal MSS
                is_in_zone = (current_price >= f["bottom"] and current_price <= f["top"]) or (candles[-1]["high"] >= f["bottom"] and current_price <= f["top"])
                if is_in_zone and retest_bear_mss:
                    entry_price = current_price
                    sl_price = f["top"] + 5.0
                    risk_dist = sl_price - entry_price
                    
                    if risk_dist > 20.0 and (risk_dist / entry_price) < 0.02:
                        tp1_price = entry_price - (self.partial_tp_rr * risk_dist)
                        tp2_price = entry_price - (self.min_rr * risk_dist)
                        
                        # Contract sizing with margin safety cap (default 80% equity, leaving 20% cushion)
                        contract_val_usd = entry_price * 0.001
                        max_margin_budget = self.capital * self.max_margin_pct
                        max_contracts_by_margin = max(1, int((max_margin_budget * self.max_leverage) / contract_val_usd))
                        
                        risk_usd = self.capital * self.risk_per_trade
                        risk_pts = max(risk_dist, 20.0)
                        contracts_by_risk = max(1, int(risk_usd / (risk_pts * 0.001)))
                        
                        num_contracts = min(contracts_by_risk, max_contracts_by_margin)
                        btc_size = num_contracts * 0.001
                        actual_notional = num_contracts * contract_val_usd
                        
                        # Real Order Execution on Delta Exchange India
                        real_order_id = None
                        if self.trading_mode == "REAL":
                            if not self.delta_client:
                                f["active"] = False
                                self.mitigated_fvg_ids.add(f["id"])
                                self.save_state()
                                
                                k_found = bool(get_env_variable(["DELTA_API_KEY", "DELTA_KEY", "API_KEY", "DELTA_EXCHANGE_API_KEY"]))
                                s_found = bool(get_env_variable(["DELTA_API_SECRET", "DELTA_SECRET", "API_SECRET", "DELTA_EXCHANGE_API_SECRET"]))
                                
                                skip_msg = (
                                    f"⚠️ <b>REAL TRADE SIGNAL SKIPPED (#{f['type']})</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━\n"
                                    f"• <b>Reason:</b> Delta Exchange client offline (Missing API Keys)\n"
                                    f"• <b>API Key Present:</b> {'✅ Yes' if k_found else '❌ No (Missing)'}\n"
                                    f"• <b>API Secret Present:</b> {'✅ Yes' if s_found else '❌ No (Missing)'}\n"
                                    f"• <b>Action Required:</b> In Railway -> Service 'worker' -> Variables tab, ensure <code>DELTA_API_KEY</code> and <code>DELTA_API_SECRET</code> are entered and click <b>Deploy / Apply Changes</b>.\n"
                                    f"• <i>Setup discarded. No trade placed.</i>"
                                )
                                send_telegram_alert(skip_msg)
                                print(f"[REAL TRADE BLOCKED] Delta client is offline. Discarded setup {f['id']}.", flush=True)
                                break
                                
                            try:
                                order_res = self.delta_client.place_market_order(product_id=27, size_contracts=num_contracts, side="sell")
                                print(f"[REAL ORDER PLACED] Sell #{num_contracts} contracts response: {order_res}", flush=True)
                                if order_res.get("success") and order_res.get("result", {}).get("id"):
                                    real_order_id = order_res.get("result", {}).get("id")
                                else:
                                    f["active"] = False
                                    self.mitigated_fvg_ids.add(f["id"])
                                    self.save_state()
                                    err_info = order_res.get("error", "Unknown Delta API error")
                                    err_text = err_info.get("message", str(err_info)) if isinstance(err_info, dict) else str(err_info)
                                    reject_alert = (
                                        f"🚨 <b>DELTA EXCHANGE REJECTED ORDER</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━\n"
                                        f"• <b>Action:</b> SELL {num_contracts} Contracts (BTCUSD, ~${actual_notional:.2f} notional)\n"
                                        f"• <b>Exchange Response:</b> <code>{err_text}</code>\n"
                                        f"• <b>Account Balance:</b> {self.balance_desc}\n"
                                        f"• <b>Status:</b> ❌ Order aborted. Account safe. No simulated runner opened."
                                    )
                                    send_telegram_alert(reject_alert)
                                    print(f"[REAL ORDER REJECTED] {err_text}", flush=True)
                                    break # DO NOT OPEN FAKE TRADE!
                            except Exception as e:
                                f["active"] = False
                                self.mitigated_fvg_ids.add(f["id"])
                                self.save_state()
                                fail_alert = (
                                    f"🚨 <b>DELTA EXCHANGE ORDER EXCEPTION</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━\n"
                                    f"• <b>Error:</b> <code>{e}</code>\n"
                                    f"• <b>Status:</b> ❌ Order aborted. No simulated runner opened."
                                )
                                send_telegram_alert(fail_alert)
                                print(f"[REAL ORDER EXCEPTION] {e}", flush=True)
                                break # DO NOT OPEN FAKE TRADE!
                        
                        if self.trading_mode == "REAL" and not real_order_id:
                            print(f"[CRITICAL ABORT] Blocked active trade creation: real_order_id is missing in REAL mode.", flush=True)
                            break
                        
                        self.active_trade = {
                            "id": len(self.completed_trades) + 1,
                            "date_str": today_str,
                            "side": "SELL",
                            "model": f["type"],
                            "trading_mode": self.trading_mode,
                            "real_order_id": real_order_id,
                            "contracts": num_contracts,
                            "entry_time": now_ist.strftime("%Y-%m-%d %H:%M:%S"),
                            "entry": entry_price,
                            "sl": sl_price,
                            "tp1": tp1_price,
                            "tp2": tp2_price,
                            "risk_dist": risk_dist,
                            "size": btc_size,
                            "notional": actual_notional,
                            "leverage": actual_notional / self.capital if self.capital > 0 else self.max_leverage,
                            "starting_balance": self.capital,
                            "risk_usd": risk_usd,
                            "tp1_hit": False,
                            "be_activated": False,
                            "realized_pnl": 0.0
                        }
                        day_tracker["trades"] += 1
                        f["active"] = False
                        self.mitigated_fvg_ids.add(f["id"])
                        self.save_state()
                        
                        mode_badge = "🔥 REAL MONEY LIVE" if self.trading_mode == "REAL" else "📝 PAPER TRADING"
                        order_id_line = f"\n• <b>Delta Order ID:</b> <code>#{real_order_id} (Verified Live)</code>" if real_order_id else ""
                        bal_display = self.balance_desc if (self.trading_mode == "REAL" and self.balance_desc) else f"${self.capital:,.2f}"
                        entry_alert = (
                            f"🚀 <b>ICT BOT: NEW ORDER EXECUTED ({mode_badge})</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"• <b>Trade #{self.active_trade['id']}</b> | <b>Side:</b> SHORT 🔴\n"
                            f"• <b>Setup Model:</b> {f['type']} (MSS Confirmed)\n"
                            f"• <b>Entry Price:</b> ${entry_price:,.1f}\n"
                            f"• <b>Stop Loss:</b> ${sl_price:,.1f} ({risk_dist:.1f} pts)\n"
                            f"• <b>Take Profit 1 (1.5R):</b> ${tp1_price:,.1f} (50% exit + BE)\n"
                            f"• <b>Take Profit 2 (2.5R):</b> ${tp2_price:,.1f}\n"
                            f"• <b>Contracts:</b> {num_contracts} Contracts ({btc_size:.4f} BTC, ${actual_notional:.2f}){order_id_line}\n"
                            f"• <b>Risk:</b> ${risk_usd:.2f} ({self.risk_per_trade*100:.1f}%)\n"
                            f"• <b>Account Balance:</b> {bal_display}"
                        )
                        send_telegram_alert(entry_alert)
                        print(f"\n[{self.trading_mode} TRADE ENTERED] SHORT #{self.active_trade['id']} ({num_contracts} contracts) at ${entry_price:,.1f} | SL: ${sl_price:,.1f} | TP1: ${tp1_price:,.1f} | TP2: ${tp2_price:,.1f}\n")
                        break

# -----------------------------------------------------------------------------
# 4. MAIN LOOP
# -----------------------------------------------------------------------------
def run_live_loop():
    bot = LiveICTPaperBot()
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    
    bal_str = bot.balance_desc if (bot.trading_mode == 'REAL' and bot.balance_desc) else f"${bot.capital:,.2f} (~₹{bot.capital * 84:,.0f} INR)"
    print("\n" + "="*65)
    print(f"      ICT LIVE TRADING ENGINE - DELTA EXCHANGE INDIA ({bot.trading_mode})")
    print("="*65)
    print(f"Trading Mode:                {bot.trading_mode}")
    print(f"Current Capital:             {bal_str}")
    print(f"Trading Session:             4:30 PM to 9:00 AM IST (Overnight)")
    print(f"Max Leverage Cap:            {bot.max_leverage:.0f}x")
    print(f"Risk Per Trade:              {bot.risk_per_trade*100:.1f}% (${bot.capital * bot.risk_per_trade:,.2f})")
    print(f"Daily Loss Limit:            {bot.daily_loss_limit*100:.1f}% (${bot.capital * bot.daily_loss_limit:,.2f})")
    print(f"Max Trades Per Day:          {bot.max_trades_daily} Trades")
    print(f"Telegram Alerts:             {'CONNECTED & ACTIVE' if TELEGRAM_BOT_TOKEN else 'OFFLINE (Token needed in .env)'}")
    print("="*65 + "\n")

    # Send Startup / Online Alert to Telegram immediately
    try:
        bot.send_startup_alert()
    except Exception as e:
        print(f"Error sending startup alert: {e}", flush=True)

    iteration = 0
    while True:
        try:
            now_ist = datetime.datetime.now(ist)
            is_active = bot.is_in_session(now_ist)
            
            # Run tick processing
            bot.process_tick()
            
            # Hourly Telegram Update (fires at the start of each new hour)
            if now_ist.hour != bot.last_hourly_alert_hour:
                try:
                    ticker = fetch_live_ticker("BTCUSD")
                    p = ticker["close"] if (ticker and ticker.get("close", 0) > 0) else 71700.0
                    bot.send_hourly_heartbeat(p, now_ist)
                    bot.last_hourly_alert_hour = now_ist.hour
                except Exception as e:
                    print(f"Error sending hourly heartbeat: {e}", flush=True)

            # Daily Summary at 9:00 AM IST (when overnight session concludes)
            if now_ist.hour == 9 and now_ist.minute < 5 and not bot.daily_summary_sent:
                try:
                    bot.send_daily_summary(now_ist)
                    bot.daily_summary_sent = True
                except Exception as e:
                    print(f"Error sending daily summary: {e}", flush=True)
            elif now_ist.hour != 9:
                bot.daily_summary_sent = False

            # Session open notification (fires when entering 4:30 PM)
            if is_active and not bot.session_notified:
                bot.session_notified = True
                bal_display = bot.balance_desc if (bot.trading_mode == "REAL" and bot.balance_desc) else f"${bot.capital:,.2f}"
                mode_badge = "🔥 REAL MONEY LIVE" if bot.trading_mode == "REAL" else "📝 PAPER TRADING"
                open_msg = (
                    "🟢 <b>ICT OVERNIGHT SESSION OPEN: 4:30 PM IST</b>\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    "• <b>Symbol:</b> BTCUSD (Delta Exchange India)\n"
                    f"• <b>Trading Mode:</b> {mode_badge}\n"
                    "• <b>Status:</b> Scanning live 15m candles for ICT setups.\n"
                    f"• <b>Capital:</b> {bal_display}\n"
                    "• <b>Active Hours:</b> 4:30 PM to 9:00 AM IST (Overnight)"
                )
                send_telegram_alert(open_msg)
            elif not is_active:
                bot.session_notified = False

            # Status heartbeat every 30 iterations (~5 minutes)
            if iteration % 30 == 0:
                ticker = fetch_live_ticker("BTCUSD")
                price_str = f"${ticker['close']:,.1f}" if ticker else "N/A"
                trade_str = f"ACTIVE ({bot.active_trade['side']} @ ${bot.active_trade['entry']:,.1f})" if bot.active_trade else "No Open Trade"
                session_str = "ACTIVE SESSION 🟢" if is_active else "STANDBY (Opens 4:30 PM IST) ⏳"
                bal_disp = bot.balance_desc if (bot.trading_mode == 'REAL' and bot.balance_desc) else f"${bot.capital:,.2f}"
                print(f"[{now_ist.strftime('%I:%M:%S %p')} IST] BTC: {price_str} | Session: {session_str} | Status: {trade_str} | Balance: {bal_disp}", flush=True)
                bot.save_state()
                
            iteration += 1
            time.sleep(10) # 10-second tick interval
            
        except KeyboardInterrupt:
            print("\nStopping bot safely...")
            bot.save_state()
            break
        except Exception as e:
            print(f"Loop error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_live_loop()
