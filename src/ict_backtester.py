import csv
import datetime
import os
import math

class ICTBacktester:
    def __init__(self, data_file_15m, initial_capital=100.0, max_leverage=20.0, 
                 risk_per_trade=0.04, daily_loss_limit=0.08, max_trades_daily=3,
                 min_rr=2.5, partial_tp_rr=1.5, partial_tp_pct=0.5):
        self.data_file_15m = data_file_15m
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.max_leverage = max_leverage
        self.risk_per_trade = risk_per_trade
        self.daily_loss_limit = daily_loss_limit
        self.max_trades_daily = max_trades_daily
        self.min_rr = min_rr
        self.partial_tp_rr = partial_tp_rr
        self.partial_tp_pct = partial_tp_pct
        
        self.candles = []
        self.trades = []
        self.daily_stats = {} # date -> {'trades': 0, 'pnl_pct': 0.0, 'locked': False}
        self.equity_curve = []
        
    def load_data(self):
        print(f"Loading 15m data from {self.data_file_15m}...")
        with open(self.data_file_15m, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # parse timestamp
                t_ist = datetime.datetime.strptime(row["timestamp_ist"], "%Y-%m-%d %H:%M:%S")
                self.candles.append({
                    "time": int(row["time"]),
                    "t_ist": t_ist,
                    "date": t_ist.date(),
                    "hour": t_ist.hour,
                    "minute": t_ist.minute,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "in_session": int(row["in_session"]) == 1
                })
        print(f"Loaded {len(self.candles)} total candles.")

    def run_backtest(self):
        print("\nStarting ICT Phase 1 Backtest Simulation...")
        n = len(self.candles)
        active_trade = None
        
        # Track historical Fair Value Gaps for Inversion
        # list of {'top': float, 'bottom': float, 'type': 'bullish'/'bearish', 'active': bool, 'inverted': bool}
        fvg_pool = []
        
        for i in range(20, n):
            c = self.candles[i]
            c_prev = self.candles[i-1]
            c_prev2 = self.candles[i-2]
            current_date = c["date"]
            
            # Initialize daily tracker
            if current_date not in self.daily_stats:
                self.daily_stats[current_date] = {"trades": 0, "pnl_usd": 0.0, "pnl_pct": 0.0, "locked": False}
                
            day_tracker = self.daily_stats[current_date]
            
            # Record equity
            self.equity_curve.append({
                "time": c["time"],
                "t_ist": c["t_ist"],
                "capital": self.capital,
                "in_trade": active_trade is not None
            })
            
            # -------------------------------------------------------------
            # 1. MANAGE ACTIVE TRADE (IF ANY)
            # -------------------------------------------------------------
            if active_trade is not None:
                trade = active_trade
                is_long = trade["side"] == "BUY"
                
                # Check High/Low of current candle against TP and SL
                high = c["high"]
                low = c["low"]
                
                trade_closed = False
                exit_price = 0.0
                exit_reason = ""
                
                if is_long:
                    # Check SL first (conservative)
                    if low <= trade["sl"]:
                        exit_price = trade["sl"]
                        exit_reason = "SL Hit" if not trade["be_activated"] else "Breakeven SL Hit"
                        trade_closed = True
                    # Check TP1 (Partial Profit + Move to Breakeven)
                    elif not trade["tp1_hit"] and high >= trade["tp1"]:
                        trade["tp1_hit"] = True
                        partial_gain = (trade["size"] * self.partial_tp_pct) * (trade["tp1"] - trade["entry"])
                        self.capital += partial_gain
                        trade["realized_pnl"] += partial_gain
                        trade["size"] *= (1.0 - self.partial_tp_pct) # reduce size
                        trade["sl"] = trade["entry"] # Move SL to Breakeven
                        trade["be_activated"] = True
                    # Check TP2 (Final Profit)
                    elif trade["tp1_hit"] and high >= trade["tp2"]:
                        exit_price = trade["tp2"]
                        exit_reason = "Full TP2 Hit"
                        trade_closed = True
                else: # SHORT
                    if high >= trade["sl"]:
                        exit_price = trade["sl"]
                        exit_reason = "SL Hit" if not trade["be_activated"] else "Breakeven SL Hit"
                        trade_closed = True
                    elif not trade["tp1_hit"] and low <= trade["tp1"]:
                        trade["tp1_hit"] = True
                        partial_gain = (trade["size"] * self.partial_tp_pct) * (trade["entry"] - trade["tp1"])
                        self.capital += partial_gain
                        trade["realized_pnl"] += partial_gain
                        trade["size"] *= (1.0 - self.partial_tp_pct)
                        trade["sl"] = trade["entry"]
                        trade["be_activated"] = True
                    elif trade["tp1_hit"] and low <= trade["tp2"]:
                        exit_price = trade["tp2"]
                        exit_reason = "Full TP2 Hit"
                        trade_closed = True
                        
                if trade_closed:
                    # Calculate final realized PnL
                    remaining_gain = trade["size"] * (exit_price - trade["entry"]) if is_long else trade["size"] * (trade["entry"] - exit_price)
                    self.capital += remaining_gain
                    trade["realized_pnl"] += remaining_gain
                    trade["exit_price"] = exit_price
                    trade["exit_time"] = c["t_ist"]
                    trade["exit_reason"] = exit_reason
                    
                    # Update daily stats
                    trade_pnl_pct = trade["realized_pnl"] / trade["starting_balance"]
                    day_tracker["pnl_usd"] += trade["realized_pnl"]
                    day_tracker["pnl_pct"] += trade_pnl_pct
                    
                    # Check daily circuit breaker
                    if day_tracker["pnl_pct"] <= -self.daily_loss_limit:
                        day_tracker["locked"] = True
                        
                    self.trades.append(trade)
                    active_trade = None
                    continue # Finished managing this trade
                    
            # -------------------------------------------------------------
            # 2. DETECT NEW FVG & INVERSION FVGs
            # -------------------------------------------------------------
            # Bullish FVG: Low[i] > High[i-2]
            if c["low"] > c_prev2["high"]:
                fvg_pool.append({
                    "top": c["low"],
                    "bottom": c_prev2["high"],
                    "type": "BULLISH",
                    "created_idx": i,
                    "active": True,
                    "inverted": False
                })
            # Bearish FVG: High[i] < Low[i-2]
            elif c["high"] < c_prev2["low"]:
                fvg_pool.append({
                    "top": c_prev2["low"],
                    "bottom": c["high"],
                    "type": "BEARISH",
                    "created_idx": i,
                    "active": True,
                    "inverted": False
                })
                
            # Check for Inversions:
            # If a Bearish FVG is closed ABOVE by current candle -> Inverts to Bullish iFVG
            # If a Bullish FVG is closed BELOW by current candle -> Inverts to Bearish iFVG
            for fvg in fvg_pool:
                if fvg["active"] and not fvg["inverted"]:
                    if fvg["type"] == "BEARISH" and c["close"] > fvg["top"]:
                        fvg["inverted"] = True
                        fvg["type"] = "BULLISH_iFVG" # Now acts as support!
                    elif fvg["type"] == "BULLISH" and c["close"] < fvg["bottom"]:
                        fvg["inverted"] = True
                        fvg["type"] = "BEARISH_iFVG" # Now acts as resistance!
                        
            # Keep only active recent FVGs (last 50 candles)
            fvg_pool = [f for f in fvg_pool if (i - f["created_idx"]) <= 50 and f["active"]]
            
            # -------------------------------------------------------------
            # 3. CHECK PERMISSION TO ENTER A NEW TRADE
            # -------------------------------------------------------------
            # Must be in session: 4:30 PM to 11:00 PM IST
            if not c["in_session"]:
                continue
                
            # Must not exceed daily trade limit
            if day_tracker["trades"] >= self.max_trades_daily:
                continue
                
            # Must not be locked by daily loss limit (-8%)
            if day_tracker["locked"] or day_tracker["pnl_pct"] <= -self.daily_loss_limit:
                continue
                
            # Must not have an existing open position
            if active_trade is not None:
                continue

            # -------------------------------------------------------------
            # 4. SCAN FOR ICT SETUPS (SWEEP + MSS + RETEST TO PD ARRAY)
            # -------------------------------------------------------------
            # Calculate local swing highs and lows (last 16 candles = 4 hours)
            lookback = 16
            recent_high = max(x["high"] for x in self.candles[i-lookback:i])
            recent_low = min(x["low"] for x in self.candles[i-lookback:i])
            
            # Average true range / body size
            avg_body = sum(abs(x["close"] - x["open"]) for x in self.candles[i-10:i]) / 10.0
            current_body = abs(c["close"] - c["open"])
            
            # --- SETUP A: BULLISH INVERSION FVG (iFVG) or FVG ---
            # 1. Sweep of recent low happened in last 4 bars
            recent_sweep_low = min(x["low"] for x in self.candles[i-4:i])
            did_sweep_ssl = recent_sweep_low < recent_low
            
            # 2. Displacement up: current or prev bar has strong green body & broke local swing high (MSS)
            did_mss_bullish = (c_prev["close"] > c_prev2["high"] and c_prev["close"] > c_prev["open"])
            
            # 3. Check if price is currently retesting a Bullish iFVG or Bullish FVG zone
            for fvg in fvg_pool:
                if fvg["active"]:
                    # BULLISH SETUP ENTRY
                    if fvg["type"] in ["BULLISH_iFVG", "BULLISH"]:
                        # Price is retesting the zone (low dipped into top or body of gap, and close is bouncing)
                        if c["low"] <= fvg["top"] and c["close"] >= fvg["bottom"] and did_mss_bullish:
                            entry_price = fvg["top"]
                            sl_price = fvg["bottom"] - 5.0 # buffer below zone
                            risk_dist = entry_price - sl_price
                            
                            if risk_dist > 20.0 and (risk_dist / entry_price) < 0.02: # valid risk window
                                tp1_price = entry_price + (self.partial_tp_rr * risk_dist)
                                tp2_price = entry_price + (self.min_rr * risk_dist)
                                
                                # Sizing: Risk exactly 4% ($4)
                                risk_amount = self.capital * self.risk_per_trade
                                position_size_usd = risk_amount / (risk_dist / entry_price)
                                max_allowed_pos = self.capital * self.max_leverage
                                position_size_usd = min(position_size_usd, max_allowed_pos)
                                btc_size = position_size_usd / entry_price
                                
                                active_trade = {
                                    "id": len(self.trades) + 1,
                                    "side": "BUY",
                                    "model": fvg["type"],
                                    "entry_time": c["t_ist"],
                                    "entry": entry_price,
                                    "sl": sl_price,
                                    "tp1": tp1_price,
                                    "tp2": tp2_price,
                                    "size": btc_size,
                                    "notional": position_size_usd,
                                    "starting_balance": self.capital,
                                    "risk_usd": risk_amount,
                                    "tp1_hit": False,
                                    "be_activated": False,
                                    "realized_pnl": 0.0
                                }
                                day_tracker["trades"] += 1
                                fvg["active"] = False # mark used
                                break
                                
                    # BEARISH SETUP ENTRY
                    elif fvg["type"] in ["BEARISH_iFVG", "BEARISH"]:
                        did_mss_bearish = (c_prev["close"] < c_prev2["low"] and c_prev["close"] < c_prev["open"])
                        if c["high"] >= fvg["bottom"] and c["close"] <= fvg["top"] and did_mss_bearish:
                            entry_price = fvg["bottom"]
                            sl_price = fvg["top"] + 5.0
                            risk_dist = sl_price - entry_price
                            
                            if risk_dist > 20.0 and (risk_dist / entry_price) < 0.02:
                                tp1_price = entry_price - (self.partial_tp_rr * risk_dist)
                                tp2_price = entry_price - (self.min_rr * risk_dist)
                                
                                risk_amount = self.capital * self.risk_per_trade
                                position_size_usd = risk_amount / (risk_dist / entry_price)
                                max_allowed_pos = self.capital * self.max_leverage
                                position_size_usd = min(position_size_usd, max_allowed_pos)
                                btc_size = position_size_usd / entry_price
                                
                                active_trade = {
                                    "id": len(self.trades) + 1,
                                    "side": "SELL",
                                    "model": fvg["type"],
                                    "entry_time": c["t_ist"],
                                    "entry": entry_price,
                                    "sl": sl_price,
                                    "tp1": tp1_price,
                                    "tp2": tp2_price,
                                    "size": btc_size,
                                    "notional": position_size_usd,
                                    "starting_balance": self.capital,
                                    "risk_usd": risk_amount,
                                    "tp1_hit": False,
                                    "be_activated": False,
                                    "realized_pnl": 0.0
                                }
                                day_tracker["trades"] += 1
                                fvg["active"] = False
                                break

        print(f"Simulation completed! Total trades executed: {len(self.trades)}")
        return self.trades

    def generate_report(self):
        trades = self.trades
        if not trades:
            print("No trades triggered under current rules.")
            return
            
        wins = [t for t in trades if t["realized_pnl"] > 0]
        losses = [t for t in trades if t["realized_pnl"] < 0]
        breakevens = [t for t in trades if t["realized_pnl"] == 0]
        
        gross_profit = sum(t["realized_pnl"] for t in wins)
        gross_loss = abs(sum(t["realized_pnl"] for t in losses))
        net_profit = self.capital - self.initial_capital
        net_return_pct = (net_profit / self.initial_capital) * 100.0
        
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999.0
        win_rate = (len(wins) / len(trades)) * 100.0
        
        # Max Drawdown calculation
        peak = self.initial_capital
        max_dd_usd = 0.0
        max_dd_pct = 0.0
        for pt in self.equity_curve:
            bal = pt["capital"]
            if bal > peak:
                peak = bal
            dd = peak - bal
            dd_pct = (dd / peak) * 100.0 if peak > 0 else 0.0
            if dd > max_dd_usd:
                max_dd_usd = dd
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
                
        print("\n" + "="*70)
        print("          ICT PHASE 1 STRATEGY - BACKTEST RESULTS REPORT")
        print("                 DELTA EXCHANGE INDIA (BTCUSD)")
        print("="*70)
        print(f"Starting Capital:            ${self.initial_capital:,.2f}")
        print(f"Final Capital:               ${self.capital:,.2f}")
        print(f"Net Profit ($):              +${net_profit:,.2f}" if net_profit >= 0 else f"-${abs(net_profit):,.2f}")
        print(f"Net Return (%):              {net_return_pct:+.2f}%")
        print(f"Profit Factor:               {profit_factor:.2f}")
        print(f"Total Trades Taken:          {len(trades)}")
        print(f"Winning Trades:              {len(wins)} ({win_rate:.1f}%)")
        print(f"Losing Trades:               {len(losses)} ({len(losses)/len(trades)*100:.1f}%)")
        print(f"Breakeven Exits:             {len(breakevens)}")
        print(f"Max Drawdown ($):            ${max_dd_usd:,.2f}")
        print(f"Max Drawdown (%):            {max_dd_pct:.2f}%")
        print(f"Daily Circuit Breaker Cap:   8.0% ($8.00)")
        print(f"Per-Trade Risk Model:        4.0% ($4.00) Capped at 20x Leverage")
        print("="*70)
        
        print("\n--- DETAILED TRADE LOG (SAMPLE OF TRADES) ---")
        for t in trades[:15]:
            side_str = "LONG [BUY]" if t["side"] == "BUY" else "SHORT [SELL]"
            pnl_str = f"+${t['realized_pnl']:,.2f}" if t['realized_pnl'] >= 0 else f"-${abs(t['realized_pnl']):,.2f}"
            print(f"Trade #{t['id']:02d} | {t['entry_time'].strftime('%Y-%m-%d %I:%M %p')} IST | {side_str:12s} | Model: {t['model']:12s} | Entry: ${t['entry']:,.1f} | Exit: ${t['exit_price']:,.1f} | PnL: {pnl_str} | Reason: {t['exit_reason']}")
        print(f"... and {len(trades) - 15} more trades saved to trade log.")
        print("="*70)

        # Export Trade Log to CSV
        log_path = os.path.join(r"c:\Users\Alok Singh\Documents\antigravity\magical-rutherford\data", "ict_trade_log.csv")
        with open(log_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["trade_id", "entry_time_ist", "side", "model", "entry_price", "exit_price", "sl_price", "tp1_price", "tp2_price", "size_btc", "risk_usd", "realized_pnl_usd", "exit_reason", "account_balance"])
            running_bal = self.initial_capital
            for t in trades:
                running_bal += t["realized_pnl"]
                writer.writerow([
                    t["id"],
                    t["entry_time"].strftime("%Y-%m-%d %H:%M:%S"),
                    t["side"],
                    t["model"],
                    t["entry"],
                    t["exit_price"],
                    t["sl"],
                    t["tp1"],
                    t["tp2"],
                    f"{t['size']:.6f}",
                    f"{t['risk_usd']:.2f}",
                    f"{t['realized_pnl']:.2f}",
                    t["exit_reason"],
                    f"{running_bal:.2f}"
                ])
        print(f"Full trade log saved to: {log_path}")

if __name__ == "__main__":
    data_file = r"c:\Users\Alok Singh\Documents\antigravity\magical-rutherford\data\delta_india_btc_15m.csv"
    tester = ICTBacktester(
        data_file_15m=data_file,
        initial_capital=100.0,
        max_leverage=20.0,
        risk_per_trade=0.04,
        daily_loss_limit=0.08,
        max_trades_daily=3,
        min_rr=2.5,
        partial_tp_rr=1.5,
        partial_tp_pct=0.5
    )
    tester.load_data()
    tester.run_backtest()
    tester.generate_report()
