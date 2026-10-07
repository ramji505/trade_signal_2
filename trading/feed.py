import asyncio
import time
import math
import random
import logging
from typing import Dict, Any, Optional, Callable, List, Tuple
from datetime import datetime, timezone
from config import settings
from database import record_price_tick

logger = logging.getLogger("MarketFeed")
logger.setLevel(logging.INFO)

def build_mtf_candles(candles_1m: List[Dict[str, Any]], timeframe_minutes: int) -> List[Dict[str, Any]]:
    """
    Constructs true multi-timeframe aggregated candles from base 1-minute candle sequence.
    Invariant:
    - Open: Open price of the first 1m candle in the timeframe block
    - High: Maximum High across all 1m candles in the block
    - Low: Minimum Low across all 1m candles in the block
    - Close: Close price of the last 1m candle in the block
    - Volume: Sum of Volumes across the block
    - Timestamp: Timestamp of the first 1m candle
    """
    if timeframe_minutes <= 1:
        return [dict(c) for c in candles_1m]

    aggregated: List[Dict[str, Any]] = []
    total_1m = len(candles_1m)

    for i in range(0, total_1m, timeframe_minutes):
        chunk = candles_1m[i:i + timeframe_minutes]
        if not chunk:
            continue
        
        c_open = chunk[0].get("open", chunk[0].get("close", 0.0))
        c_high = max(c.get("high", c_open) for c in chunk)
        c_low = min(c.get("low", c_open) for c in chunk)
        c_close = chunk[-1].get("close", c_open)
        c_vol = sum(c.get("volume", 0.0) for c in chunk)
        c_time = chunk[0].get("timestamp", time.time())

        aggregated.append({
            "timestamp": c_time,
            "open": round(c_open, 2),
            "high": round(c_high, 2),
            "low": round(c_low, 2),
            "close": round(c_close, 2),
            "volume": round(c_vol, 1)
        })

    return aggregated

class CandleBuffer:
    """Maintains rolling buffers of 1-minute and 5-minute candles for MTF alignment & Opening Range."""
    def __init__(self, maxlen: int = 50, base_price: float = 22600.0, base_epoch: Optional[float] = None):
        self.maxlen = maxlen
        self.candles_1m: List[Dict[str, Any]] = []
        self.candles_5m: List[Dict[str, Any]] = []
        self.aggressor_buy_vol: float = 6500.0
        self.aggressor_sell_vol: float = 3500.0

        current_time = base_epoch if base_epoch is not None else (time.time() - (maxlen * 60.0))

        for i in range(maxlen):
            t = current_time + (i * 60.0)
            progress = i / float(maxlen)
            mid = (base_price - 12.0) + (progress * 10.0)
            c_open = mid
            c_high = mid + 5.0 + random.uniform(0.5, 2.0)
            c_low = mid - 5.0 - random.uniform(0.5, 2.0)
            c_close = mid + 1.0 + (progress * 1.5)
            c_vol = 950.0 + (progress * 300.0) + random.uniform(10.0, 50.0)

            c_high = max(c_high, c_open, c_close)
            c_low = min(c_low, c_open, c_close)

            self.candles_1m.append({
                "timestamp": t,
                "open": round(c_open, 2),
                "high": round(c_high, 2),
                "low": round(c_low, 2),
                "close": round(c_close, 2),
                "volume": round(c_vol, 1)
            })

        # Build true 5m candles from 1m candles
        self.candles_5m = build_mtf_candles(self.candles_1m, 5)

    def load_1m_candles(self, candles: List[Dict[str, Any]]) -> None:
        """Replace synthetic warm-up history with verified historical candles."""
        normalized = []
        for c in candles[-self.maxlen:]:
            try:
                normalized.append({
                    "timestamp": c["timestamp"],
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": float(c.get("volume", 0.0)),
                })
            except (KeyError, TypeError, ValueError):
                continue
        if normalized:
            self.candles_1m = normalized
            self.candles_5m = build_mtf_candles(self.candles_1m, 5)

    def add_tick(self, price: float, volume: float = 1200.0, is_buyer_aggressor: bool = True, timestamp: Optional[float] = None):
        t = timestamp if timestamp is not None else time.time()
        if not self.candles_1m:
            self.candles_1m.append({"timestamp": t, "open": price, "high": price, "low": price, "close": price, "volume": volume})
            return

        last_1m = self.candles_1m[-1]
        last_1m["high"] = max(last_1m["high"], price)
        last_1m["low"] = min(last_1m["low"], price)
        last_1m["close"] = price
        last_1m["volume"] += volume

        if is_buyer_aggressor:
            self.aggressor_buy_vol += volume
        else:
            self.aggressor_sell_vol += volume

    def push_new_candle(self, open_price: float, volume: float = 1200.0, timestamp: Optional[float] = None):
        t = timestamp if timestamp is not None else time.time()
        if len(self.candles_1m) >= self.maxlen:
            self.candles_1m.pop(0)
        self.candles_1m.append({
            "timestamp": t,
            "open": open_price,
            "high": open_price,
            "low": open_price,
            "close": open_price,
            "volume": volume
        })

        if len(self.candles_1m) % 5 == 0:
            if len(self.candles_5m) >= self.maxlen:
                self.candles_5m.pop(0)
            self.candles_5m.append({
                "timestamp": t,
                "open": open_price,
                "high": open_price,
                "low": open_price,
                "close": open_price,
                "volume": volume * 5
            })

    def get_opening_range(self, period_candles: int = 15) -> Tuple[float, float, bool]:
        """Calculates 15-minute Opening Range High (ORH) and Opening Range Low (ORL)."""
        if len(self.candles_1m) < period_candles:
            sample = self.candles_1m
            ready = False
        else:
            sample = self.candles_1m[:period_candles]
            ready = True
        
        orh = max([c["high"] for c in sample]) if sample else 22650.0
        orl = min([c["low"] for c in sample]) if sample else 22550.0
        return round(orh, 2), round(orl, 2), ready

    def get_aggressor_buy_ratio(self) -> float:
        total = self.aggressor_buy_vol + self.aggressor_sell_vol
        return round((self.aggressor_buy_vol / total) * 100.0, 1) if total > 0 else 50.0

    def get_5m_ema20(self) -> float:
        closes = [c["close"] for c in self.candles_5m]
        if not closes:
            return self.candles_1m[-1]["close"]
        alpha = 2.0 / (20.0 + 1.0)
        ema = closes[0]
        for p in closes[1:]:
            ema = p * alpha + ema * (1.0 - alpha)
        return round(ema, 2)

    def get_closes(self) -> List[float]:
        return [c["close"] for c in self.candles_1m]

    def get_highs(self) -> List[float]:
        return [c["high"] for c in self.candles_1m]

    def get_lows(self) -> List[float]:
        return [c["low"] for c in self.candles_1m]

    def get_volumes(self) -> List[float]:
        return [c["volume"] for c in self.candles_1m]

    def calculate_ema(self, period: int) -> List[float]:
        closes = self.get_closes()
        if not closes:
            return []
        alpha = 2.0 / (period + 1.0)
        ema = [closes[0]]
        for price in closes[1:]:
            ema.append(round(price * alpha + ema[-1] * (1.0 - alpha), 2))
        return ema

    def calculate_atr(self, period: int = 14) -> float:
        if len(self.candles_1m) < 2:
            return 12.0
        tr_list = []
        for i in range(1, len(self.candles_1m)):
            h = self.candles_1m[i]["high"]
            l = self.candles_1m[i]["low"]
            prev_c = self.candles_1m[i-1]["close"]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr_list.append(tr)
        subset = tr_list[-period:] if len(tr_list) >= period else tr_list
        return round(sum(subset) / len(subset), 2) if subset else 12.0

    def calculate_vwap(self) -> float:
        total_pv = 0.0
        total_vol = 0.0
        for c in self.candles_1m:
            typical_price = (c["high"] + c["low"] + c["close"]) / 3.0
            total_pv += typical_price * c["volume"]
            total_vol += c["volume"]
        return round(total_pv / total_vol, 2) if total_vol > 0 else self.candles_1m[-1]["close"]

    def get_mtf_trend(self) -> Dict[str, Any]:
        """
        Calculates Multi-Timeframe (MTF) trend confirmation on 5m/15m aggregated data:
        - 5m EMA9 vs EMA21
        - 5m Price vs 5m VWAP
        - Cumulative Volume Delta (CVD)
        """
        candles_5m = build_mtf_candles(self.candles_1m, 5)
        candles_15m = build_mtf_candles(self.candles_1m, 15)
        
        c5_closes = [c["close"] for c in candles_5m]
        spot = self.candles_1m[-1]["close"] if self.candles_1m else 22600.0
        
        # 5m EMA9
        alpha9 = 2.0 / 10.0
        ema9_5m = c5_closes[0] if c5_closes else spot
        for p in c5_closes[1:]:
            ema9_5m = p * alpha9 + ema9_5m * (1.0 - alpha9)
        
        # 5m EMA21
        alpha21 = 2.0 / 22.0
        ema21_5m = c5_closes[0] if c5_closes else spot
        for p in c5_closes[1:]:
            ema21_5m = p * alpha21 + ema21_5m * (1.0 - alpha21)
        
        trend_5m = "BULLISH" if (spot > ema9_5m > ema21_5m) else ("BEARISH" if (spot < ema9_5m < ema21_5m) else "NEUTRAL")
        
        # Cumulative Volume Delta (CVD)
        cvd = round(self.aggressor_buy_vol - self.aggressor_sell_vol, 1)
        cvd_bias = "BULLISH_FLOW" if cvd > 500 else ("BEARISH_FLOW" if cvd < -500 else "BALANCED_FLOW")
        
        return {
            "trend_5m": trend_5m,
            "ema9_5m": round(ema9_5m, 2),
            "ema21_5m": round(ema21_5m, 2),
            "cvd": cvd,
            "cvd_bias": cvd_bias,
            "candles_15m_count": len(candles_15m)
        }

    def calculate_avg_volume(self, period: int = 20) -> float:
        vols = self.get_volumes()
        subset = vols[-period:] if len(vols) >= period else vols
        return sum(subset) / len(subset) if subset else 1000.0

class TickMetrics:
    def __init__(self, symbol: str = "NIFTY", base_price: float = 22600.0):
        self.symbol = symbol
        self.current_price = base_price
        self.day_open = base_price - 12.0
        self.day_high = base_price + 35.0
        self.day_low = base_price - 40.0
        self.pdh = base_price + 45.0   # Previous Day High
        self.pdl = base_price - 55.0   # Previous Day Low
        self.pdc = base_price - 8.0    # Previous Day Close
        self.candle_buffer = CandleBuffer(maxlen=50, base_price=base_price)
        self.tick_count = 0
        self.last_tick_time = datetime.now()

    def update(self, price: float, volume: float = 100.0) -> Dict[str, Any]:
        self.current_price = round(price, 2)
        self.tick_count += 1
        self.last_tick_time = datetime.now()

        # Update Day High / Low
        if self.current_price > self.day_high:
            self.day_high = self.current_price
        if self.current_price < self.day_low:
            self.day_low = self.current_price

        # Update candle buffer
        self.candle_buffer.add_tick(price, volume)
        if self.tick_count % 30 == 0:
            self.candle_buffer.push_new_candle(price, volume)

        # 1. EMAs and Slope
        ema9_series = self.candle_buffer.calculate_ema(9)
        ema21_series = self.candle_buffer.calculate_ema(21)
        ema9 = ema9_series[-1] if ema9_series else self.current_price
        ema21 = ema21_series[-1] if ema21_series else self.current_price
        
        # EMA9 slope over last 3 periods: (EMA9[t] - EMA9[t-3]) / 3
        ema9_prev = ema9_series[-4] if len(ema9_series) >= 4 else ema9_series[0]
        ema9_slope = round((ema9 - ema9_prev) / 3.0, 3)

        # 2. VWAP and ATR
        vwap = self.candle_buffer.calculate_vwap()
        atr = self.candle_buffer.calculate_atr(14)
        avg_vol = self.candle_buffer.calculate_avg_volume(20)
        curr_vol = self.candle_buffer.get_volumes()[-1] if self.candle_buffer.get_volumes() else 1000.0

        # 3. Market Structure Levels
        orh, orl, or_ready = self.candle_buffer.get_opening_range(15)

        # 4. Proximity Checks to Key Levels (Day High, Day Low, ORH, ORL, PDH, PDL)
        dist_to_high_pct = abs(self.day_high - self.current_price) / self.day_high * 100.0
        dist_to_low_pct = abs(self.current_price - self.day_low) / self.day_low * 100.0
        dist_to_orh_pct = abs(orh - self.current_price) / orh * 100.0
        dist_to_orl_pct = abs(self.current_price - orl) / orl * 100.0
        dist_to_pdh_pct = abs(self.pdh - self.current_price) / self.pdh * 100.0
        dist_to_pdl_pct = abs(self.current_price - self.pdl) / self.pdl * 100.0

        is_near_high = (dist_to_high_pct <= settings.BREAKOUT_THRESHOLD_PCT)
        is_near_low = (dist_to_low_pct <= settings.BREAKOUT_THRESHOLD_PCT)
        is_near_orh = (dist_to_orh_pct <= settings.BREAKOUT_THRESHOLD_PCT)
        is_near_orl = (dist_to_orl_pct <= settings.BREAKOUT_THRESHOLD_PCT)

        momentum = "BULLISH" if ema9 > ema21 and self.current_price > vwap else (
            "BEARISH" if ema9 < ema21 and self.current_price < vwap else "NEUTRAL"
        )

        # 5. Multi-Timeframe Trend & CVD Aggregation
        mtf_info = self.candle_buffer.get_mtf_trend()

        return {
            "symbol": self.symbol,
            "timestamp": self.last_tick_time.isoformat(),
            "price": self.current_price,
            "day_open": self.day_open,
            "day_high": self.day_high,
            "day_low": self.day_low,
            "pdh": self.pdh,
            "pdl": self.pdl,
            "pdc": self.pdc,
            "orh": orh,
            "orl": orl,
            "or_ready": or_ready,
            "ema_9": ema9,
            "ema_21": ema21,
            "ema9_slope": ema9_slope,
            "vwap": vwap,
            "atr": atr,
            "curr_vol": curr_vol,
            "avg_vol": avg_vol,
            "mtf_trend": mtf_info,
            "cvd": mtf_info.get("cvd", 0.0),
            "dist_to_high_pct": round(dist_to_high_pct, 4),
            "dist_to_low_pct": round(dist_to_low_pct, 4),
            "dist_to_orh_pct": round(dist_to_orh_pct, 4),
            "dist_to_orl_pct": round(dist_to_orl_pct, 4),
            "dist_to_pdh_pct": round(dist_to_pdh_pct, 4),
            "dist_to_pdl_pct": round(dist_to_pdl_pct, 4),
            "is_near_high": is_near_high,
            "is_near_low": is_near_low,
            "is_near_orh": is_near_orh,
            "is_near_orl": is_near_orl,
            "momentum": momentum,
            "tick_count": self.tick_count,
            "timestamp_raw": time.time(),
            "data_source": settings.MARKET_DATA_MODE
        }

class OptionChainSnapshot:
    """Calculates option microstructure with optional live Groww source and cache."""
    _provider = None
    _cache: Dict[str, Any] = {}
    _cache_time: float = 0.0
    _oi_snapshot_cache: Dict[float, Dict[str, int]] = {}  # strike -> {"ce_oi": ..., "pe_oi": ...}

    @classmethod
    def set_provider(cls, provider: Any) -> None:
        cls._provider = provider
        cls._cache = {}
        cls._cache_time = 0.0

    @classmethod
    def _from_live(cls, payload: Dict[str, Any], spot: float) -> Dict[str, Any]:
        strikes_data = payload.get("strikes", {}) if isinstance(payload, dict) else {}
        rows = []
        atm = min((float(k) for k in strikes_data.keys()), key=lambda k: abs(k - spot)) if strikes_data else round(spot / 50) * 50
        total_ce = total_pe = 0
        max_call = (atm, 0)
        max_put = (atm, 0)
        atm_iv = 13.5
        atm_call_chg = 0
        atm_put_chg = 0
        live_spread_pct = 0.30
        for key, item in strikes_data.items():
            try:
                strike = float(key)
                ce = item.get("CE", {}) or {}
                pe = item.get("PE", {}) or {}
                ce_oi = int(ce.get("open_interest") or 0)
                pe_oi = int(pe.get("open_interest") or 0)
                ce_vol = int(ce.get("volume") or 0)
                pe_vol = int(pe.get("volume") or 0)
                total_ce += ce_oi; total_pe += pe_oi
                if ce_oi > max_call[1]: max_call = (strike, ce_oi)
                if pe_oi > max_put[1]: max_put = (strike, pe_oi)
                if abs(strike - atm) < 0.01:
                    atm_iv = float((ce.get("greeks") or {}).get("iv") or (pe.get("greeks") or {}).get("iv") or atm_iv)
                    # OI change: derived by diffing current vs cached previous snapshot
                    # (Groww API does not expose oi_day_change directly)
                    cached = cls._oi_snapshot_cache.get(strike, {})
                    prev_ce_oi = cached.get("ce_oi", ce_oi)
                    prev_pe_oi = cached.get("pe_oi", pe_oi)
                    atm_call_chg = ce_oi - prev_ce_oi
                    atm_put_chg = pe_oi - prev_pe_oi
                    cls._oi_snapshot_cache[strike] = {"ce_oi": ce_oi, "pe_oi": pe_oi}
                    # Compute live bid-ask spread from quote data if available
                    ce_bid = float(ce.get("bid_price") or 0.0)
                    ce_ask = float(ce.get("ask_price") or 0.0)
                    ce_ltp = float(ce.get("ltp") or 0.0)
                    if ce_bid > 0 and ce_ask > 0 and ce_ltp > 0:
                        mid = (ce_bid + ce_ask) / 2.0
                        live_spread_pct = round((ce_ask - ce_bid) / mid * 100.0, 3)
                    else:
                        live_spread_pct = round(min(1.0, (ce_ltp * 0.005)) if ce_ltp > 0 else 0.30, 3)
                rows.append({"strike": strike, "call_oi": ce_oi, "put_oi": pe_oi, "call_oi_change": atm_call_chg, "put_oi_change": atm_put_chg})
            except (TypeError, ValueError):
                continue
        pcr = round(total_pe / total_ce, 2) if total_ce else 1.0
        return {
            "atm_strike": atm, "pcr": pcr, "pcr_ntm": pcr,
            "max_call_wall": max_call[0], "max_put_wall": max_put[0],
            "atm_iv": atm_iv, "iv_regime": "HIGH_IV" if atm_iv >= 22 else ("LOW_IV" if atm_iv < 10 else "NORMAL_IV"),
            "spread_pct": live_spread_pct,
            "liquidity_status": "EXCELLENT" if live_spread_pct <= 0.30 else ("GOOD" if live_spread_pct <= 0.50 else "ILLIQUID"),
            "atm_call_change_oi": atm_call_chg, "atm_put_change_oi": atm_put_chg,
            "strikes": rows, "source": "GROWW_LIVE", "timestamp": datetime.now().isoformat()
        }

    """Calculates Option Chain Microstructure, OI Strike Concentration Walls, IV Regime & Liquidity."""
    @staticmethod
    def get_snapshot(spot_price: float = 22600.0, symbol: str = "NIFTY", spot: Optional[float] = None) -> Dict[str, Any]:
        if spot is not None:
            spot_price = spot

        if settings.MARKET_DATA_MODE == "UPSTOX":
            try:
                from upstox_client import upstox_client
                return upstox_client.get_option_chain_snapshot(spot_price, symbol)
            except Exception as e:
                logger.error(f"Upstox live option chain error: {e}")
                if not settings.ALLOW_SYNTHETIC_IN_LIVE:
                    return {
                        "is_valid": False,
                        "error": f"UPSTOX_OPTION_CHAIN_FAILED: {e}",
                        "pcr_ntm": 1.0,
                        "spread_pct": 1.0,
                        "atm_iv": 0.0,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }

        step = 50 if symbol == "NIFTY" else 100
        atm_strike = int(round(spot_price / step) * step)

        strikes_data = []
        ntm_calls_oi = []
        ntm_puts_oi = []
        atm_call_change = 0
        atm_put_change = 0

        max_call_oi = -1
        max_call_wall = atm_strike + 100
        max_put_oi = -1
        max_put_wall = atm_strike - 100

        # Range: ATM +/- 4 strikes for complete concentration walls
        for offset in range(-4, 5):
            strike = atm_strike + (offset * step)
            dist_factor = max(1, abs(offset) + 1)
            call_oi = int(1450000 / dist_factor + random.randint(10000, 75000))
            put_oi = int(1400000 / dist_factor + random.randint(10000, 75000))

            call_oi_change = -random.randint(40000, 120000) if offset <= 0 else random.randint(15000, 70000)
            put_oi_change = random.randint(60000, 180000) if offset <= 0 else -random.randint(10000, 30000)

            if offset == 0:
                atm_call_change = call_oi_change
                atm_put_change = put_oi_change

            if call_oi > max_call_oi:
                max_call_oi = call_oi
                max_call_wall = strike
            if put_oi > max_put_oi:
                max_put_oi = put_oi
                max_put_wall = strike

            # Add to NTM if within +/- 2 strikes
            if abs(offset) <= 2:
                ntm_calls_oi.append(call_oi)
                ntm_puts_oi.append(put_oi)

            strikes_data.append({
                "strike": strike,
                "call_oi": call_oi,
                "call_oi_change": call_oi_change,
                "put_oi": put_oi,
                "put_oi_change": put_oi_change
            })

        total_call_oi = sum(ntm_calls_oi)
        total_put_oi = sum(ntm_puts_oi)
        pcr_ntm = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 1.0

        # IV Regime (Intraday Nifty ATM IV typically 11.5% - 15.5%)
        atm_iv = round(13.2 + random.uniform(-0.6, 0.6), 1)
        if atm_iv < 11.0:
            iv_regime = "LOW_IV"
        elif atm_iv <= 16.0:
            iv_regime = "NORMAL_IV"
        elif atm_iv <= 22.0:
            iv_regime = "HIGH_IV"
        else:
            iv_regime = "EXTREME_IV"

        # 4. Implied Volatility Skew (OTM Put IV - OTM Call IV)
        otm_put_iv = round(atm_iv + 0.8, 2)
        otm_call_iv = round(atm_iv - 0.4, 2)
        iv_skew = round(otm_put_iv - otm_call_iv, 2)

        # 5. Time-Weighted OI Velocity (Change in OI / dt * volume proxy)
        oi_velocity = round((atm_put_change - atm_call_change) * 1.25, 2)

        spread_pct = round(0.22 + random.uniform(0.01, 0.08), 2)
        liquidity_status = "EXCELLENT" if spread_pct <= 0.4 else ("GOOD" if spread_pct <= 0.8 else "ILLIQUID")

        # Zero-Synthetic Invariant in Live Mode:
        if settings.MARKET_DATA_MODE == "GROWW" and not settings.ALLOW_SYNTHETIC_IN_LIVE and not cls._provider:
            return {
                "is_valid": False,
                "error": "LIVE_OPTION_CHAIN_UNAVAILABLE",
                "pcr_ntm": 1.0,
                "spread_pct": 1.0,
                "atm_iv": 0.0,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        return {
            "symbol": symbol,
            "spot_price": spot_price,
            "atm_strike": atm_strike,
            "pcr": pcr_ntm,
            "pcr_ntm": pcr_ntm,
            "max_call_wall": max_call_wall,
            "max_put_wall": max_put_wall,
            "atm_iv": atm_iv,
            "iv_skew": iv_skew,
            "oi_velocity": oi_velocity,
            "iv_regime": iv_regime,
            "spread_pct": spread_pct,
            "liquidity_status": liquidity_status,
            "ntm_calls_oi": ntm_calls_oi,
            "ntm_puts_oi": ntm_puts_oi,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "total_call_oi_change": atm_call_change,
            "total_put_oi_change": atm_put_change,
            "atm_call_change_oi": atm_call_change,
            "atm_put_change_oi": atm_put_change,
            "strikes": strikes_data,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

def classify_oi_microstructure(price_movement: Any, oi_change: int) -> str:
    """
    Classifies derivatives order-flow across 4 institutional quadrants:
    - Price Up + OI Up: LONG_BUILDUP (Aggressive buyers entering)
    - Price Up + OI Down: SHORT_COVERING (Writers covering in panic)
    - Price Down + OI Up: SHORT_BUILDUP (Aggressive writers building resistance)
    - Price Down + OI Down: LONG_LIQUIDATION (Buyers unwinding)
    """
    if isinstance(price_movement, str):
        is_up = (price_movement.upper() == "BULLISH")
    else:
        is_up = (float(price_movement) >= 0)

    if is_up:
        return "LONG_BUILDUP" if oi_change > 0 else "SHORT_COVERING"
    else:
        return "SHORT_BUILDUP" if oi_change > 0 else "LONG_LIQUIDATION"

def evaluate_market_state(tick_data: Dict[str, Any], option_chain: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Evaluates multi-factor facts across 6 structural layers:
    1. Initial Balance / Trading Window
    2. Price Structure & Levels (Day High/Low, ORH/ORL, PDH/PDL)
    3. VWAP & EMA Alignment
    4. Volume Confirmation
    5. Options Microstructure (PCR, OI Walls, Flow)
    6. IV & Spread Liquidity
    Returns: (ready_for_scoring: bool, gate_status: str, facts_payload: dict)
    """
    # 0. Initial Balance (IB) Freeze Filter
    if settings.ENFORCE_MARKET_HOURS and not settings.DRY_RUN:
        now_time = datetime.now().time()
        market_open_safe = datetime.strptime("09:30:00", "%H:%M:%S").time()
        market_close_safe = datetime.strptime("15:15:00", "%H:%M:%S").time()
        if now_time < market_open_safe or now_time > market_close_safe:
            return False, "MARKET_TIME_INACTIVE", {"status": "IB_FREEZE_OR_AFTER_HOURS"}

    spot = tick_data["price"]
    day_high = tick_data["day_high"]
    day_low = tick_data["day_low"]
    pdh = tick_data.get("pdh", day_high + 10)
    pdl = tick_data.get("pdl", day_low - 10)
    orh = tick_data.get("orh", day_high)
    orl = tick_data.get("orl", day_low)
    vwap = tick_data["vwap"]
    atr = tick_data.get("atr", 12.0)
    ema9 = tick_data["ema_9"]
    ema21 = tick_data["ema_21"]
    ema9_slope = tick_data.get("ema9_slope", 0.0)
    curr_vol = tick_data.get("curr_vol", 1200.0)
    avg_vol = tick_data.get("avg_vol", 1000.0)

    # Spatial Proximity: Day High / ORH / PDH for CE or Day Low / ORL / PDL for PE
    atr_pct = (atr / spot) * 100.0 if spot > 0 else 0.05
    dynamic_threshold_pct = min(0.08, max(0.03, 0.5 * atr_pct))

    dist_high_pct = abs(day_high - spot) / day_high * 100.0
    dist_low_pct = abs(spot - day_low) / day_low * 100.0
    dist_orh_pct = abs(orh - spot) / orh * 100.0
    dist_orl_pct = abs(spot - orl) / orl * 100.0

    breakout_up = (dist_high_pct <= dynamic_threshold_pct) or (dist_orh_pct <= dynamic_threshold_pct) or (spot >= orh and spot > vwap)
    breakout_down = (dist_low_pct <= dynamic_threshold_pct) or (dist_orl_pct <= dynamic_threshold_pct) or (spot <= orl and spot < vwap)

    if not (breakout_up or breakout_down):
        return False, "STAGE_1_MONITORING", {"status": "NO_TRIGGER"}

    pcr_ntm = option_chain.get("pcr_ntm", 1.0)
    call_oi_change = option_chain.get("atm_call_change_oi", 0)
    put_oi_change = option_chain.get("atm_put_change_oi", 0)
    max_call_wall = option_chain.get("max_call_wall", int(round((spot+100)/50)*50))
    max_put_wall = option_chain.get("max_put_wall", int(round((spot-100)/50)*50))
    iv_regime = option_chain.get("iv_regime", "NORMAL_IV")
    spread_pct = option_chain.get("spread_pct", 0.3)
    liquidity_status = option_chain.get("liquidity_status", "GOOD")

    spot_momentum = tick_data.get("momentum", "NEUTRAL")
    inv_momentum = "BEARISH" if spot_momentum == "BULLISH" else ("BULLISH" if spot_momentum == "BEARISH" else "NEUTRAL")
    ce_flow = classify_oi_microstructure(spot_momentum, call_oi_change)
    pe_flow = classify_oi_microstructure(inv_momentum, put_oi_change)

    # Long Setup Cascade
    if breakout_up:
        spot_vwap_diff = spot - vwap
        if spot_vwap_diff <= 0:
            return False, "STAGE_2_FAIL_BELOW_VWAP", {"status": "BELOW_VWAP"}
        if spot_vwap_diff > max(25.0, 2.5 * atr):
            return False, "STAGE_2_FAIL_OVEREXTENDED", {"status": "OVEREXTENDED_CHASE"}

        if not (ema9 >= ema21 and ema9_slope >= -0.05):
            return False, "STAGE_2_FAIL_EMA_MOMENTUM", {"status": "WEAK_EMA_SLOPE"}

        if curr_vol < (1.05 * avg_vol):
            return False, "STAGE_2_FAIL_VOLUME", {"status": "LOW_VOLUME"}

        # Invalidation level: Spot below VWAP or 1 ATR below entry
        invalidation_level = round(max(vwap, spot - (1.0 * atr)), 2)

        return True, "QUALITY_SCORING_ACTIVE", {
            "direction": "CE",
            "bias": "BUY_CE",
            "strike": f"{settings.SYMBOL} {option_chain.get('atm_strike', int(round(spot/50)*50))} CE",
            "spot": spot,
            "vwap": round(vwap, 2),
            "atr": round(atr, 2),
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2),
            "ema9_slope": ema9_slope,
            "curr_vol": curr_vol,
            "avg_vol": avg_vol,
            "vol_ratio": round(curr_vol / avg_vol, 2) if avg_vol > 0 else 1.2,
            "day_high": day_high,
            "day_low": day_low,
            "pdh": pdh,
            "pdl": pdl,
            "orh": orh,
            "orl": orl,
            "pcr": round(pcr_ntm, 2),
            "ce_flow": ce_flow,
            "pe_flow": pe_flow,
            "call_oi_change": call_oi_change,
            "put_oi_change": put_oi_change,
            "max_call_wall": max_call_wall,
            "max_put_wall": max_put_wall,
            "atm_iv": option_chain.get("atm_iv", 13.5),
            "iv_regime": iv_regime,
            "spread_pct": spread_pct,
            "liquidity_status": liquidity_status,
            "invalidation_level": invalidation_level,
            "recommended_sl": round(spot - (1.0 * atr), 2),
            "sl_pts": round(1.0 * atr, 1),
            "target_pts": round(2.0 * atr, 1),
            "mtf_trend": tick_data.get("mtf_trend", {}),
            "cvd": tick_data.get("cvd", 0.0)
        }

    # Short Setup Cascade
    if breakout_down:
        spot_vwap_diff = vwap - spot
        if spot_vwap_diff <= 0:
            return False, "STAGE_2_FAIL_ABOVE_VWAP", {"status": "ABOVE_VWAP"}
        if spot_vwap_diff > max(25.0, 2.5 * atr):
            return False, "STAGE_2_FAIL_OVEREXTENDED", {"status": "OVEREXTENDED_CHASE"}

        if not (ema9 <= ema21 and ema9_slope <= 0.05):
            return False, "STAGE_2_FAIL_EMA_MOMENTUM", {"status": "WEAK_EMA_SLOPE"}

        if curr_vol < (1.05 * avg_vol):
            return False, "STAGE_2_FAIL_VOLUME", {"status": "LOW_VOLUME"}

        # Invalidation level: Spot above VWAP or 1 ATR above entry
        invalidation_level = round(min(vwap, spot + (1.0 * atr)), 2)

        return True, "QUALITY_SCORING_ACTIVE", {
            "direction": "PE",
            "bias": "BUY_PE",
            "strike": f"{settings.SYMBOL} {option_chain.get('atm_strike', int(round(spot/50)*50))} PE",
            "spot": spot,
            "vwap": round(vwap, 2),
            "atr": round(atr, 2),
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2),
            "ema9_slope": ema9_slope,
            "curr_vol": curr_vol,
            "avg_vol": avg_vol,
            "vol_ratio": round(curr_vol / avg_vol, 2) if avg_vol > 0 else 1.2,
            "day_high": day_high,
            "day_low": day_low,
            "pdh": pdh,
            "pdl": pdl,
            "orh": orh,
            "orl": orl,
            "pcr": round(pcr_ntm, 2),
            "ce_flow": ce_flow,
            "pe_flow": pe_flow,
            "call_oi_change": call_oi_change,
            "put_oi_change": put_oi_change,
            "max_call_wall": max_call_wall,
            "max_put_wall": max_put_wall,
            "atm_iv": option_chain.get("atm_iv", 13.5),
            "iv_regime": iv_regime,
            "spread_pct": spread_pct,
            "liquidity_status": liquidity_status,
            "invalidation_level": invalidation_level,
            "recommended_sl": round(spot + (1.0 * atr), 2),
            "recommended_target": round(spot - (2.0 * atr), 2),
            "sl_pts": round(1.0 * atr, 1),
            "target_pts": round(2.0 * atr, 1),
            "mtf_trend": tick_data.get("mtf_trend", {}),
            "cvd": tick_data.get("cvd", 0.0)
        }

    return False, "STAGE_FAILED", {"status": "FAILED_CONFLUENCE"}

class MarketFeed:
    def __init__(self, symbol: str = settings.SYMBOL, base_price: float = 22600.0):
        self.symbol = symbol
        self.metrics = TickMetrics(symbol=symbol, base_price=base_price)
        self.listeners: List[Callable[[Dict[str, Any]], Any]] = []
        self._is_running = False
        self._task: Optional[asyncio.Task] = None
        self._trend_direction = 1
        self._live_client = None

    def subscribe(self, listener: Callable[[Dict[str, Any]], Any]):
        if listener not in self.listeners:
            self.listeners.append(listener)

    def unsubscribe(self, listener: Callable[[Dict[str, Any]], Any]):
        if listener in self.listeners:
            self.listeners.remove(listener)

    def get_current_metrics(self) -> Dict[str, Any]:
        return self.metrics.update(self.metrics.current_price)

    async def _emit_tick(self, tick_data: Dict[str, Any]):
        if tick_data.get("tick_count", 0) % 5 == 0:
            record_price_tick(
                symbol=tick_data["symbol"],
                price=tick_data["price"],
                day_high=tick_data["day_high"],
                day_low=tick_data["day_low"],
                ema_9=tick_data["ema_9"],
                ema_21=tick_data["ema_21"],
                vwap=tick_data["vwap"]
            )

        for listener in self.listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(tick_data)
                else:
                    listener(tick_data)
            except Exception as e:
                logger.error(f"Error in tick listener: {e}")

    async def run_simulation_loop(self):
        logger.info(f"Starting Market Feed Tick Engine for {self.symbol}...")
        self._is_running = True

        while self._is_running:
            try:
                if random.random() < 0.05:
                    self._trend_direction = -self._trend_direction

                drift = self._trend_direction * random.uniform(0.3, 1.8)
                noise = random.gauss(0, 1.2)
                price_delta = drift + noise

                new_price = round(self.metrics.current_price + price_delta, 2)
                volume = random.uniform(300, 1800)

                tick_data = self.metrics.update(new_price, volume)
                await self._emit_tick(tick_data)

                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Feed error: {e}")
                await asyncio.sleep(1.0)

    async def run_live_loop(self):
        """Poll live quote and 1m history from Upstox or Groww."""
        if settings.MARKET_DATA_MODE == "UPSTOX":
            from upstox_client import upstox_client
            client = upstox_client
        else:
            from groww_client import live_client
            client = live_client

        try:
            if settings.MARKET_DATA_MODE == "UPSTOX":
                history = await asyncio.to_thread(client.get_intraday_candles, self.symbol)
            else:
                history = await asyncio.to_thread(client.get_historical_candles, self.symbol, "1m", max(50, self.metrics.candle_buffer.maxlen))
            self.metrics.candle_buffer.load_1m_candles(history)
            if history:
                self.metrics.current_price = float(history[-1]["close"])
                logger.info("Loaded %d live 1m candles for %s from %s. Current price: %.2f", len(history), self.symbol, settings.MARKET_DATA_MODE, self.metrics.current_price)
        except Exception as exc:
            logger.warning("Unable to seed live 1m history: %s", exc)

        self._is_running = True
        self._consecutive_feed_errors = 0
        while self._is_running:
            try:
                quote = await asyncio.to_thread(client.get_quote, self.symbol)
                price = float(quote.get("last_price") or quote.get("ltp") or quote.get("close") or 0.0)
                if price <= 0:
                    raise RuntimeError("Live quote did not return last_price")
                volume = float(quote.get("volume") or 0.0)
                tick_data = self.metrics.update(price, volume)
                tick_data["day_open"] = float(quote.get("open") or tick_data["day_open"])
                tick_data["day_high"] = float(quote.get("high") or tick_data["day_high"])
                tick_data["day_low"] = float(quote.get("low") or tick_data["day_low"])
                tick_data["timestamp_raw"] = time.time()
                tick_data["is_feed_healthy"] = True
                self._consecutive_feed_errors = 0
                await self._emit_tick(tick_data)
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._consecutive_feed_errors += 1
                logger.error("%s live feed error (drop #%d): %s", settings.MARKET_DATA_MODE, self._consecutive_feed_errors, exc)
                # Emit emergency stale tick to notify orchestrator of feed drop
                if self._consecutive_feed_errors >= 3:
                    emergency_tick = dict(self.metrics.update(self.metrics.current_price))
                    emergency_tick["is_feed_healthy"] = False
                    emergency_tick["timestamp_raw"] = time.time() - 10.0 # Force stale veto
                    await self._emit_tick(emergency_tick)
                await asyncio.sleep(2.0)

    def start(self):
        if not self._is_running:
            self._is_running = True
            try:
                loop = asyncio.get_running_loop()
                runner = self.run_live_loop if settings.MARKET_DATA_MODE in ("GROWW", "UPSTOX") else self.run_simulation_loop
                self._task = loop.create_task(runner())
            except RuntimeError:
                pass

    def stop(self):
        self._is_running = False
        if self._task:
            self._task.cancel()

market_feed = MarketFeed()

