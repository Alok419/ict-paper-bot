import csv
import datetime
import os
import json

class ICTBacktester90D:
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
        self.daily_stats = {}
        self.equity_curve = []
        
    def load_data(self):
        print(f"Loading 90-day 15m data from {self.data_file_15m}...")
        with open(self.data_file_15m, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                t_ist = datetime.datetime.strptime(row["timestamp_ist"], "%Y-%m-%d %H:%M:%S")
                self.candles.append({
                    "time": int(row["time"]),
                    "t_ist": t_ist,
                    "date": t_ist.date(),
                    "date_str": t_ist.strftime("%Y-%m-%d"),
                    "hour": t_ist.hour,
                    "minute": t_ist.minute,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "in_session": int(row["in_session"]) == 1
                })
        print(f"Loaded {len(self.candles)} total 15m candles (covering {self.candles[0]['date_str']} to {self.candles[-1]['date_str']}).")

    def run_backtest(self):
        print("\nSimulating 90-Day ICT Strategy with Detailed Forensic Trade Logging...")
        n = len(self.candles)
        active_trade = None
        fvg_pool = []
        
        for i in range(20, n):
            c = self.candles[i]
            c_prev = self.candles[i-1]
            c_prev2 = self.candles[i-2]
            current_date = c["date"]
            
            if current_date not in self.daily_stats:
                self.daily_stats[current_date] = {
                    "date_str": c["date_str"],
                    "trades": 0,
                    "pnl_usd": 0.0,
                    "pnl_pct": 0.0,
                    "locked": False
                }
            day_tracker = self.daily_stats[current_date]
            
            # Record equity point
            self.equity_curve.append({
                "time": c["time"],
                "t_ist": c["t_ist"],
                "capital": self.capital,
                "in_trade": active_trade is not None
            })
            
            # -------------------------------------------------------------
            # 1. MANAGE ACTIVE TRADE (STEP-BY-STEP PROGRESSION)
            # -------------------------------------------------------------
            if active_trade is not None:
                trade = active_trade
                is_long = trade["side"] == "BUY"
                high = c["high"]
                low = c["low"]
                
                trade_closed = False
                exit_price = 0.0
                exit_reason = ""
                
                if is_long:
                    # Check SL
                    if low <= trade["sl"]:
                        exit_price = trade["sl"]
                        if trade["be_activated"]:
                            exit_reason = "Breakeven SL Hit (50% TP1 Banked)"
                            trade["execution_path"] += f" -> Candle at {c['t_ist'].strftime('%I:%M %p')} tapped Breakeven Stop at ${trade['sl']:,.1f}"
                        else:
                            exit_reason = "Initial SL Hit (-1.0R)"
                            trade["execution_path"] += f" -> Candle at {c['t_ist'].strftime('%I:%M %p')} dropped to Stop Loss at ${trade['sl']:,.1f}"
                        trade_closed = True
                    # Check TP1 (1.5R Partial)
                    elif not trade["tp1_hit"] and high >= trade["tp1"]:
                        trade["tp1_hit"] = True
                        partial_gain = (trade["size"] * self.partial_tp_pct) * (trade["tp1"] - trade["entry"])
                        self.capital += partial_gain
                        trade["realized_pnl"] += partial_gain
                        trade["size"] *= (1.0 - self.partial_tp_pct)
                        trade["sl"] = trade["entry"]
                        trade["be_activated"] = True
                        trade["execution_path"] += f" -> Reached TP1 (${trade['tp1']:,.1f}) at {c['t_ist'].strftime('%I:%M %p')} (+${partial_gain:.2f} banked, SL moved to BE)"
                    # Check TP2 (2.5R Final)
                    elif trade["tp1_hit"] and high >= trade["tp2"]:
                        exit_price = trade["tp2"]
                        exit_reason = "Full Target TP2 Hit (+2.5R)"
                        trade["execution_path"] += f" -> Reached Final TP2 (${trade['tp2']:,.1f}) at {c['t_ist'].strftime('%I:%M %p')}"
                        trade_closed = True
                else: # SHORT
                    if high >= trade["sl"]:
                        exit_price = trade["sl"]
                        if trade["be_activated"]:
                            exit_reason = "Breakeven SL Hit (50% TP1 Banked)"
                            trade["execution_path"] += f" -> Candle at {c['t_ist'].strftime('%I:%M %p')} tapped Breakeven Stop at ${trade['sl']:,.1f}"
                        else:
                            exit_reason = "Initial SL Hit (-1.0R)"
                            trade["execution_path"] += f" -> Candle at {c['t_ist'].strftime('%I:%M %p')} rose to Stop Loss at ${trade['sl']:,.1f}"
                        trade_closed = True
                    elif not trade["tp1_hit"] and low <= trade["tp1"]:
                        trade["tp1_hit"] = True
                        partial_gain = (trade["size"] * self.partial_tp_pct) * (trade["entry"] - trade["tp1"])
                        self.capital += partial_gain
                        trade["realized_pnl"] += partial_gain
                        trade["size"] *= (1.0 - self.partial_tp_pct)
                        trade["sl"] = trade["entry"]
                        trade["be_activated"] = True
                        trade["execution_path"] += f" -> Reached TP1 (${trade['tp1']:,.1f}) at {c['t_ist'].strftime('%I:%M %p')} (+${partial_gain:.2f} banked, SL moved to BE)"
                    elif trade["tp1_hit"] and low <= trade["tp2"]:
                        exit_price = trade["tp2"]
                        exit_reason = "Full Target TP2 Hit (+2.5R)"
                        trade["execution_path"] += f" -> Reached Final TP2 (${trade['tp2']:,.1f}) at {c['t_ist'].strftime('%I:%M %p')}"
                        trade_closed = True
                        
                if trade_closed:
                    remaining_gain = trade["size"] * (exit_price - trade["entry"]) if is_long else trade["size"] * (trade["entry"] - exit_price)
                    self.capital += remaining_gain
                    trade["realized_pnl"] += remaining_gain
                    trade["exit_price"] = exit_price
                    trade["exit_time"] = c["t_ist"]
                    trade["exit_reason"] = exit_reason
                    trade["running_capital"] = self.capital
                    
                    trade_pnl_pct = trade["realized_pnl"] / trade["starting_balance"]
                    day_tracker["pnl_usd"] += trade["realized_pnl"]
                    day_tracker["pnl_pct"] += trade_pnl_pct
                    
                    if day_tracker["pnl_pct"] <= -self.daily_loss_limit:
                        day_tracker["locked"] = True
                        
                    self.trades.append(trade)
                    active_trade = None
                    continue

            # -------------------------------------------------------------
            # 2. FVG & INVERSION TRACKING
            # -------------------------------------------------------------
            if c["low"] > c_prev2["high"]:
                fvg_pool.append({
                    "top": c["low"],
                    "bottom": c_prev2["high"],
                    "type": "BULLISH",
                    "created_idx": i,
                    "created_time": c["t_ist"],
                    "active": True,
                    "inverted": False
                })
            elif c["high"] < c_prev2["low"]:
                fvg_pool.append({
                    "top": c_prev2["low"],
                    "bottom": c["high"],
                    "type": "BEARISH",
                    "created_idx": i,
                    "created_time": c["t_ist"],
                    "active": True,
                    "inverted": False
                })
                
            for fvg in fvg_pool:
                if fvg["active"] and not fvg["inverted"]:
                    if fvg["type"] == "BEARISH" and c["close"] > fvg["top"]:
                        fvg["inverted"] = True
                        fvg["type"] = "BULLISH_iFVG"
                        fvg["inversion_time"] = c["t_ist"]
                    elif fvg["type"] == "BULLISH" and c["close"] < fvg["bottom"]:
                        fvg["inverted"] = True
                        fvg["type"] = "BEARISH_iFVG"
                        fvg["inversion_time"] = c["t_ist"]
                        
            fvg_pool = [f for f in fvg_pool if (i - f["created_idx"]) <= 50 and f["active"]]
            
            # -------------------------------------------------------------
            # 3. TRADING PERMISSIONS (4:30 PM - 11:00 PM IST, <= 3 trades, > -8%)
            # -------------------------------------------------------------
            if not c["in_session"]:
                continue
            if day_tracker["trades"] >= self.max_trades_daily:
                continue
            if day_tracker["locked"] or day_tracker["pnl_pct"] <= -self.daily_loss_limit:
                continue
            if active_trade is not None:
                continue

            # -------------------------------------------------------------
            # 4. SETUP SCANNER & DETAILED METADATA COMPILATION
            # -------------------------------------------------------------
            lookback = 16
            recent_high = max(x["high"] for x in self.candles[i-lookback:i])
            recent_low = min(x["low"] for x in self.candles[i-lookback:i])
            
            for fvg in fvg_pool:
                if fvg["active"]:
                    # --- BULLISH SETUP (iFVG / FVG) ---
                    if fvg["type"] in ["BULLISH_iFVG", "BULLISH"]:
                        did_mss_bullish = (c_prev["close"] > c_prev2["high"] and c_prev["close"] > c_prev["open"])
                        if c["low"] <= fvg["top"] and c["close"] >= fvg["bottom"] and did_mss_bullish:
                            entry_price = fvg["top"]
                            sl_price = fvg["bottom"] - 5.0
                            risk_dist = entry_price - sl_price
                            
                            if risk_dist > 20.0 and (risk_dist / entry_price) < 0.02:
                                tp1_price = entry_price + (self.partial_tp_rr * risk_dist)
                                tp2_price = entry_price + (self.min_rr * risk_dist)
                                
                                risk_amount = self.capital * self.risk_per_trade
                                position_size_usd = risk_amount / (risk_dist / entry_price)
                                max_allowed_pos = self.capital * self.max_leverage
                                position_size_usd = min(position_size_usd, max_allowed_pos)
                                btc_size = position_size_usd / entry_price
                                lev_used = position_size_usd / self.capital
                                
                                # Forensic trade descriptions
                                if fvg["type"] == "BULLISH_iFVG":
                                    context_desc = f"Bearish FVG between ${fvg['bottom']:,.1f}-${fvg['top']:,.1f} was blown through by strong bullish displacement at {fvg['inversion_time'].strftime('%I:%M %p')}, breaking market structure upward (MSS)."
                                    entry_desc = f"Price retraced into the top boundary of the newly inverted Bullish iFVG at ${entry_price:,.1f}. Trapped sellers exit, triggering explosive upward momentum."
                                else:
                                    context_desc = f"Bullish displacement leg formed a fresh Fair Value Gap imbalance between ${fvg['bottom']:,.1f} and ${fvg['top']:,.1f}."
                                    entry_desc = f"Price retraced into the upper edge of the Bullish FVG at ${entry_price:,.1f} during the active NY/London session."
                                    
                                sl_desc = f"Stop Loss set at ${sl_price:,.1f} (5 pts below the lower boundary of ${fvg['bottom']:,.1f}). Invalidation level: candle close below gap cancels institutional thesis."
                                tp1_desc = f"TP1 set at ${tp1_price:,.1f} (+1.5R). Rule: close 50% position and move Stop Loss to Breakeven (${entry_price:,.1f}) to eliminate remaining risk."
                                tp2_desc = f"TP2 set at ${tp2_price:,.1f} (+2.5R). Targets opposing Buy-Side Liquidity resting above local highs."
                                
                                active_trade = {
                                    "id": len(self.trades) + 1,
                                    "date_str": c["date_str"],
                                    "side": "BUY",
                                    "model": fvg["type"],
                                    "entry_time": c["t_ist"],
                                    "entry": entry_price,
                                    "sl": sl_price,
                                    "tp1": tp1_price,
                                    "tp2": tp2_price,
                                    "risk_dist": risk_dist,
                                    "size": btc_size,
                                    "notional": position_size_usd,
                                    "leverage": lev_used,
                                    "starting_balance": self.capital,
                                    "risk_usd": risk_amount,
                                    "daily_trade_num": day_tracker["trades"] + 1,
                                    "context_desc": context_desc,
                                    "entry_desc": entry_desc,
                                    "sl_desc": sl_desc,
                                    "tp1_desc": tp1_desc,
                                    "tp2_desc": tp2_desc,
                                    "execution_path": f"Entered LONG at ${entry_price:,.1f} ({c['t_ist'].strftime('%I:%M %p')})",
                                    "tp1_hit": False,
                                    "be_activated": False,
                                    "realized_pnl": 0.0
                                }
                                day_tracker["trades"] += 1
                                fvg["active"] = False
                                break

                    # --- BEARISH SETUP (iFVG / FVG) ---
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
                                lev_used = position_size_usd / self.capital
                                
                                if fvg["type"] == "BEARISH_iFVG":
                                    context_desc = f"Bullish FVG between ${fvg['bottom']:,.1f}-${fvg['top']:,.1f} was broken downward by strong displacement at {fvg['inversion_time'].strftime('%I:%M %p')}, shifting market structure bearish."
                                    entry_desc = f"Price pulled back to retest the bottom boundary of the failed gap at ${entry_price:,.1f}. Old support is now confirmed resistance."
                                else:
                                    context_desc = f"Bearish displacement leg created an unfilled Fair Value Gap between ${fvg['bottom']:,.1f} and ${fvg['top']:,.1f}."
                                    entry_desc = f"Price retraced into the lower edge of the Bearish FVG at ${entry_price:,.1f} during session hours."
                                    
                                sl_desc = f"Stop Loss placed at ${sl_price:,.1f} (5 pts above the upper boundary of ${fvg['top']:,.1f}). Invalidation level: candle close back above the gap cancels short setup."
                                tp1_desc = f"TP1 set at ${tp1_price:,.1f} (+1.5R). Rule: close 50% position and move Stop Loss to Breakeven (${entry_price:,.1f}) to eliminate remaining risk."
                                tp2_desc = f"TP2 set at ${tp2_price:,.1f} (+2.5R). Targets Sell-Side Liquidity resting below recent swing lows."
                                
                                active_trade = {
                                    "id": len(self.trades) + 1,
                                    "date_str": c["date_str"],
                                    "side": "SELL",
                                    "model": fvg["type"],
                                    "entry_time": c["t_ist"],
                                    "entry": entry_price,
                                    "sl": sl_price,
                                    "tp1": tp1_price,
                                    "tp2": tp2_price,
                                    "risk_dist": risk_dist,
                                    "size": btc_size,
                                    "notional": position_size_usd,
                                    "leverage": lev_used,
                                    "starting_balance": self.capital,
                                    "risk_usd": risk_amount,
                                    "daily_trade_num": day_tracker["trades"] + 1,
                                    "context_desc": context_desc,
                                    "entry_desc": entry_desc,
                                    "sl_desc": sl_desc,
                                    "tp1_desc": tp1_desc,
                                    "tp2_desc": tp2_desc,
                                    "execution_path": f"Entered SHORT at ${entry_price:,.1f} ({c['t_ist'].strftime('%I:%M %p')})",
                                    "tp1_hit": False,
                                    "be_activated": False,
                                    "realized_pnl": 0.0
                                }
                                day_tracker["trades"] += 1
                                fvg["active"] = False
                                break

        print(f"90-Day Simulation completed! Total trades executed: {len(self.trades)}")
        return self.trades

    def export_full_trade_log(self, out_csv_path):
        with open(out_csv_path, mode="w", newline="", encoding="utf-8") as f:
            fieldnames = [
                "trade_id", "date_str", "entry_time_ist", "exit_time_ist", "side", "model",
                "entry_price", "exit_price", "sl_price", "tp1_price", "tp2_price",
                "risk_dist_pts", "risk_usd", "position_size_btc", "notional_usd", "leverage_used",
                "tp1_hit", "be_activated", "realized_pnl_usd", "exit_reason", "running_capital",
                "daily_trade_num", "context_desc", "entry_desc", "sl_desc", "tp1_desc", "tp2_desc", "execution_path"
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for t in self.trades:
                writer.writerow({
                    "trade_id": t["id"],
                    "date_str": t["date_str"],
                    "entry_time_ist": t["entry_time"].strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_time_ist": t["exit_time"].strftime("%Y-%m-%d %H:%M:%S"),
                    "side": t["side"],
                    "model": t["model"],
                    "entry_price": f"{t['entry']:.1f}",
                    "exit_price": f"{t['exit_price']:.1f}",
                    "sl_price": f"{t['sl']:.1f}",
                    "tp1_price": f"{t['tp1']:.1f}",
                    "tp2_price": f"{t['tp2']:.1f}",
                    "risk_dist_pts": f"{t['risk_dist']:.1f}",
                    "risk_usd": f"{t['risk_usd']:.2f}",
                    "position_size_btc": f"{t['size']:.6f}",
                    "notional_usd": f"{t['notional']:.2f}",
                    "leverage_used": f"{t['leverage']:.1f}x",
                    "tp1_hit": t["tp1_hit"],
                    "be_activated": t["be_activated"],
                    "realized_pnl_usd": f"{t['realized_pnl']:.2f}",
                    "exit_reason": t["exit_reason"],
                    "running_capital": f"{t['running_capital']:.2f}",
                    "daily_trade_num": t["daily_trade_num"],
                    "context_desc": t["context_desc"],
                    "entry_desc": t["entry_desc"],
                    "sl_desc": t["sl_desc"],
                    "tp1_desc": t["tp1_desc"],
                    "tp2_desc": t["tp2_desc"],
                    "execution_path": t["execution_path"]
                })
        print(f"Exported detailed 90-day forensic log to: {out_csv_path}")

    def generate_report(self):
        trades = self.trades
        total_trades = len(trades)
        wins = [t for t in trades if t["realized_pnl"] > 0]
        losses = [t for t in trades if t["realized_pnl"] < 0]
        breakevens = [t for t in trades if t["realized_pnl"] == 0]
        
        gross_profit = sum(t["realized_pnl"] for t in wins)
        gross_loss = abs(sum(t["realized_pnl"] for t in losses))
        net_profit = self.capital - self.initial_capital
        net_return_pct = (net_profit / self.initial_capital) * 100.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999.0
        win_rate = (len(wins) / total_trades) * 100.0 if total_trades else 0
        
        # Max Drawdown
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
                
        print("\n" + "="*75)
        print("          ICT 90-DAY (3-MONTH) STRATEGY BACKTEST RESULTS")
        print("                 DELTA EXCHANGE INDIA (BTCUSD)")
        print("="*75)
        print(f"Duration:                    90 Full Days (Aug 14, 2026 - Nov 12, 2026)")
        print(f"Starting Capital:            ${self.initial_capital:,.2f}")
        print(f"Final Capital:               ${self.capital:,.2f}")
        print(f"Net Profit ($):              +${net_profit:,.2f}" if net_profit >= 0 else f"-${abs(net_profit):,.2f}")
        print(f"Net Return (%):              {net_return_pct:+.2f}%")
        print(f"Profit Factor:               {profit_factor:.2f}")
        print(f"Total Trades Taken:          {total_trades}")
        print(f"Winning Trades:              {len(wins)} ({win_rate:.1f}%)")
        print(f"Losing Trades:               {len(losses)} ({len(losses)/total_trades*100:.1f}%)")
        print(f"Breakeven Exits:             {len(breakevens)}")
        print(f"Max Drawdown ($):            ${max_dd_usd:,.2f}")
        print(f"Max Drawdown (%):            {max_dd_pct:.2f}%")
        print(f"Daily Loss Circuit Breaker:  8.0% ($8.00)")
        print(f"Per-Trade Risk Model:        4.0% ($4.00) Capped at 20x Leverage")
        print("="*75)
        
        # Monthly breakdowns
        month1 = [t for t in trades if "2026-08-14" <= t["date_str"] <= "2026-09-13"]
        month2 = [t for t in trades if "2026-09-14" <= t["date_str"] <= "2026-10-13"]
        month3 = [t for t in trades if "2026-10-14" <= t["date_str"] <= "2026-11-12"]
        
        for name, m_trades in [("Month 1 (Aug 14 - Sep 13)", month1),
                               ("Month 2 (Sep 14 - Oct 13)", month2),
                               ("Month 3 (Oct 14 - Nov 12)", month3)]:
            m_pnl = sum(t["realized_pnl"] for t in m_trades)
            m_wins = len([t for t in m_trades if t["realized_pnl"] > 0])
            m_wr = (m_wins / len(m_trades) * 100) if m_trades else 0
            print(f"  {name:26s} | Trades: {len(m_trades):2d} | Win Rate: {m_wr:5.1f}% | Net PnL: {'+' if m_pnl>=0 else '-'}${abs(m_pnl):,.2f}")
        print("="*75)

if __name__ == "__main__":
    data_file = r"c:\Users\Alok Singh\Documents\antigravity\magical-rutherford\data\delta_india_btc_15m_90d.csv"
    tester = ICTBacktester90D(
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
    
    out_csv = r"c:\Users\Alok Singh\Documents\antigravity\magical-rutherford\data\ict_trade_log_90d.csv"
    tester.export_full_trade_log(out_csv)
