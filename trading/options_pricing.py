import math
from typing import Dict, Any, Tuple, List, Optional

def norm_cdf(x: float) -> float:
    """Cumulative standard normal distribution function (accurate numerical approximation)."""
    # Hart approximation for erfc / cdf
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)

class BlackScholesEngine:
    """
    Institutional Black-Scholes Options Pricing & Greeks Engine for Indian Index Derivatives (NIFTY/BANKNIFTY).
    Computes theoretical option price, Delta, Gamma, Theta, and Vega.
    """
    @staticmethod
    def calculate_greeks(
        spot: float,
        strike: float,
        time_to_expiry_days: float,
        iv_pct: float,
        risk_free_rate: float = 0.065, # 6.5% standard Indian RBI repo/T-Bill rate
        option_type: str = "CE"
    ) -> Dict[str, float]:
        """
        Calculates theoretical premium and Greeks:
        - Delta: Price sensitivity to underlying movement
        - Gamma: Rate of change of Delta per point of underlying
        - Theta: Daily time decay (loss in Rs/pts per day)
        - Vega: Sensitivity to 1% change in Implied Volatility
        """
        T = max(1e-5, time_to_expiry_days / 365.0)
        sigma = max(0.01, iv_pct / 100.0)
        r = risk_free_rate
        S = max(1.0, spot)
        K = max(1.0, strike)

        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        pdf_d1 = norm_pdf(d1)
        cdf_d1 = norm_cdf(d1)
        cdf_d2 = norm_cdf(d2)

        is_call = (option_type.upper() == "CE" or option_type.upper() == "CALL")

        if is_call:
            theoretical_price = S * cdf_d1 - K * math.exp(-r * T) * cdf_d2
            delta = cdf_d1
            theta_annual = -(S * pdf_d1 * sigma) / (2.0 * math.sqrt(T)) - r * K * math.exp(-r * T) * cdf_d2
        else:
            cdf_neg_d1 = norm_cdf(-d1)
            cdf_neg_d2 = norm_cdf(-d2)
            theoretical_price = K * math.exp(-r * T) * cdf_neg_d2 - S * cdf_neg_d1
            delta = cdf_d1 - 1.0
            theta_annual = -(S * pdf_d1 * sigma) / (2.0 * math.sqrt(T)) + r * K * math.exp(-r * T) * cdf_neg_d2

        gamma = pdf_d1 / (S * sigma * math.sqrt(T))
        vega_annual = S * math.sqrt(T) * pdf_d1
        vega = vega_annual / 100.0 # Per 1% IV change
        theta_daily = theta_annual / 365.0 # Per calendar day decay

        return {
            "theoretical_price": round(max(0.05, theoretical_price), 2),
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta_daily": round(theta_daily, 2),
            "vega": round(vega, 2),
            "d1": round(d1, 4),
            "d2": round(d2, 4)
        }

    @staticmethod
    def calculate_max_pain(option_chain: List[Dict[str, Any]]) -> float:
        """
        Calculates the theoretical Option Max Pain strike where option writers face minimum cumulative loss.
        """
        if not option_chain:
            return 22600.0

        strikes = sorted(list(set([opt["strike"] for opt in option_chain if "strike" in opt])))
        if not strikes:
            return 22600.0

        min_loss = float("inf")
        max_pain_strike = strikes[0]

        for s in strikes:
            total_loss = 0.0
            for opt in option_chain:
                k = opt.get("strike", s)
                call_oi = opt.get("call_oi", 0)
                put_oi = opt.get("put_oi", 0)

                # Loss on Calls if price expires at s: max(0, s - k) * call_oi
                if s > k:
                    total_loss += (s - k) * call_oi
                # Loss on Puts if price expires at s: max(0, k - s) * put_oi
                if s < k:
                    total_loss += (k - s) * put_oi

            if total_loss < min_loss:
                min_loss = total_loss
                max_pain_strike = s

        return float(max_pain_strike)

    @staticmethod
    def calculate_iv_percentile(current_iv: float, iv_history: Optional[List[float]] = None) -> Dict[str, Any]:
        """
        Calculates IV Percentile (IVP) against historical IV distribution.
        IVP > 80 indicates high volatility (high risk of IV crush for naked option buyers).
        """
        if not iv_history:
            # Standard NIFTY baseline IV range: 10.0 to 24.0
            min_iv, max_iv = 10.0, 24.0
            iv_rank = max(0.0, min(100.0, ((current_iv - min_iv) / (max_iv - min_iv)) * 100.0))
            return {
                "current_iv": current_iv,
                "iv_percentile": round(iv_rank, 1),
                "is_iv_crush_risk": current_iv >= 20.0 or iv_rank >= 80.0,
                "regime": "HIGH_IV_CRUSH_RISK" if iv_rank >= 80.0 else ("LOW_IV" if iv_rank <= 25.0 else "NORMAL_IV")
            }
        
        count_below = sum(1 for iv in iv_history if iv < current_iv)
        ivp = (count_below / len(iv_history)) * 100.0
        return {
            "current_iv": current_iv,
            "iv_percentile": round(ivp, 1),
            "is_iv_crush_risk": ivp >= 80.0 or current_iv >= 20.0,
            "regime": "HIGH_IV_CRUSH_RISK" if ivp >= 80.0 else ("LOW_IV" if ivp <= 25.0 else "NORMAL_IV")
        }

    @staticmethod
    def calculate_theta_decay_penalty(hour: int, minute: int, days_to_expiry: float) -> Tuple[float, str]:
        """
        Computes intraday time decay penalty.
        On Expiry Day (DTE <= 0.5) between 1:00 PM and 2:45 PM (13:00 to 14:45),
        naked option buying experiences severe nonlinear theta destruction during chop.
        """
        if days_to_expiry <= 0.5: # 0-DTE Expiry Session
            time_mins = hour * 60 + minute
            if 780 <= time_mins <= 885: # 13:00 to 14:45
                return 15.0, "EXPIRY_AFTERNOON_THETA_CHOP_ZONE"
            elif time_mins > 885: # 14:45+ Hero or Zero zone
                return 10.0, "EXPIRY_LATE_SESSION_VOLATILITY"
        return 0.0, "NORMAL_THETA_REGIME"

    @staticmethod
    def calculate_dynamic_option_price(
        spot_entry: float,
        strike: float,
        dte_days: float,
        iv_pct: float,
        option_type: str = "CE",
        spot_change: float = 0.0,
        time_elapsed_minutes: float = 0.0,
        iv_change: float = 0.0,
        spread_half: float = 0.50
    ) -> Dict[str, float]:
        """
        Dynamically calculates option premium movement using second-order Taylor expansion Greeks:
        ΔOption = Delta * ΔS + 0.5 * Gamma * (ΔS)^2 + Vega * ΔIV + (Theta_daily / 1440) * Δt_mins
        Replaces static 0.50 delta proxy with mathematically rigorous pricing.
        """
        greeks = BlackScholesEngine.calculate_greeks(
            spot=spot_entry,
            strike=strike,
            time_to_expiry_days=dte_days,
            iv_pct=iv_pct,
            option_type=option_type
        )
        base_theo = greeks["theoretical_price"]
        delta = greeks["delta"]
        gamma = greeks["gamma"]
        vega = greeks["vega"]
        theta_min = greeks["theta_daily"] / 375.0 # 375 trading minutes per session

        delta_effect = delta * spot_change
        gamma_effect = 0.5 * gamma * (spot_change ** 2)
        vega_effect = vega * iv_change
        theta_effect = theta_min * time_elapsed_minutes

        simulated_premium = max(0.50, base_theo + delta_effect + gamma_effect + vega_effect + theta_effect)
        return {
            "entry_theoretical": base_theo,
            "simulated_premium": round(simulated_premium, 2),
            "delta_effect": round(delta_effect, 2),
            "gamma_effect": round(gamma_effect, 2),
            "vega_effect": round(vega_effect, 2),
            "theta_effect": round(theta_effect, 2),
            "effective_delta": round(delta + (gamma * spot_change), 4),
            "bid": round(max(0.05, simulated_premium - spread_half), 2),
            "ask": round(simulated_premium + spread_half, 2)
        }

    @staticmethod
    def calculate_implementation_shortfall(
        decision_price: float,
        expected_fill_price: float,
        actual_fill_price: float,
        side: str = "BUY"
    ) -> Dict[str, Any]:
        """
        Calculates institutional Implementation Shortfall (IS):
        IS = Actual Fill Price - Decision Price (for BUY)
        Measures total execution drag across market impact, latency slippage, and spread.
        """
        multiplier = 1.0 if side.upper() == "BUY" else -1.0
        slippage_pts = round((actual_fill_price - expected_fill_price) * multiplier, 2)
        delay_cost_pts = round((expected_fill_price - decision_price) * multiplier, 2)
        total_is_pts = round((actual_fill_price - decision_price) * multiplier, 2)

        quality = "OPTIMAL" if total_is_pts <= 0.25 else ("ACCEPTABLE" if total_is_pts <= 0.75 else "DEGRADED")
        return {
            "decision_price": decision_price,
            "expected_fill": expected_fill_price,
            "actual_fill": actual_fill_price,
            "slippage_pts": slippage_pts,
            "delay_cost_pts": delay_cost_pts,
            "total_implementation_shortfall_pts": total_is_pts,
            "execution_quality": quality
        }


class PortfolioGreeksEngine:
    """
    Institutional Portfolio-Level Risk & Exposure Kill-Switch Engine.
    Computes Net Delta, Gamma, Vega, Theta across concurrent positions and
    enforces multi-tier safety states: GREEN -> YELLOW -> ORANGE -> RED.
    """
    def __init__(self, max_net_delta: float = 250.0, max_net_gamma: float = 0.50):
        self.max_net_delta = max_net_delta
        self.max_net_gamma = max_net_gamma

    def evaluate_portfolio_state(
        self,
        open_positions: List[Dict[str, Any]],
        daily_pnl: float,
        max_daily_loss: float,
        consecutive_losses: int
    ) -> Dict[str, Any]:
        """
        Evaluates portfolio state:
        - GREEN: Normal full size execution (100%)
        - YELLOW: 50% lot size reduction (drawdown > 50% or elevated Greeks)
        - ORANGE: Halt new entries, manage existing positions only
        - RED: Hard kill-switch: flatten all positions and lockout
        """
        net_delta = 0.0
        net_gamma = 0.0
        net_vega = 0.0
        net_theta = 0.0

        for pos in open_positions:
            qty = pos.get("lots", 1) * pos.get("lot_size", 65)
            greeks = pos.get("greeks", {})
            net_delta += greeks.get("delta", 0.50) * qty
            net_gamma += greeks.get("gamma", 0.001) * qty
            net_vega += greeks.get("vega", 1.0) * qty
            net_theta += greeks.get("theta_daily", -5.0) * qty

        # Multi-Tier Kill-Switch State Machine
        if daily_pnl <= -max_daily_loss or consecutive_losses >= 2:
            kill_state = "RED"
            action = "HARD_LOCKOUT_FLATTEN_ALL"
            size_multiplier = 0.0
        elif daily_pnl <= -0.60 * max_daily_loss or abs(net_delta) > self.max_net_delta:
            kill_state = "ORANGE"
            action = "HALT_NEW_ENTRIES_MANAGE_OPEN"
            size_multiplier = 0.0
        elif daily_pnl <= -0.35 * max_daily_loss or abs(net_delta) > 0.6 * self.max_net_delta:
            kill_state = "YELLOW"
            action = "REDUCED_SIZE_50_PCT"
            size_multiplier = 0.5
        else:
            kill_state = "GREEN"
            action = "NORMAL_EXECUTION"
            size_multiplier = 1.0

        return {
            "kill_state": kill_state,
            "action": action,
            "size_multiplier": size_multiplier,
            "net_delta": round(net_delta, 2),
            "net_gamma": round(net_gamma, 4),
            "net_vega": round(net_vega, 2),
            "net_theta": round(net_theta, 2),
            "is_trade_permitted": kill_state in ["GREEN", "YELLOW"]
        }

portfolio_risk_engine = PortfolioGreeksEngine()
