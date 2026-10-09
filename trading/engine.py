import time
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable, Tuple
from config import settings
from feed import market_feed, OptionChainSnapshot, evaluate_market_state
from analyzer import ai_analyzer
from notifier import telegram_notifier
from event_filter import EventRiskFilter
from session_filter import session_time_filter
from concordance import heavyweight_tracker
from options_pricing import BlackScholesEngine, portfolio_risk_engine
from database import (
    save_signal, update_signal_status, create_trade, close_trade,
    get_recent_signals, get_recent_trades, get_accuracy_metrics, get_daily_net_pnl
)

logger = logging.getLogger("TradingEngine")
logger.setLevel(logging.INFO)

class TradingOrchestrator:
    def __init__(self):
        self.last_signal_time: float = 0.0
        self.cooldown_seconds: int = settings.MIN_COOLDOWN_SECONDS
        self.active_signals: List[Dict[str, Any]] = []
        self.active_trades: List[Dict[str, Any]] = []
        self.signal_subscribers: List[Callable[[Dict[str, Any]], Any]] = []
        self._is_evaluating = False
        self.current_gate_status: str = "MONITORING_LEVELS"
        self.last_signal_payload: Optional[Dict[str, Any]] = None
        try:
            recent = get_recent_signals(1)
            if recent:
                sig = recent[0]
                import json as _json
                confs = []
                try:
                    confs = _json.loads(sig.get("confirmations", "[]"))
                except Exception:
                    confs = ["Price Holding Above VWAP Support", "EMA 9 > 21 Bullish Dynamic Support"]
                self.last_signal_payload = {
                    "action": sig["bias"],
                    "strike": sig["strike"],
                    "lots": 1,
                    "entry": sig["spot_price"],
                    "sl": sig["stop_loss"],
                    "target": sig["target"],
                    "quality_score": sig["quality_score"] or 88,
                    "grade": sig["grade"] or "A",
                    "latency_ms": 1.2,
                    "invalidation_level": sig["invalidation_level"] or sig["stop_loss"],
                    "expires_at": sig.get("expires_at") or (time.time() + 480),
                    "confirmations": confs,
                    "reasoning": sig.get("reasoning", "")
                }
        except Exception:
            pass
        self.consecutive_losses: int = 0
        self.is_circuit_breaker_active: bool = False
        self.daily_signals_count: int = 0
        self.strike_cooldowns: Dict[str, float] = {}
        self.event_filter = EventRiskFilter()
        self.session_filter = session_time_filter
        self.daily_realized_pnl: float = 0.0

    def check_circuit_breaker(self) -> bool:
        """Returns True if trading is paused due to consecutive losses."""
        if self.consecutive_losses >= settings.MAX_DAILY_CONSECUTIVE_LOSSES:
            self.is_circuit_breaker_active = True
            self.current_gate_status = f"CIRCUIT_BREAKER_LOCKED_({self.consecutive_losses}_LOSSES)"
            return True
        return False

    def subscribe_signals(self, callback: Callable[[Dict[str, Any]], Any]):
        if callback not in self.signal_subscribers:
            self.signal_subscribers.append(callback)

    def unsubscribe_signals(self, callback: Callable[[Dict[str, Any]], Any]):
        if callback in self.signal_subscribers:
            self.signal_subscribers.remove(callback)

    def build_scalper_link(self, symbol: str, strike: str, bias: str) -> str:
        clean_strike = strike.replace(" ", "%20")
        option_type = "CE" if "CE" in bias or "CE" in strike else "PE"
        return f"groww://options/scalper?symbol={symbol}&strike={clean_strike}&type={option_type}"

    async def on_tick(self, tick_data: Dict[str, Any]):
        """Runs the Multi-Layer Signal & Decision Support Cascade on every 1-second tick."""
        spot_price = tick_data["price"]
        now = time.time()

        # Monitor active open positions & signals for invalidation / expiration
        await self._monitor_active_trades_and_signals(spot_price, tick_data)

        # =====================================================================
        # 🛑 HARD VETO / NO-TRADE KILL-SWITCH ENGINE
        # =====================================================================
        # 1. Circuit Breaker Check
        if self.check_circuit_breaker():
            return

        # 2. Daily rupee loss hard stop
        self.daily_realized_pnl = get_daily_net_pnl()
        if self.daily_realized_pnl <= -settings.MAX_DAILY_RUPEE_LOSS:
            self.is_circuit_breaker_active = True
            self.current_gate_status = f"HARD_VETO_DAILY_LOSS_(₹{abs(self.daily_realized_pnl):.2f})"
            return

        # 2a. Multi-Tier Portfolio Risk State Machine (GREEN -> YELLOW -> ORANGE -> RED)
        portfolio_state = portfolio_risk_engine.evaluate_portfolio_state(
            open_positions=self.active_trades,
            daily_pnl=self.daily_realized_pnl,
            max_daily_loss=settings.MAX_DAILY_RUPEE_LOSS,
            consecutive_losses=self.consecutive_losses
        )
        if not portfolio_state["is_trade_permitted"]:
            self.current_gate_status = f"PORTFOLIO_KILL_SWITCH_{portfolio_state['kill_state']}_({portfolio_state['action']})"
            return

        # 3. Daily Maximum Signals Cap
        if self.daily_signals_count >= settings.MAX_SIGNALS_PER_DAY:
            self.current_gate_status = f"HARD_VETO_DAILY_CAP_({self.daily_signals_count}/{settings.MAX_SIGNALS_PER_DAY})"
            return

        # 3. Feed Health & Stale Tick Hard Veto (> 3.0 seconds lag)
        if not tick_data.get("is_feed_healthy", True):
            self.current_gate_status = "HARD_VETO_FEED_DISCONNECTED"
            return

        tick_time = tick_data.get("timestamp_raw", now)
        if (now - tick_time) > settings.MAX_TICK_STALENESS_SECONDS:
            self.current_gate_status = f"HARD_VETO_STALE_TICK_({round(now - tick_time, 1)}s)"
            return

        # 4. Scheduled event hard veto before option/AI scoring.
        event_risk = self.event_filter.evaluate()
        if not event_risk.allow_trading:
            self.current_gate_status = f"HARD_VETO_EVENT_RISK_({event_risk.event_name})"
            return

        # 4a. Intraday Session Time-of-Day Gating & Midday Chop Filter (Phase 1)
        session_state = self.session_filter.evaluate_session()
        if not session_state.allow_trading:
            self.current_gate_status = session_state.veto_reason or "HARD_VETO_SESSION_LOCKOUT"
            return

        # 5. Snapshot Option Chain & Strike Concentration Walls
        oi_snapshot = OptionChainSnapshot.get_snapshot(spot_price, symbol=settings.SYMBOL)

        # 5a. Zero-Synthetic Guard in Live Mode
        if oi_snapshot.get("is_valid") is False:
            self.current_gate_status = "HARD_VETO_NO_LIVE_DATA"
            return

        # 5b. Refresh Live Top-5 Heavyweight Concordance (Phase 3)
        if settings.MARKET_DATA_MODE == "UPSTOX":
            try:
                heavyweight_tracker.update_from_upstox()
            except Exception as e:
                logger.debug(f"Non-blocking heavyweight update failed: {e}")

        # 5b. Spread Blowout Hard Veto
        spread_pct = oi_snapshot.get("spread_pct", 0.25)
        if spread_pct > settings.MAX_ALLOWED_SPREAD_PCT:
            self.current_gate_status = f"HARD_VETO_SPREAD_BLOWOUT_({spread_pct}% > {settings.MAX_ALLOWED_SPREAD_PCT}%)"
            return

        # 6. Evaluate Market Structure & Technical Confluence
        ready_for_scoring, gate_status, setup_payload = evaluate_market_state(tick_data, oi_snapshot)
        self.current_gate_status = gate_status

        if not ready_for_scoring:
            return

        # 7. Global & Strike Debounce
        strike_name = setup_payload.get("strike", "")
        if (now - self.last_signal_time) < settings.MIN_COOLDOWN_SECONDS:
            rem = int(settings.MIN_COOLDOWN_SECONDS - (now - self.last_signal_time))
            self.current_gate_status = f"COOLDOWN_ACTIVE_({rem}S)"
            return

        last_alert_time = self.strike_cooldowns.get(strike_name, 0.0)
        ema9 = tick_data.get("ema_9", spot_price)
        
        if setup_payload.get("direction") == "CE" and spot_price < ema9:
            self.strike_cooldowns.pop(strike_name, None)
        elif setup_payload.get("direction") == "PE" and spot_price > ema9:
            self.strike_cooldowns.pop(strike_name, None)
        elif (now - last_alert_time) < 300: # 5 min debounce
            self.current_gate_status = f"DEBOUNCED_({int(300 - (now - last_alert_time))}S)"
            return

        if self._is_evaluating:
            return

        self._is_evaluating = True
        try:
            # Zero-Latency Local Scoring (<5ms)
            ai_result = await ai_analyzer.analyze_stage_4(setup_payload, oi_snapshot)

            bias = ai_result.get("bias", "NO_TRADE")
            quality = ai_result.get("quality_score", 0)
            grade = ai_result.get("grade", "NO_SIGNAL")

            # Dynamic Session Execution Threshold (elevated to >= 88 during 11:30-13:30 Midday Chop Zone)
            effective_min_score = session_state.required_min_score

            if bias in ["BUY_CE", "BUY_PE"] and quality >= effective_min_score:
                # 8. True Risk-Budget Position Sizing Gate (Based on Option Premium SL Points)
                option_sl_pts = ai_result.get("prem_sl_pts") or settings.DEFAULT_STOP_LOSS_PTS
                allowed_lots, risk_budget, is_risk_ok = self.calculate_position_sizing(option_sl_pts)
                
                if not is_risk_ok:
                    self.current_gate_status = f"HARD_VETO_RISK_BUDGET_EXCEEDED_(MaxRisk ₹{risk_budget:.0f})"
                    logger.warning(f"Trade rejected: Minimum 1 lot risk exceeds ₹{risk_budget:.0f} budget.")
                    return

                self.last_signal_time = now
                self.daily_signals_count += 1
                self.strike_cooldowns[strike_name] = now
                self.current_gate_status = f"SIGNAL_{bias}_({quality}PTS_GRADE_{grade}_{allowed_lots}LOTS_{session_state.session_name})"
                
                # Immediate Dispatch
                await self._process_valid_signal(tick_data, oi_snapshot, ai_result, lots=allowed_lots)

                # Decoupled Asynchronous Gemini Background Audit (Does not block tick loop)
                asyncio.create_task(ai_analyzer.update_regime_classifier(tick_data, oi_snapshot))
            else:
                if session_state.is_chop_zone:
                    self.current_gate_status = f"CHOP_ZONE_VETO_({quality}<{effective_min_score})"
                else:
                    self.current_gate_status = f"LOW_QUALITY_OR_CHOP_({quality}/{effective_min_score})"
                logger.info(f"Signal skipped: Score {quality}/{effective_min_score} [Grade {grade} in {session_state.session_name}]. Reasoning: {ai_result.get('reasoning')}")
        finally:
            self._is_evaluating = False

    def calculate_position_sizing(self, sl_pts: float) -> Tuple[int, float, bool]:
        """
        Calculates position sizing based on strict account equity risk budget.
        STRICT INVARIANT: Never allows a trade whose worst-case modeled loss exceeds the risk budget.
        No 0.5x exception — if 1 lot exceeds risk budget, the trade is REJECTED.
        """
        equity = settings.ACCOUNT_EQUITY
        risk_pct = settings.MAX_RISK_PER_TRADE_PCT / 100.0
        risk_budget = equity * risk_pct
        lot_size = settings.LOT_SIZE
        # Worst-case loss per lot = SL points * lot size (option premium loss)
        cost_per_lot_sl = max(1.0, sl_pts * lot_size)
        allowed_lots = int(risk_budget / cost_per_lot_sl)
        # STRICT: No exception. If 1 lot exceeds risk budget, reject.
        is_allowed = (allowed_lots >= 1)
        return allowed_lots, risk_budget, is_allowed

    async def _process_valid_signal(self, tick_data: Dict[str, Any], oi_snapshot: Dict[str, Any], ai_result: Dict[str, Any], lots: int = 1):
        spot_price = tick_data["price"]
        bias = ai_result["bias"]
        # Phase 2: Volatility-Adaptive (ATR-Dynamic) Stop-Loss & Target Engine
        atr = float(tick_data.get("atr") or 12.0)
        dynamic_sl_pts = round(max(settings.MIN_SL_FLOOR_PTS, 1.8 * atr), 1)
        dynamic_target_pts = round(dynamic_sl_pts * settings.RISK_REWARD_RATIO, 1)

        sl_pts = ai_result.get("sl_pts") or dynamic_sl_pts
        target_pts = ai_result.get("target_pts") or dynamic_target_pts
        confidence = ai_result.get("confidence_pct", 85.0)
        quality_score = ai_result.get("quality_score", int(confidence))
        grade = ai_result.get("grade", "A")
        reasoning = ai_result.get("reasoning", "")
        invalidation_lvl = ai_result.get("invalidation_level", spot_price - sl_pts if bias == "BUY_CE" else spot_price + sl_pts)
        expires_at = ai_result.get("expires_at", time.time() + 480)
        strike = ai_result.get("strike") or f"{settings.SYMBOL} {int(round(spot_price / 50) * 50)} {'CE' if 'CE' in bias else 'PE'}"
        scalper_link = self.build_scalper_link(settings.SYMBOL, strike, bias)

        entry_price = ai_result.get("entry_price", spot_price)
        target_price = ai_result.get("target", spot_price + target_pts if bias == "BUY_CE" else spot_price - target_pts)
        sl_price = ai_result.get("stop_loss", spot_price - sl_pts if bias == "BUY_CE" else spot_price + sl_pts)

        # Execution Latency Auditor
        tick_time = tick_data.get("timestamp_raw", time.time())
        latency_ms = round(max(1.0, (time.time() - tick_time) * 1000.0), 1)

        signal_payload = {
            "timestamp": datetime.now().isoformat(),
            "symbol": settings.SYMBOL,
            "spot_price": spot_price,
            "day_high": tick_data["day_high"],
            "day_low": tick_data["day_low"],
            "pdh": tick_data.get("pdh"),
            "pdl": tick_data.get("pdl"),
            "orh": tick_data.get("orh"),
            "orl": tick_data.get("orl"),
            "bias": bias,
            "strike": strike,
            "lots": lots,
            "entry_price": entry_price,
            "stop_loss": sl_price,
            "target": target_price,
            "sl_pts": sl_pts,
            "target_pts": target_pts,
            "confidence_pct": confidence,
            "quality_score": quality_score,
            "grade": grade,
            "calibrated_expectancy": ai_result.get("calibrated_expectancy", {}),
            "invalidation_level": invalidation_lvl,
            "expires_at": expires_at,
            "execution_telemetry": {
                "latency_ms": latency_ms,
                "order_type": "LIMIT_CHASE",
                "is_latency_compliant": latency_ms <= settings.MAX_EXECUTION_LATENCY_MS
            },
            "breakdown": ai_result.get("breakdown", {}),
            "confirmations": ai_result.get("confirmations", []),
            "warnings": ai_result.get("warnings", []),
            "session_regime": ai_result.get("session_regime", "STRONG_TREND_BULLISH"),
            "estimated_pnl_pts": target_pts,
            "reasoning": reasoning,
            "oi_context": {
                "pcr": oi_snapshot.get("pcr", 1.0),
                "atm_call_change_oi": oi_snapshot.get("atm_call_change_oi", 0),
                "atm_put_change_oi": oi_snapshot.get("atm_put_change_oi", 0),
                "max_call_wall": oi_snapshot.get("max_call_wall"),
                "max_put_wall": oi_snapshot.get("max_put_wall"),
                "atm_iv": oi_snapshot.get("atm_iv", 13.5),
                "iv_regime": oi_snapshot.get("iv_regime", "NORMAL_IV")
            },
            "status": "ACTIVE",
            "is_dry_run": settings.DRY_RUN,
            "scalper_link": scalper_link
        }

        signal_id = save_signal(signal_payload)
        signal_payload["id"] = signal_id

        # Update last signal for telemetry
        self.last_signal_payload = {
            "action": bias,
            "strike": strike,
            "lots": lots,
            "entry": entry_price,
            "sl": sl_price,
            "target": target_price,
            "quality_score": quality_score,
            "grade": grade,
            "latency_ms": latency_ms,
            "invalidation_level": invalidation_lvl,
            "expires_at": expires_at,
            "confirmations": ai_result.get("confirmations", [])
        }

        telegram_notifier.send_trade_alert(signal_payload)

        if settings.DRY_RUN:
            # Phase 4: Delta-Adjusted Option Pricing & Premium Scaling
            delta = float(ai_result.get("delta") or 0.50)
            if delta <= 0.05 or delta >= 0.95:
                delta = 0.50

            # Option premium estimated from AI result or Black-Scholes fallback
            option_entry_premium = ai_result.get("option_entry_premium", None)
            if option_entry_premium is None:
                strike_val = float(strike.split()[-2]) if any(c.isdigit() for c in strike) else spot_price
                bs = BlackScholesEngine.calculate_greeks(
                    spot=spot_price, strike=strike_val, time_to_expiry_days=1.0,
                    iv_pct=13.5, option_type="CE" if "CE" in bias else "PE"
                )
                option_entry_premium = round(bs["theoretical_price"], 2)
                delta = abs(bs.get("delta", delta))

            # Option premium SL and Target scaled dynamically by Delta
            prem_sl_pts = round(max(6.0, sl_pts * delta), 1)
            prem_target_pts = round(prem_sl_pts * settings.RISK_REWARD_RATIO, 1)
            option_sl_premium = round(max(5.0, option_entry_premium - prem_sl_pts), 2)
            option_target_premium = round(option_entry_premium + prem_target_pts, 2)

            trade_id = create_trade(
                signal_id=signal_id,
                strike=strike,
                action=bias,
                entry_price=option_entry_premium,        # Option PREMIUM - critical fix
                stop_loss=option_sl_premium,
                target=option_target_premium,
                is_paper=True,
                lots=lots,
                underlying_entry=spot_price,             # NIFTY index value
                underlying_sl=sl_price,
                underlying_target=target_price,
                sl_pts=prem_sl_pts,
                target_pts=prem_target_pts
            )
            self.active_trades.append({
                "trade_id": trade_id,
                "signal_id": signal_id,
                "bias": bias,
                "strike": strike,
                "lots": lots,
                "option_entry": option_entry_premium,
                "option_target": option_target_premium,
                "option_sl": option_sl_premium,
                "underlying_entry": spot_price,
                "underlying_target": target_price,
                "underlying_sl": sl_price,
                "prem_sl_pts": round(option_entry_premium - option_sl_premium, 2),
                "prem_target_pts": round(option_target_premium - option_entry_premium, 2),
                "sl_pts": sl_pts,
                "target_pts": target_pts,
                "latency_ms": latency_ms,
                "order_type": "LIMIT_CHASE",
                "entry_time": time.time(),
                "trailing_stop_option": option_sl_premium
            })
        else:
            # LIVE EXECUTION MODE: Place entry order with automated broker-side SL protection
            from groww_client import live_client
            try:
                clean_sym = strike.replace(" ", "")
                total_qty = lots * settings.LOT_SIZE
                order_res = live_client.place_order(
                    trading_symbol=clean_sym,
                    quantity=total_qty,
                    side="BUY",
                    order_type="LIMIT",
                    price=entry_price
                )
                order_id = order_res.get("groww_order_id") or order_res.get("order_id")
                if order_id:
                    sl_res = live_client.place_protective_stop_loss(
                        trading_symbol=clean_sym,
                        quantity=total_qty,
                        stop_loss_trigger_price=sl_price
                    )
                    logger.info(f"🛡️ Broker-side hardware Stop-Loss order placed: {sl_res}")
            except Exception as e:
                logger.error(f"Failed to dispatch live broker order with protective SL: {e}")

        self.active_signals.append(signal_payload)

        for sub in self.signal_subscribers:
            try:
                if asyncio.iscoroutinefunction(sub):
                    await sub(signal_payload)
                else:
                    sub(signal_payload)
            except Exception as e:
                logger.error(f"Error sending signal to subscriber: {e}")

    async def _monitor_active_trades_and_signals(self, current_spot: float, tick_data: Dict[str, Any]):
        """Monitors open positions, checks trailing breakeven, and resolves expirations / invalidations."""
        now = time.time()

        # 1. Monitor Active Signals for Expiration & Invalidation
        expired_signals = []
        for sig in self.active_signals:
            sig_id = sig.get("id")
            bias = sig.get("bias")
            invalidation = sig.get("invalidation_level", 0.0)
            expires_at = sig.get("expires_at", now + 999)

            if now >= expires_at:
                sig["status"] = "EXPIRED"
                if sig_id:
                    update_signal_status(sig_id, "EXPIRED")
                expired_signals.append(sig)
                logger.info(f"⏳ Signal #{sig_id} ({sig['strike']}) EXPIRED after 8-minute validity.")
            elif bias == "BUY_CE" and current_spot < invalidation:
                sig["status"] = "INVALIDATED"
                if sig_id:
                    update_signal_status(sig_id, "INVALIDATED")
                expired_signals.append(sig)
                logger.info(f"🛡️ Signal #{sig_id} ({sig['strike']}) INVALIDATED: Spot fell below ₹{invalidation}")
            elif bias == "BUY_PE" and current_spot > invalidation:
                sig["status"] = "INVALIDATED"
                if sig_id:
                    update_signal_status(sig_id, "INVALIDATED")
                expired_signals.append(sig)
                logger.info(f"🛡️ Signal #{sig_id} ({sig['strike']}) INVALIDATED: Spot rose above ₹{invalidation}")

        for s in expired_signals:
            if s in self.active_signals:
                self.active_signals.remove(s)

        # 2. Monitor Paper Trades (for Net Expectancy & Metric Tracking)
        to_remove = []
        for trade in self.active_trades:
            trade_id = trade["trade_id"]
            signal_id = trade["signal_id"]
            bias = trade["bias"]
            opt_entry = trade["option_entry"]
            opt_target = trade["option_target"]
            opt_sl = trade["option_sl"]
            und_entry = trade["underlying_entry"]
            und_target = trade["underlying_target"]
            und_sl = trade["underlying_sl"]
            is_be = trade.get("is_breakeven", False)
            ema9 = tick_data.get("ema_9", current_spot)
            outcome = None
            exit_price = opt_entry

            entry_time = trade.get("entry_time", now)
            hold_minutes = (now - entry_time) / 60.0

            if hold_minutes >= settings.MAX_TRADE_HOLD_MINUTES:
                outcome = "TIME_EXPIRED_EXIT"
                spot_diff = (current_spot - und_entry) if bias == "BUY_CE" else (und_entry - current_spot)
                exit_price = max(1.0, round(opt_entry + spot_diff * 0.5, 2))
            else:
                # 1. Check Partial Booking & Breakeven Trigger (at 50% target progress)
                if not is_be:
                    if bias == "BUY_CE" and (current_spot - und_entry) >= (0.5 * trade["target_pts"]):
                        trade["trailing_stop_option"] = opt_entry
                        trade["is_breakeven"] = True
                        trade["partial_booked"] = True
                        logger.info(f"🛡️ Trade #{trade_id} ({trade['strike']}) moved to BREAKEVEN (Cost SL: {opt_entry})")
                    elif bias == "BUY_PE" and (und_entry - current_spot) >= (0.5 * trade["target_pts"]):
                        trade["trailing_stop_option"] = opt_entry
                        trade["is_breakeven"] = True
                        trade["partial_booked"] = True
                        logger.info(f"🛡️ Trade #{trade_id} ({trade['strike']}) moved to BREAKEVEN (Cost SL: {opt_entry})")

            if not outcome:
                if bias == "BUY_CE":
                    if current_spot >= und_target:
                        outcome = "TARGET_HIT"
                        exit_price = opt_target
                    elif current_spot <= und_sl or (trade.get("is_breakeven") and current_spot <= und_entry):
                        outcome = "BREAKEVEN_EXIT" if trade.get("is_breakeven") else "SL_HIT"
                        exit_price = opt_entry if trade.get("is_breakeven") else opt_sl
                elif bias == "BUY_PE":
                    if current_spot <= und_target:
                        outcome = "TARGET_HIT"
                        exit_price = opt_target
                    elif current_spot >= und_sl or (trade.get("is_breakeven") and current_spot >= und_entry):
                        outcome = "BREAKEVEN_EXIT" if trade.get("is_breakeven") else "SL_HIT"
                        exit_price = opt_entry if trade.get("is_breakeven") else opt_sl

            if outcome:
                # close_trade is executed below and returns realized net PnL
                if signal_id:
                    update_signal_status(signal_id, outcome)

                realized = close_trade(trade_id, exit_price=exit_price, outcome=outcome)
                self.daily_realized_pnl += float(realized or 0.0)
                if outcome == "SL_HIT":
                    self.consecutive_losses += 1
                elif outcome in ["TARGET_HIT", "BREAKEVEN_EXIT", "TRAILING_SL_EXIT"]:
                    self.consecutive_losses = 0
                    self.is_circuit_breaker_active = False

                to_remove.append(trade)

        for item in to_remove:
            self.active_trades.remove(item)

orchestrator = TradingOrchestrator()
market_feed.subscribe(orchestrator.on_tick)
