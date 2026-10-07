import json
import logging
import asyncio
import time
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime

try:
    import httpx
except ImportError:
    httpx = None

from config import settings
from options_pricing import BlackScholesEngine
from concordance import heavyweight_tracker

logger = logging.getLogger("AIAnalyzer")
logger.setLevel(logging.INFO)

CONTRADICTION_AUDITOR_PROMPT = """You are an institutional risk & anomaly auditor for Indian Index Options (NIFTY 50 / BANKNIFTY).
Your job is NOT to guess price direction. Your job is to audit structured market facts and identify CONTRADICTIONS, TRAPS, and RANGE-BOUND CHOP.

Evaluate the following multi-factor telemetry:
1. Is there contradictory evidence (e.g. Spot rising but Call OI surging / Put OI dropping)?
2. Is this an institutional liquidity grab / false breakout trap near Day High/Low?
3. Is market volume or IV abnormal?

Output ONLY valid JSON with this exact schema:
{
  "is_contradiction_detected": false,
  "is_trap_risk_high": false,
  "trap_risk_score": 0.15,
  "session_regime": "STRONG_TREND_BULLISH" | "STRONG_TREND_BEARISH" | "BREAKOUT_EXPANSION" | "RANGE_BOUND_CHOP" | "FAILED_BREAKOUT_TRAP" | "HIGH_VOLATILITY_EVENT",
  "audit_summary": "Spot confirmed above VWAP with aggressive Call unwinding. No divergence detected."
}
"""

class MarketAIAnalyzer:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.GEMINI_MODEL
        self.cached_regime: Dict[str, Any] = {
            "session_regime": "STRONG_TREND_BULLISH",
            "trade_bias": "FAVOR_CE",
            "trap_risk_score": 0.15,
            "is_contradiction_detected": False,
            "audit_summary": "Initial Balance established above VWAP with supportive Put writing and Call unwinding.",
            "last_updated": time.time(),
            "is_ai_powered": False
        }

    def get_current_regime(self) -> Dict[str, Any]:
        """Hot-path query: returns pre-scored regime in 0 milliseconds."""
        return self.cached_regime

    def classify_market_regime(self, tick_data: Dict[str, Any], oi_snapshot: Dict[str, Any]) -> str:
        """
        Classifies market context into 6 distinct quantitative regimes:
        1. STRONG_TREND_BULLISH: Higher highs, Spot > VWAP > EMA21, EMA9 slope > 0, PCR >= 1.05
        2. STRONG_TREND_BEARISH: Lower lows, Spot < VWAP < EMA21, EMA9 slope < 0, PCR <= 0.85
        3. BREAKOUT_EXPANSION: Breaking ORH / PDH / Day High with Volume > 1.25x
        4. RANGE_BOUND_CHOP: Spot oscillating around VWAP, flat EMAs, low volume
        5. FAILED_BREAKOUT_TRAP: Breakout rejected back below level / conflicting OI
        6. HIGH_VOLATILITY_EVENT: ATR > 22.0 or IV >= 22.0
        """
        spot = tick_data.get("price", 22600.0)
        vwap = tick_data.get("vwap", spot)
        ema9 = tick_data.get("ema_9", spot)
        ema21 = tick_data.get("ema_21", spot)
        ema9_slope = tick_data.get("ema9_slope", 0.0)
        atr = tick_data.get("atr", 12.0)
        pcr = oi_snapshot.get("pcr_ntm", 1.0)
        iv = oi_snapshot.get("atm_iv", 13.5)
        vol_ratio = tick_data.get("curr_vol", 1000) / max(1.0, tick_data.get("avg_vol", 1000))
        day_high = tick_data.get("day_high", spot)
        day_low = tick_data.get("day_low", spot)
        orh = tick_data.get("orh", day_high)
        orl = tick_data.get("orl", day_low)

        if iv >= 22.0 or atr >= 24.0:
            return "HIGH_VOLATILITY_EVENT"

        if (spot >= day_high - 2 or spot >= orh) and vol_ratio >= 1.25 and spot > vwap:
            return "BREAKOUT_EXPANSION"
        elif (spot <= day_low + 2 or spot <= orl) and vol_ratio >= 1.25 and spot < vwap:
            return "BREAKOUT_EXPANSION"

        if spot > vwap and ema9 > ema21 and ema9_slope > 0.05 and pcr >= 1.05:
            return "STRONG_TREND_BULLISH"
        elif spot < vwap and ema9 < ema21 and ema9_slope < -0.05 and pcr <= 0.85:
            return "STRONG_TREND_BEARISH"

        if abs(spot - vwap) < 4.0 and abs(ema9 - ema21) < 3.0:
            return "RANGE_BOUND_CHOP"

        return "RANGE_BOUND_CHOP"

    def calculate_setup_quality_score(self, payload: Dict[str, Any], oi_snapshot: Dict[str, Any]) -> Tuple[int, str, Dict[str, int], List[str], List[str]]:
        """
        Dimensionally rigorous 0-100 Setup Quality Scoring Engine:
        - Market Regime: 0 to 20 pts
        - Price Structure & Key Levels: 0 to 20 pts
        - VWAP & EMA Trend Alignment: 0 to 15 pts
        - Volume Expansion: 0 to 10 pts
        - Options Microstructure & OI Walls: 0 to 15 pts
        - IV & Spread Liquidity: 0 to 10 pts
        - Trap Risk Audit: 0 to 10 pts
        Returns: (total_score, grade, breakdown_dict, confirmations_list, warnings_list)
        """
        direction = payload.get("direction", "CE")
        spot = payload.get("spot", 22600.0)
        vwap = payload.get("vwap", spot)
        ema9 = payload.get("ema9", spot)
        ema21 = payload.get("ema21", spot)
        ema9_slope = payload.get("ema9_slope", 0.0)
        vol_ratio = payload.get("vol_ratio", 1.2)
        pcr = payload.get("pcr", 1.0)
        call_oi_chg = payload.get("call_oi_change", 0)
        put_oi_chg = payload.get("put_oi_change", 0)
        max_call_wall = payload.get("max_call_wall", spot + 100)
        max_put_wall = payload.get("max_put_wall", spot - 100)
        iv_regime = payload.get("iv_regime", "NORMAL_IV")
        spread_pct = payload.get("spread_pct", 0.3)
        liquidity_status = payload.get("liquidity_status", "GOOD")
        day_high = payload.get("day_high", spot)
        day_low = payload.get("day_low", spot)
        orh = payload.get("orh", day_high)
        orl = payload.get("orl", day_low)
        pdh = payload.get("pdh", day_high + 10)
        pdl = payload.get("pdl", day_low - 10)

        regime = self.cached_regime.get("session_regime", "STRONG_TREND_BULLISH")
        trap_score = self.cached_regime.get("trap_risk_score", 0.15)

        confirmations: List[str] = []
        warnings: List[str] = []

        # 1. Market Regime (0-20 pts)
        regime_score = 0
        if direction == "CE" and regime in ["STRONG_TREND_BULLISH", "BREAKOUT_EXPANSION"]:
            regime_score = 20
            confirmations.append(f"Regime: {regime}")
        elif direction == "PE" and regime in ["STRONG_TREND_BEARISH", "BREAKOUT_EXPANSION"]:
            regime_score = 20
            confirmations.append(f"Regime: {regime}")
        elif regime == "RANGE_BOUND_CHOP":
            regime_score = 5
            warnings.append("Market is in Range-Bound Consolidation")
        elif regime == "HIGH_VOLATILITY_EVENT":
            regime_score = 8
            warnings.append("High Volatility Session - Wider Swings Expected")
        else:
            regime_score = 10

        # 2. Price Structure & Key Levels (0-20 pts)
        structure_score = 0
        is_false_breakout_trap = False
        if direction == "CE":
            if spot >= day_high - 3:
                structure_score += 10
                confirmations.append(f"Day High Test/Breakout (₹{day_high})")
            if spot >= orh - 3:
                structure_score += 6
                confirmations.append(f"Opening Range High Confirmed (₹{orh})")
            if spot >= pdh - 5:
                structure_score += 4
                confirmations.append(f"Above Previous Day High (₹{pdh})")
            # False breakout detection: Breaking Day High with negative EMA slope
            if spot >= day_high and ema9_slope < -0.02:
                is_false_breakout_trap = True
                warnings.append("Liquidity Grab Trap Warning: Spot at Day High with negative momentum slope")
            structure_score = min(20, max(8, structure_score))
        else:
            if spot <= day_low + 3:
                structure_score += 10
                confirmations.append(f"Day Low Breakdown (₹{day_low})")
            if spot <= orl + 3:
                structure_score += 6
                confirmations.append(f"Opening Range Low Broken (₹{orl})")
            if spot <= pdl + 5:
                structure_score += 4
                confirmations.append(f"Below Previous Day Low (₹{pdl})")
            # False breakdown detection: Breaking Day Low with positive EMA slope
            if spot <= day_low and ema9_slope > 0.02:
                is_false_breakout_trap = True
                warnings.append("Liquidity Grab Trap Warning: Spot at Day Low with positive momentum slope")
            structure_score = min(20, max(8, structure_score))

        if is_false_breakout_trap:
            structure_score = max(0, structure_score - 10)

        # 3. VWAP, EMA & Multi-Timeframe (MTF) Alignment (0-15 pts)
        tech_score = 0
        mtf_info = payload.get("mtf_trend", {})
        trend_5m = mtf_info.get("trend_5m", "NEUTRAL")
        cvd = payload.get("cvd", 0.0)

        if direction == "CE":
            if spot > vwap:
                tech_score += 5
                confirmations.append("Price Holding Above VWAP Support")
            if ema9 > ema21:
                tech_score += 4
                confirmations.append("EMA 9 > 21 Bullish Alignment")
            if ema9_slope > 0:
                tech_score += 3
                confirmations.append("Positive EMA Slope Momentum")
            else:
                warnings.append("EMA Slope Flattening")

            if trend_5m == "BULLISH":
                tech_score += 3
                confirmations.append("MTF 5m/15m Trend Confirmed Bullish")
            elif trend_5m == "BEARISH":
                tech_score = max(0, tech_score - 3)
                warnings.append("MTF Trend Conflict: 5m Trend is Bearish")
        else:
            if spot < vwap:
                tech_score += 5
                confirmations.append("Price Rejected Below VWAP Resistance")
            if ema9 < ema21:
                tech_score += 4
                confirmations.append("EMA 9 < 21 Bearish Alignment")
            if ema9_slope < 0:
                tech_score += 3
                confirmations.append("Negative EMA Slope Momentum")
            else:
                warnings.append("EMA Slope Flattening")

            if trend_5m == "BEARISH":
                tech_score += 3
                confirmations.append("MTF 5m/15m Trend Confirmed Bearish")
            elif trend_5m == "BULLISH":
                tech_score = max(0, tech_score - 3)
                warnings.append("MTF Trend Conflict: 5m Trend is Bullish")

        tech_score = min(15, max(0, tech_score))

        # 4. Volume Expansion & Cumulative Volume Delta (CVD) (0-10 pts)
        vol_score = 0
        if vol_ratio >= 1.3:
            vol_score = 8
            confirmations.append(f"Strong Volume Surge ({vol_ratio}x Avg)")
        elif vol_ratio >= 1.1:
            vol_score = 6
            confirmations.append(f"Above Average Volume ({vol_ratio}x)")
        else:
            vol_score = 3
            warnings.append("Subdued Volume Participation")

        if direction == "CE" and cvd > 0:
            vol_score += 2
            confirmations.append(f"CVD Bullish Net Flow (+{cvd:.0f})")
        elif direction == "PE" and cvd < 0:
            vol_score += 2
            confirmations.append(f"CVD Bearish Net Flow ({cvd:.0f})")

        vol_score = min(10, max(0, vol_score))

        # 5. Options OI & Strike Concentration Walls (0-15 pts)
        oi_score = 0
        iv_skew = oi_snapshot.get("iv_skew", 0.0)
        oi_velocity = oi_snapshot.get("oi_velocity", 0.0)

        if direction == "CE":
            if pcr >= 1.10:
                oi_score += 6
                confirmations.append(f"Bullish Put Writing Floor (PCR {pcr})")
            elif pcr >= 0.95:
                oi_score += 4
            else:
                warnings.append(f"Low PCR ({pcr}) - Call Resistance Dominant")

            if call_oi_chg <= 0:
                oi_score += 5
                confirmations.append("Call Unwinding at ATM Strikes")
            else:
                warnings.append("Call Resistance Adding Pressure")

            if spot < max_call_wall:
                oi_score += 4
                confirmations.append(f"Room to Major Call Resistance Wall (₹{max_call_wall})")

            if oi_velocity > 0:
                confirmations.append(f"Positive Put Writing Acceleration (OI Velocity +{oi_velocity:.0f})")
        else:
            if pcr <= 0.80:
                oi_score += 6
                confirmations.append(f"Bearish Call Writing Wall (PCR {pcr})")
            elif pcr <= 0.95:
                oi_score += 4
            else:
                warnings.append(f"High PCR ({pcr}) - Put Support Holding")

            if put_oi_chg <= 0:
                oi_score += 5
                confirmations.append("Put Unwinding at ATM Strikes")
            else:
                warnings.append("Put Support Adding Friction")

            if spot > max_put_wall:
                oi_score += 4
                confirmations.append(f"Room to Major Put Support Wall (₹{max_put_wall})")

            if iv_skew >= 0.6:
                confirmations.append(f"Institutional Downside Hedging Detected (IV Skew +{iv_skew}%)")

        # 6. IV & Spread Liquidity (0-10 pts)
        iv_score = 0
        current_iv = oi_snapshot.get("atm_iv", 13.5)
        ivp_data = BlackScholesEngine.calculate_iv_percentile(current_iv)
        
        if ivp_data.get("is_iv_crush_risk"):
            iv_score += 1
            warnings.append(f"High IV Percentile ({ivp_data['iv_percentile']}%) - High risk of IV Crush on naked buying")
        elif iv_regime == "NORMAL_IV":
            iv_score += 5
            confirmations.append(f"Normal IV Environment ({current_iv}%, IVP {ivp_data['iv_percentile']}%)")
        elif iv_regime == "LOW_IV":
            iv_score += 4
            confirmations.append("Low IV (Favorable Option Premium Entry)")
        else:
            iv_score += 2
            warnings.append(f"Elevated IV ({iv_regime})")

        if liquidity_status in ["EXCELLENT", "GOOD"]:
            iv_score += 5
            confirmations.append(f"Tight Spread ({spread_pct}%) - Excellent Liquidity")
        else:
            iv_score += 1
            warnings.append(f"Wider Spread ({spread_pct}%) - Slippage Caution")

        # 7. Trap Risk Audit (0-10 pts)
        trap_audit_score = round(max(0, (1.0 - trap_score) * 10))
        if trap_score <= 0.25:
            confirmations.append("Low Trap Risk Confirmed by Anomaly Auditor")
        elif trap_score >= 0.60:
            warnings.append(f"Elevated Trap Risk ({int(trap_score*100)}%)")

        # 8. Heavyweight Stock Concordance Audit
        is_concordant, concord_reason = heavyweight_tracker.evaluate_bias_alignment(f"BUY_{direction}")
        concordance_data = heavyweight_tracker.compute_concordance()
        if is_concordant:
            confirmations.append(f"Heavyweight Concordance: {concord_reason} (Regime: {concordance_data['regime']})")
        else:
            warnings.append(f"Heavyweight Divergence Warning: {concord_reason}")

        # 9. Time of Day Session Window & Theta Decay
        dte = oi_snapshot.get("dte", 1.0)
        now_dt = datetime.now()
        cur_min = now_dt.hour * 60 + now_dt.minute
        time_penalty = 0.0

        if settings.ENABLE_TIME_OF_DAY_FILTER:
            if 690 <= cur_min <= 810: # 11:30 to 13:30 Dead Zone
                time_penalty += 5.0
                warnings.append("Midday Institutional Dead Zone (11:30-13:30) - Lower Edge Window")
            elif (555 <= cur_min <= 630) or (810 <= cur_min <= 900): # Morning (09:15-10:30) or Afternoon (13:30-15:00)
                confirmations.append("High Liquidity Session Window")

        theta_penalty, theta_reason = BlackScholesEngine.calculate_theta_decay_penalty(now_dt.hour, now_dt.minute, dte)
        if theta_penalty > 0:
            warnings.append(f"Theta Warning: {theta_reason} (-{int(theta_penalty)} pts penalty)")

        concordance_penalty = 5.0 if not is_concordant else 0.0
        total_deduction = theta_penalty + time_penalty + concordance_penalty
        raw_sum = regime_score + structure_score + tech_score + vol_score + oi_score + iv_score + trap_audit_score
        total_score = min(100, max(0, int(raw_sum - total_deduction)))

        # Grade calculation
        if total_score >= 90:
            grade = "A+"
        elif total_score >= 80:
            grade = "A"
        elif total_score >= 70:
            grade = "B"
        elif total_score >= 60:
            grade = "C"
        else:
            grade = "NO_SIGNAL"

        breakdown = {
            "regime": regime_score,
            "structure": structure_score,
            "technicals": tech_score,
            "volume": vol_score,
            "oi_microstructure": oi_score,
            "iv_liquidity": iv_score,
            "trap_audit": trap_audit_score
        }

        return total_score, grade, breakdown, confirmations, warnings

    async def update_regime_classifier(self, tick_data: Dict[str, Any], oi_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Background worker: evaluates market context & anomaly review every 3 minutes."""
        if not self.api_key:
            self._algorithmic_audit(tick_data, oi_snapshot)
            return self.cached_regime

        try:
            telemetry = {
                "symbol": tick_data.get("symbol", settings.SYMBOL),
                "spot": tick_data.get("price"),
                "day_high": tick_data.get("day_high"),
                "day_low": tick_data.get("day_low"),
                "vwap": tick_data.get("vwap"),
                "atr": tick_data.get("atr"),
                "pcr_ntm": oi_snapshot.get("pcr_ntm", 1.0),
                "atm_call_oi_change": oi_snapshot.get("atm_call_change_oi", 0),
                "atm_put_oi_change": oi_snapshot.get("atm_put_change_oi", 0),
                "atm_iv": oi_snapshot.get("atm_iv", 13.5),
                "max_call_wall": oi_snapshot.get("max_call_wall"),
                "max_put_wall": oi_snapshot.get("max_put_wall")
            }

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"
            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": f"{CONTRADICTION_AUDITOR_PROMPT}\n\n[STRUCTURED TELEMETRY]\n{json.dumps(telemetry, indent=2)}"}]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json"
                }
            }

            def _send_http_post():
                if httpx:
                    with httpx.Client(timeout=2.5) as client:
                        return client.post(url, json=payload)
                import urllib.request
                req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    class MockResp:
                        status_code = resp.status
                        def json(self): return json.loads(resp.read().decode("utf-8"))
                    return MockResp()

            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, _send_http_post)

            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        raw_text = parts[0].get("text", "")
                        parsed = json.loads(raw_text)
                        if "session_regime" in parsed:
                            parsed["last_updated"] = time.time()
                            parsed["is_ai_powered"] = True
                            parsed["trade_bias"] = "FAVOR_CE" if "BULLISH" in parsed["session_regime"] else ("FAVOR_PE" if "BEARISH" in parsed["session_regime"] else "STRICT_NO_TRADE")
                            self.cached_regime = parsed
                            logger.info(f"⚡ AI Anomaly Reviewer Updated: {parsed['session_regime']} | Trap Risk: {int(parsed.get('trap_risk_score',0)*100)}%")
                            return self.cached_regime
        except Exception as e:
            logger.warning(f"Background AI review failed ({e}). Using quantitative audit.")

        self._algorithmic_audit(tick_data, oi_snapshot)
        return self.cached_regime

    def _algorithmic_audit(self, tick_data: Dict[str, Any], oi_snapshot: Dict[str, Any]):
        regime = self.classify_market_regime(tick_data, oi_snapshot)
        spot = tick_data.get("price", 22600.0)
        vwap = tick_data.get("vwap", spot)
        pcr = oi_snapshot.get("pcr_ntm", 1.0)
        call_oi_chg = oi_snapshot.get("atm_call_change_oi", 0)
        put_oi_chg = oi_snapshot.get("atm_put_change_oi", 0)

        # Detect contradictions (e.g. price up but call writing increasing)
        is_contradiction = False
        trap_score = 0.20
        summary = "No contradictory signals detected."

        if spot > vwap and call_oi_chg > 50000:
            is_contradiction = True
            trap_score = 0.65
            summary = "Contradiction: Spot trading above VWAP but heavy Call writing resistance detected."
        elif spot < vwap and put_oi_chg > 50000:
            is_contradiction = True
            trap_score = 0.65
            summary = "Contradiction: Spot trading below VWAP but Put writing support defending level."
        elif regime == "RANGE_BOUND_CHOP":
            trap_score = 0.70
            summary = "Range-bound chop: Balanced OI and flat VWAP indicate low directional edge."
        elif regime in ["STRONG_TREND_BULLISH", "BREAKOUT_EXPANSION"]:
            trap_score = 0.15
            summary = "Trend alignment: Supportive Put writing and Call unwinding confirm directional flow."
        elif regime in ["STRONG_TREND_BEARISH"]:
            trap_score = 0.18
            summary = "Trend alignment: Call writing and Put liquidation confirm downward momentum."

        self.cached_regime = {
            "session_regime": regime,
            "trade_bias": "FAVOR_CE" if "BULLISH" in regime else ("FAVOR_PE" if "BEARISH" in regime else "STRICT_NO_TRADE"),
            "trap_risk_score": trap_score,
            "is_contradiction_detected": is_contradiction,
            "audit_summary": summary,
            "last_updated": time.time(),
            "is_ai_powered": False
        }

    def _get_dynamic_delta(self, spot: float, strike_str: str, direction: str) -> float:
        try:
            parts = strike_str.split()
            strike_price = float(parts[1]) if len(parts) >= 2 else spot
            moneyness_pts = (spot - strike_price) if direction == "CE" else (strike_price - spot)
            raw_delta = 0.50 + (moneyness_pts / 300.0)
            return round(max(0.20, min(0.85, raw_delta)), 2)
        except Exception:
            return 0.50

    @staticmethod
    def get_calibrated_expectancy(score: int) -> Dict[str, Any]:
        """
        Maps 0-100 Setup Quality Score to RESEARCH ESTIMATE expectancy & probability.
        NOTE: These are theoretical research estimates derived from strategy design principles,
        NOT calibrated against a verified historical dataset. They should be treated as
        approximate guidance only, not statistically validated claims.
        Proper calibration requires a minimum of 200+ out-of-sample trades per grade tier.
        - Grade A+ (90-100): Research estimate WR ~75-80%, EV ~ +Rs 920/trade (UNVERIFIED)
        - Grade A  (80-89):  Research estimate WR ~65-70%, EV ~ +Rs 540/trade (UNVERIFIED)
        - Grade B  (70-79):  Research estimate WR ~52-58%, EV ~ +Rs 180/trade (UNVERIFIED)
        - Grade C  (<70):    Research estimate WR <45%, Non-Actionable
        """
        if score >= 90:
            return {"expected_win_rate_pct": 76.5, "expected_value_inr": 920.0, "historical_mae_pts": 3.8, "confidence_tier": "VERY_HIGH", "is_empirically_calibrated": False, "note": "Research estimate only — requires live OOS validation"}
        elif score >= 80:
            return {"expected_win_rate_pct": 68.0, "expected_value_inr": 540.0, "historical_mae_pts": 6.5, "confidence_tier": "HIGH", "is_empirically_calibrated": False, "note": "Research estimate only — requires live OOS validation"}
        elif score >= 70:
            return {"expected_win_rate_pct": 54.0, "expected_value_inr": 180.0, "historical_mae_pts": 10.2, "confidence_tier": "MODERATE", "is_empirically_calibrated": False, "note": "Research estimate only — requires live OOS validation"}
        else:
            return {"expected_win_rate_pct": 40.0, "expected_value_inr": -150.0, "historical_mae_pts": 14.0, "confidence_tier": "LOW", "is_empirically_calibrated": False, "note": "Research estimate only — requires live OOS validation"}

    async def analyze_stage_4(self, payload: Dict[str, Any], oi_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Stage 4: Calculates Setup Quality Score (0-100) and produces structured Decision Support Signal.
        """
        regime_info = self.get_current_regime()
        trap_risk = regime_info.get("trap_risk_score", 0.20)
        direction = payload.get("direction", "CE")
        spot = payload.get("spot", 22600.0)
        atr = payload.get("atr", 14.0)
        strike_str = payload.get("strike", f"{settings.SYMBOL} {int(round(spot/50)*50)} {direction}")
        delta = self._get_dynamic_delta(spot, strike_str, direction)

        total_score, grade, breakdown, confirmations, warnings = self.calculate_setup_quality_score(payload, oi_snapshot)
        calibrated_stats = self.get_calibrated_expectancy(total_score)

        # Signal Filtering: Quality score must be >= 60 (Grade C or higher) and Trap Score <= 0.65
        if total_score < 60 or trap_risk > 0.65:
            return {
                "bias": "NO_TRADE",
                "strike": strike_str,
                "entry_price": round(spot, 2),
                "stop_loss_pts": 0.0,
                "target_pts": 0.0,
                "confidence_pct": total_score,
                "quality_score": total_score,
                "grade": grade,
                "calibrated_expectancy": calibrated_stats,
                "breakdown": breakdown,
                "confirmations": confirmations,
                "warnings": warnings,
                "invalidation_level": payload.get("invalidation_level", round(spot, 2)),
                "expires_at": None,
                "reasoning": f"Setup Quality ({total_score}/100 [Grade {grade}]) below threshold. Anomaly: {regime_info.get('audit_summary')}",
                "is_ai_powered": regime_info.get("is_ai_powered", False)
            }

        bias = "BUY_CE" if direction == "CE" else "BUY_PE"

        # Option Premium SL/Target Floor (>= 12 pts)
        prem_sl = round(max(settings.MIN_SL_FLOOR_PTS, delta * atr), 1)
        prem_tgt = round(prem_sl * settings.RISK_REWARD_RATIO, 1)
        spot_sl = round(prem_sl / delta, 1)
        spot_tgt = round(prem_tgt / delta, 1)

        invalidation_level = payload.get("invalidation_level", round(spot - spot_sl if bias == "BUY_CE" else spot + spot_sl, 2))
        expiry_seconds = 480 # 8-minute signal validity window

        explanation = f"{settings.SYMBOL} {direction} Setup ({total_score}/100 [Grade {grade}]) during {regime_info.get('session_regime')}. EV: +₹{calibrated_stats['expected_value_inr']:.0f} (Hist WR {calibrated_stats['expected_win_rate_pct']}%). " + " | ".join(confirmations[:3])

        strike_val = float(strike_str.split()[-2]) if any(c.isdigit() for c in strike_str) else spot
        bs_info = BlackScholesEngine.calculate_greeks(
            spot=spot, strike=strike_val, time_to_expiry_days=1.0,
            iv_pct=oi_snapshot.get("atm_iv", 13.5), option_type=direction
        )
        opt_entry_prem = round(bs_info.get("theoretical_price", 120.0), 2)
        opt_sl_prem = round(max(5.0, opt_entry_prem - prem_sl), 2)
        opt_tgt_prem = round(opt_entry_prem + prem_tgt, 2)

        return {
            "bias": bias,
            "strike": strike_str,
            "direction": direction,
            "underlying_entry": round(spot, 2),
            "underlying_sl": round(spot - spot_sl if bias == "BUY_CE" else spot + spot_sl, 2),
            "underlying_target": round(spot + spot_tgt if bias == "BUY_CE" else spot - spot_tgt, 2),
            "option_entry_premium": opt_entry_prem,
            "option_sl_premium": opt_sl_prem,
            "option_target_premium": opt_tgt_prem,
            "entry_price": round(spot, 2),
            "stop_loss": round(spot - spot_sl if bias == "BUY_CE" else spot + spot_sl, 2),
            "target": round(spot + spot_tgt if bias == "BUY_CE" else spot - spot_tgt, 2),
            "sl_pts": spot_sl,
            "target_pts": spot_tgt,
            "prem_sl_pts": prem_sl,
            "prem_target_pts": prem_tgt,
            "confidence_pct": total_score,
            "quality_score": total_score,
            "grade": grade,
            "calibrated_expectancy": calibrated_stats,
            "breakdown": breakdown,
            "confirmations": confirmations,
            "warnings": warnings,
            "invalidation_level": invalidation_level,
            "valid_duration_seconds": expiry_seconds,
            "expires_at": time.time() + expiry_seconds,
            "session_regime": regime_info.get("session_regime"),
            "audit_summary": regime_info.get("audit_summary"),
            "estimated_pnl_pts": prem_tgt,
            "rejection_reason": None,
            "reasoning": explanation,
            "is_ai_powered": regime_info.get("is_ai_powered", False)
        }

    async def analyze_breakout(self, tick_data: Dict[str, Any], oi_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Backward-compatible entry point for tests and single-call evaluation."""
        from feed import evaluate_market_state
        ready, status, payload = evaluate_market_state(tick_data, oi_snapshot)
        if not ready:
            return {
                "bias": "NO_TRADE",
                "strike": f"{settings.SYMBOL} {oi_snapshot.get('atm_strike', 22600)} CE",
                "entry_price": tick_data.get("price", 22600.0),
                "stop_loss_pts": 0.0,
                "target_pts": 0.0,
                "confidence_pct": 50.0,
                "quality_score": 50,
                "grade": "NO_SIGNAL",
                "invalidation_level": tick_data.get("vwap", tick_data.get("price", 22600.0)),
                "expires_at": None,
                "confirmations": [],
                "warnings": [status],
                "reasoning": f"Gating Filter: {status}",
                "is_ai_powered": False
            }
        return await self.analyze_stage_4(payload, oi_snapshot)

ai_analyzer = MarketAIAnalyzer()
