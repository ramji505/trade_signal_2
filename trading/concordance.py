"""
NIFTY 50 Heavyweight Stock Concordance Engine
Monitors the top 7 index-moving stocks (accounting for ~46.5% of NIFTY weight)
to confirm underlying institutional momentum and prevent index bull/bear traps.
"""
from typing import Dict, Any, List, Optional, Tuple
import time

import urllib.parse
import logging

logger = logging.getLogger("Concordance")

HEAVYWEIGHT_WEIGHTS: Dict[str, float] = {
    "HDFCBANK": 0.135,
    "RELIANCE": 0.092,
    "ICICIBANK": 0.078,
    "INFY": 0.055,
    "TCS": 0.038,
    "ITC": 0.035,
    "LT": 0.032,
}

UPSTOX_HEAVYWEIGHT_KEYS: Dict[str, str] = {
    "HDFCBANK": "NSE_EQ|INE040A01034",
    "RELIANCE": "NSE_EQ|INE002A01018",
    "ICICIBANK": "NSE_EQ|INE090A01021",
    "INFY": "NSE_EQ|INE009A01021",
    "TCS": "NSE_EQ|INE467B01029",
}

class HeavyweightConcordanceTracker:
    """
    Evaluates real-time constituent momentum and concordance alignment.
    Prevents buying index CE when key heavyweights are dumping, or buying index PE
    when banking/tech heavyweights are ramping up.
    """
    def __init__(self):
        self._last_fetch_time: float = 0.0
        self.constituents: Dict[str, Dict[str, Any]] = {
            symbol: {
                "weight": weight,
                "price": 1000.0,
                "change_pct": 0.0,
                "vwap": 1000.0,
                "trend": 0, # +1 = Bullish, -1 = Bearish, 0 = Neutral
                "last_update": time.time()
            }
            for symbol, weight in HEAVYWEIGHT_WEIGHTS.items()
        }

    def update_from_upstox(self) -> None:
        """Pulls real-time multi-quotes for top 5 NIFTY heavyweights from Upstox API v2."""
        now = time.time()
        if (now - self._last_fetch_time) < 3.0:
            return
        try:
            from config import settings
            if settings.MARKET_DATA_MODE != "UPSTOX" or not settings.UPSTOX_ACCESS_TOKEN:
                return
            from upstox_client import upstox_client
            keys_str = ",".join(UPSTOX_HEAVYWEIGHT_KEYS.values())
            safe_keys = urllib.parse.quote(keys_str, safe=",")
            resp = upstox_client._get(f"/market-quote/quotes?instrument_key={safe_keys}")
            data = resp.get("data", {})
            for sym, isin_key in UPSTOX_HEAVYWEIGHT_KEYS.items():
                dict_key = isin_key.replace("|", ":")
                sym_key = f"NSE_EQ:{sym}"
                stock = data.get(sym_key) or data.get(dict_key) or {}
                if stock:
                    ltp = float(stock.get("last_price") or 0.0)
                    ohlc = stock.get("ohlc", {})
                    open_p = float(ohlc.get("open") or ltp)
                    change_pct = round((ltp - open_p) / open_p * 100.0, 2) if open_p > 0 else 0.0
                    vwap = float(stock.get("average_price") or ohlc.get("close") or ltp)
                    self.update_stock_tick(sym, ltp, change_pct, vwap)
            self._last_fetch_time = now
        except Exception as e:
            logger.debug(f"Unable to update live heavyweight concordance: {e}")

    def update_stock_tick(self, symbol: str, price: float, change_pct: float, vwap: Optional[float] = None):
        """Update price and trend posture for a constituent stock."""
        sym = symbol.upper().replace(".NS", "").replace("NSE:", "")
        if sym in self.constituents:
            vwap_val = vwap if vwap is not None else price
            trend = 1 if price > vwap_val and change_pct > 0 else (-1 if price < vwap_val and change_pct < 0 else 0)
            self.constituents[sym] = {
                "weight": HEAVYWEIGHT_WEIGHTS[sym],
                "price": price,
                "change_pct": change_pct,
                "vwap": vwap_val,
                "trend": trend,
                "last_update": time.time()
            }

    def compute_concordance(self) -> Dict[str, Any]:
        """
        Computes the weighted institutional concordance score: [-1.0, +1.0].
        """
        total_weight = sum(HEAVYWEIGHT_WEIGHTS.values())
        weighted_score = 0.0
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0

        details = {}
        for sym, data in self.constituents.items():
            w = data["weight"]
            trend = data["trend"]
            weighted_score += (trend * w)
            if trend > 0:
                bullish_count += 1
            elif trend < 0:
                bearish_count += 1
            else:
                neutral_count += 1
            details[sym] = {
                "change_pct": data["change_pct"],
                "trend": "BULLISH" if trend > 0 else ("BEARISH" if trend < 0 else "NEUTRAL")
            }

        # Normalize score between -1.0 and +1.0 relative to monitored basket
        normalized_score = round(weighted_score / total_weight, 3)

        if normalized_score >= 0.35:
            regime = "STRONG_BULLISH_CONCORDANCE"
        elif normalized_score >= 0.10:
            regime = "MILD_BULLISH_CONCORDANCE"
        elif normalized_score <= -0.35:
            regime = "STRONG_BEARISH_CONCORDANCE"
        elif normalized_score <= -0.10:
            regime = "MILD_BEARISH_CONCORDANCE"
        else:
            regime = "NEUTRAL_DIVERGENCE"

        return {
            "score": normalized_score,
            "regime": regime,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": neutral_count,
            "total_monitored": len(self.constituents),
            "basket_weight_pct": round(total_weight * 100.0, 1),
            "details": details
        }

    def evaluate_bias_alignment(self, bias: str) -> Tuple[bool, str]:
        """
        Validates whether index signal aligns with heavyweights.
        Returns: (is_aligned, reason)
        """
        concordance = self.compute_concordance()
        score = concordance["score"]

        if bias == "BUY_CE":
            if score < -0.15:
                return False, f"Heavyweight divergence: Basket score {score} is bearish (HDFCBANK/RELIANCE lagging)."
            return True, f"Heavyweight confirmation: Basket score {score} supports bullish trend."
        elif bias == "BUY_PE":
            if score > 0.15:
                return False, f"Heavyweight divergence: Basket score {score} is bullish (Heavyweights holding up)."
            return True, f"Heavyweight confirmation: Basket score {score} supports bearish breakdown."
        return True, "Neutral bias."

# Global Singleton instance
heavyweight_tracker = HeavyweightConcordanceTracker()
