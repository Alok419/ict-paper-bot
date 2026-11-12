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

os.makedirs(DATA_DIR, exist_ok=True)

DELTA_BASE_URL = "https://api.india.delta.exchange/v2"

# -----------------------------------------------------------------------------
# 1. ENVIRONMENT & TELEGRAM ALERT MODULE
# -----------------------------------------------------------------------------
def load_env():
    env = {}
    # 1. Read from OS Environment (Cloud Platforms: Railway, Render, Docker)
    for k in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]:
        if k in os.environ and os.environ[k]:
            env[k] = os.environ[k].strip().strip('"').strip("'")
            
    # 2. Fallback to local .env file
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, mode="r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k_clean = k.strip()
                    if k_clean not in env:
                        env[k_clean] = v.strip().strip('"').strip("'")
    return env

ENV_VARS = load_env()
TELEGRAM_BOT_TOKEN = ENV_VARS.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = ENV_VARS.get("TELEGRAM_CHAT_ID", "")

def send_telegram_alert(message_html):
    env_vars = load_env()
    token = env_vars.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env_vars.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("[Telegram Alert Disabled: Token or Chat ID not configured in .env]", flush=True)
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
    def __init__(self, initial_capital=100.0, max_leverage=20.0,
                 risk_per_trade=0.04, daily_loss_limit=0.08, max_trades_daily=3,
                 min_rr=2.5, partial_tp_rr=1.5, partial_tp_pct=0.5):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.max_leverage = max_leverage
        self.risk_per_trade = risk_per_trade
        self.daily_loss_limit = daily_loss_limit
        self.max_trades_daily = max_trades_daily
        self.min_rr = min_rr
        self.partial_tp_rr = partial_tp_rr
        self.partial_tp_pct = partial_tp_pct
        
        self.active_trade = None
        self.daily_stats = {}
        self.completed_trades = []
        self.fvg_pool = []
        self.last_candle_time = 0
        self.session_notified = False
        self.daily_summary_sent = False
        self.last_hourly_alert_hour = -1
        
        self.load_state()
        self.save_state()

    def send_hourly_heartbeat(self, current_price, now_ist):
        is_active = self.is_in_session(now_ist)
        session_text = "🟢 ACTIVE SESSION (Trading)" if is_active else "⏳ STANDBY (Next session: 4:30 PM IST)"
        today_str = now_ist.strftime("%Y-%m-%d")
        day_stats = self.daily_stats.get(today_str, {"trades": 0, "pnl_usd": 0.0, "pnl_pct": 0.0})
        
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
            f"⏱ <b>1-HOUR ICT BOT UPDATE</b> ({now_ist.strftime('%I:%M %p')} IST)\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>BTC Live Price:</b> ${current_price:,.1f}\n"
            f"• <b>Session Status:</b> {session_text}\n"
            f"• <b>Account Balance:</b> ${self.capital:,.2f}\n"
            f"• <b>Today's Realized PnL:</b> {pnl_day_sign}${abs(day_stats['pnl_usd']):.2f} ({day_stats['pnl_pct']*100:+.1f}%)\n"
            f"• <b>Trades Taken Today:</b> {day_stats['trades']} of {self.max_trades_daily} max\n"
            f"• <b>Active Position:</b> {trade_status_text}"
        )
        send_telegram_alert(msg)

    def send_daily_summary(self, now_ist):
        today_str = now_ist.strftime("%Y-%m-%d")
        day_stats = self.daily_stats.get(today_str, {"trades": 0, "pnl_usd": 0.0, "pnl_pct": 0.0})
        today_trades = [t for t in self.completed_trades if t.get("date_str") == today_str]
        wins = len([t for t in today_trades if t.get("realized_pnl", 0) > 0])
        losses = len([t for t in today_trades if t.get("realized_pnl", 0) < 0])
        
        pnl_sign = "+" if day_stats["pnl_usd"] >= 0 else "-"
        total_growth = ((self.capital - self.initial_capital) / self.initial_capital) * 100.0
        
        msg = (
            f"🌙 <b>ICT BOT: DAILY SESSION SUMMARY (11:55 PM IST)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Date:</b> {today_str}\n"
            f"• <b>Today's Trades:</b> {day_stats['trades']} taken ({wins} Wins / {losses} Losses)\n"
            f"• <b>Today's Realized PnL:</b> {pnl_sign}${abs(day_stats['pnl_usd']):.2f} ({day_stats['pnl_pct']*100:+.1f}%)\n"
            f"• <b>Closing Account Balance:</b> ${self.capital:,.2f}\n"
            f"• <b>Total Net Return (All Time):</b> {total_growth:+.1f}%\n"
            f"• <b>Active Runners:</b> {'Running to SL/TP' if self.active_trade else 'None'}\n"
            f"• <i>Session closed. New trade entries will resume tomorrow at 4:30 PM IST.</i>"
        )
        send_telegram_alert(msg)

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, mode="r", encoding="utf-8") as f:
                    st = json.load(f)
                    self.capital = st.get("capital", self.initial_capital)
                    self.active_trade = st.get("active_trade", None)
                    self.daily_stats = st.get("daily_stats", {})
                    self.completed_trades = st.get("completed_trades", [])
                    print(f"[State Loaded] Capital: ${self.capital:,.2f} | Active Trade: {'YES' if self.active_trade else 'None'}")
            except Exception as e:
                print(f"Error loading state: {e}")

    def save_state(self):
        st = {
            "capital": self.capital,
            "active_trade": self.active_trade,
            "daily_stats": self.daily_stats,
            "completed_trades": self.completed_trades,
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(STATE_FILE, mode="w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)

    def is_in_session(self, now_ist=None):
        if now_ist is None:
            ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
            now_ist = datetime.datetime.now(ist)
        h = now_ist.hour
        m = now_ist.minute
        # 4:30 PM (16:30) to 11:55 PM (23:55) IST
        return ((h == 16 and m >= 30) or (17 <= h < 23) or (h == 23 and m < 55))

    def process_tick(self):
        ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now_ist = datetime.datetime.now(ist)
        today_str = now_ist.strftime("%Y-%m-%d")
        
        if today_str not in self.daily_stats:
            self.daily_stats[today_str] = {
                "trades": 0, "pnl_usd": 0.0, "pnl_pct": 0.0, "locked": False
            }
        day_tracker = self.daily_stats[today_str]

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
                remaining_gain = t["size"] * (exit_price - t["entry"]) if is_long else t["size"] * (t["entry"] - exit_price)
                self.capital += remaining_gain
                t["realized_pnl"] += remaining_gain
                t["exit_price"] = exit_price
                t["exit_time"] = now_ist.strftime("%Y-%m-%d %H:%M:%S")
                t["exit_reason"] = exit_reason
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
                alert_closed = (
                    f"{'🎉' if t['realized_pnl'] > 0 else '🛑'} <b>TRADE CLOSED: #{t['id']}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"• <b>Model:</b> {t['model']}\n"
                    f"• <b>Exit Price:</b> ${exit_price:,.1f}\n"
                    f"• <b>Exit Reason:</b> {exit_reason}\n"
                    f"• <b>Realized PnL:</b> {pnl_sign}${abs(t['realized_pnl']):.2f}\n"
                    f"• <b>Updated Account Balance:</b> ${self.capital:,.2f}"
                )
                send_telegram_alert(alert_closed)
                print(f"\n[TRADE CLOSED] Trade #{t['id']} | Exit: ${exit_price:,.1f} | PnL: {pnl_sign}${abs(t['realized_pnl']):.2f} | Reason: {exit_reason}\n")
                return

        # ---------------------------------------------------------------------
        # 2. CHECK CANDLE FORMATION & SCAN FOR NEW SETUPS
        # ---------------------------------------------------------------------
        # Only scan for entries during 4:30 PM - 11:55 PM IST
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
            c = closed[i]
            c_mid = closed[i-1]
            c_prev2 = closed[i-2]
            
            # Bullish FVG: c Low > c_prev2 High
            if c["low"] > c_prev2["high"]:
                active_pool.append({
                    "top": c["low"],
                    "bottom": c_prev2["high"],
                    "type": "BULLISH",
                    "time": c_mid["time"],
                    "active": True,
                    "inverted": False
                })
            # Bearish FVG: c High < c_prev2 Low
            elif c["high"] < c_prev2["low"]:
                active_pool.append({
                    "top": c_prev2["low"],
                    "bottom": c["high"],
                    "type": "BEARISH",
                    "time": c_mid["time"],
                    "active": True,
                    "inverted": False
                })
                
        # Track Inversions across subsequent candles
        for f in active_pool:
            for c in closed:
                if c["time"] > f["time"]:
                    if not f["inverted"]:
                        if f["type"] == "BEARISH" and c["close"] > f["top"]:
                            f["inverted"] = True
                            f["type"] = "BULLISH_iFVG"
                        elif f["type"] == "BULLISH" and c["close"] < f["bottom"]:
                            f["inverted"] = True
                            f["type"] = "BEARISH_iFVG"
                            
        self.fvg_pool = active_pool
                    
        # Check retest for entry
        for f in self.fvg_pool[-10:]:
            if f["active"]:
                # Bullish setup
                if f["type"] in ["BULLISH_iFVG", "BULLISH"]:
                    if current_price <= f["top"] and current_price >= f["bottom"]:
                        entry_price = current_price
                        sl_price = f["bottom"] - 5.0
                        risk_dist = entry_price - sl_price
                        
                        if risk_dist > 20.0 and (risk_dist / entry_price) < 0.02:
                            tp1_price = entry_price + (self.partial_tp_rr * risk_dist)
                            tp2_price = entry_price + (self.min_rr * risk_dist)
                            
                            risk_usd = self.capital * self.risk_per_trade
                            notional_usd = min(risk_usd / (risk_dist / entry_price), self.capital * self.max_leverage)
                            btc_size = notional_usd / entry_price
                            
                            self.active_trade = {
                                "id": len(self.completed_trades) + 1,
                                "date_str": today_str,
                                "side": "BUY",
                                "model": f["type"],
                                "entry_time": now_ist.strftime("%Y-%m-%d %H:%M:%S"),
                                "entry": entry_price,
                                "sl": sl_price,
                                "tp1": tp1_price,
                                "tp2": tp2_price,
                                "risk_dist": risk_dist,
                                "size": btc_size,
                                "notional": notional_usd,
                                "leverage": notional_usd / self.capital,
                                "starting_balance": self.capital,
                                "risk_usd": risk_usd,
                                "tp1_hit": False,
                                "be_activated": False,
                                "realized_pnl": 0.0
                            }
                            day_tracker["trades"] += 1
                            f["active"] = False
                            self.save_state()
                            
                            entry_alert = (
                                f"🚀 <b>ICT BOT: NEW LIVE TRADE ENTERED</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━\n"
                                f"• <b>Trade #{self.active_trade['id']}</b> | <b>Side:</b> LONG 🟢\n"
                                f"• <b>Setup Model:</b> {f['type']}\n"
                                f"• <b>Entry Price:</b> ${entry_price:,.1f}\n"
                                f"• <b>Stop Loss:</b> ${sl_price:,.1f} ({risk_dist:.1f} pts)\n"
                                f"• <b>Take Profit 1 (1.5R):</b> ${tp1_price:,.1f} (50% exit + BE)\n"
                                f"• <b>Take Profit 2 (2.5R):</b> ${tp2_price:,.1f}\n"
                                f"• <b>Position Size:</b> {btc_size:.6f} BTC (${notional_usd:.2f})\n"
                                f"• <b>Risk:</b> ${risk_usd:.2f} (4.0%)\n"
                                f"• <b>Account Balance:</b> ${self.capital:,.2f}"
                            )
                            send_telegram_alert(entry_alert)
                            print(f"\n[LIVE TRADE ENTERED] LONG #{self.active_trade['id']} at ${entry_price:,.1f} | SL: ${sl_price:,.1f} | TP1: ${tp1_price:,.1f} | TP2: ${tp2_price:,.1f}\n")
                            break

                # Bearish setup
                elif f["type"] in ["BEARISH_iFVG", "BEARISH"]:
                    if current_price >= f["bottom"] and current_price <= f["top"]:
                        entry_price = current_price
                        sl_price = f["top"] + 5.0
                        risk_dist = sl_price - entry_price
                        
                        if risk_dist > 20.0 and (risk_dist / entry_price) < 0.02:
                            tp1_price = entry_price - (self.partial_tp_rr * risk_dist)
                            tp2_price = entry_price - (self.min_rr * risk_dist)
                            
                            risk_usd = self.capital * self.risk_per_trade
                            notional_usd = min(risk_usd / (risk_dist / entry_price), self.capital * self.max_leverage)
                            btc_size = notional_usd / entry_price
                            
                            self.active_trade = {
                                "id": len(self.completed_trades) + 1,
                                "date_str": today_str,
                                "side": "SELL",
                                "model": f["type"],
                                "entry_time": now_ist.strftime("%Y-%m-%d %H:%M:%S"),
                                "entry": entry_price,
                                "sl": sl_price,
                                "tp1": tp1_price,
                                "tp2": tp2_price,
                                "risk_dist": risk_dist,
                                "size": btc_size,
                                "notional": notional_usd,
                                "leverage": notional_usd / self.capital,
                                "starting_balance": self.capital,
                                "risk_usd": risk_usd,
                                "tp1_hit": False,
                                "be_activated": False,
                                "realized_pnl": 0.0
                            }
                            day_tracker["trades"] += 1
                            f["active"] = False
                            self.save_state()
                            
                            entry_alert = (
                                f"🚀 <b>ICT BOT: NEW LIVE TRADE ENTERED</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━\n"
                                f"• <b>Trade #{self.active_trade['id']}</b> | <b>Side:</b> SHORT 🔴\n"
                                f"• <b>Setup Model:</b> {f['type']}\n"
                                f"• <b>Entry Price:</b> ${entry_price:,.1f}\n"
                                f"• <b>Stop Loss:</b> ${sl_price:,.1f} ({risk_dist:.1f} pts)\n"
                                f"• <b>Take Profit 1 (1.5R):</b> ${tp1_price:,.1f} (50% exit + BE)\n"
                                f"• <b>Take Profit 2 (2.5R):</b> ${tp2_price:,.1f}\n"
                                f"• <b>Position Size:</b> {btc_size:.6f} BTC (${notional_usd:.2f})\n"
                                f"• <b>Risk:</b> ${risk_usd:.2f} (4.0%)\n"
                                f"• <b>Account Balance:</b> ${self.capital:,.2f}"
                            )
                            send_telegram_alert(entry_alert)
                            print(f"\n[LIVE TRADE ENTERED] SHORT #{self.active_trade['id']} at ${entry_price:,.1f} | SL: ${sl_price:,.1f} | TP1: ${tp1_price:,.1f} | TP2: ${tp2_price:,.1f}\n")
                            break

# -----------------------------------------------------------------------------
# 4. MAIN LOOP
# -----------------------------------------------------------------------------
def run_live_loop():
    bot = LiveICTPaperBot()
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    
    print("\n" + "="*65)
    print("      ICT LIVE PAPER TRADING BOT - DELTA EXCHANGE INDIA")
    print("="*65)
    print(f"Current Capital:             ${bot.capital:,.2f}")
    print(f"Trading Session:             4:30 PM to 11:55 PM IST (Daily)")
    print(f"Risk Per Trade:              4.0% (${bot.capital * 0.04:,.2f})")
    print(f"Daily Loss Limit:            8.0% (${bot.capital * 0.08:,.2f})")
    print(f"Max Trades Per Day:          3 Trades")
    print(f"Telegram Alerts:             {'CONNECTED & ACTIVE' if TELEGRAM_BOT_TOKEN else 'OFFLINE (Token needed in .env)'}")
    print("="*65 + "\n")

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

            # Daily Summary at 11:55 PM IST (23:55)
            if now_ist.hour == 23 and now_ist.minute >= 55 and not bot.daily_summary_sent:
                try:
                    bot.send_daily_summary(now_ist)
                    bot.daily_summary_sent = True
                except Exception as e:
                    print(f"Error sending daily summary: {e}", flush=True)
            elif now_ist.hour != 23:
                bot.daily_summary_sent = False

            # Session open notification
            if is_active and not bot.session_notified:
                bot.session_notified = True
                open_msg = (
                    "🟢 <b>ICT SESSION OPEN: 4:30 PM IST</b>\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    "• <b>Symbol:</b> BTCUSD (Delta Exchange India)\n"
                    "• <b>Status:</b> Scanning live 15m candles for ICT setups.\n"
                    "• <b>Capital:</b> ${:,.2f}\n"
                    "• <b>Active Hours:</b> 4:30 PM - 11:55 PM IST".format(bot.capital)
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
                print(f"[{now_ist.strftime('%I:%M:%S %p')} IST] BTC: {price_str} | Session: {session_str} | Status: {trade_str} | Balance: ${bot.capital:,.2f}", flush=True)
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
