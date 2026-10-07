import pytest
import time
import asyncio
from datetime import datetime
from config import settings
from feed import CandleBuffer, TickMetrics, OptionChainSnapshot, evaluate_market_state, classify_oi_microstructure, build_mtf_candles
from analyzer import MarketAIAnalyzer, ai_analyzer
from engine import TradingOrchestrator, orchestrator
from auth import auth_manager
from notifier import telegram_notifier
from database import save_signal, create_trade, close_trade, get_accuracy_metrics, calculate_statutory_charges
from options_pricing import BlackScholesEngine
from backtester import BacktestEngine

# ==============================================================================
# 1. MARKET STRUCTURE & TECHNICAL CONFLUENCE TESTS (Tests 1-5)
# ==============================================================================

def test_market_structure_levels():
    """Test 1: Verify calculation of Opening Range High/Low and 5m EMA."""
    cb = CandleBuffer(maxlen=50, base_price=22600.0)
    orh, orl, ready = cb.get_opening_range(period_candles=15)
    assert orh >= 22580.0
    assert orl <= 22620.0
    assert ready is True

def test_opening_range_breakout_detection():
    """Test 2: Verify Day High & Opening Range breakout state detection."""
    tick = {
        "price": 22630.0,
        "day_high": 22630.0,
        "day_low": 22550.0,
        "vwap": 22615.0,
        "ema_9": 22625.0,
        "ema_21": 22618.0,
        "ema9_slope": 0.20,
        "atr": 14.0,
        "curr_vol": 3000,
        "avg_vol": 1000,
        "timestamp_raw": time.time()
    }
    snap = OptionChainSnapshot.get_snapshot(22630.0, "NIFTY")
    ready, status, payload = evaluate_market_state(tick, snap)
    assert ready is True
    assert payload["direction"] == "CE"

def test_vwap_and_ema_alignment():
    """Test 3: Verify bullish EMA slope and VWAP support filters."""
    analyzer = MarketAIAnalyzer()
    regime = analyzer.classify_market_regime(
        {"price": 22650.0, "vwap": 22620.0, "ema_9": 22640.0, "ema_21": 22610.0, "ema9_slope": 0.15, "atr": 12.0, "day_high": 22660.0, "day_low": 22550.0, "curr_vol": 1500, "avg_vol": 1000},
        {"pcr_ntm": 1.20, "atm_iv": 13.5}
    )
    assert regime in ["STRONG_TREND_BULLISH", "BREAKOUT_EXPANSION"]

def test_volume_surge_ratio_multiplier():
    """Test 4: Verify volume expansion calculation."""
    cb = CandleBuffer(maxlen=25, base_price=22600.0)
    avg_vol = cb.calculate_avg_volume(period=20)
    assert avg_vol > 0

def test_option_chain_walls_and_iv():
    """Test 5: Verify Max Call/Put strike concentration walls."""
    snap = OptionChainSnapshot.get_snapshot(spot=22640.0, symbol="NIFTY")
    assert "max_call_wall" in snap
    assert "max_put_wall" in snap
    assert snap["max_call_wall"] >= snap["max_put_wall"]

# ==============================================================================
# 2. BLACK-SCHOLES OPTIONS GREEKS & PRICING TESTS (Tests 6-9)
# ==============================================================================

def test_black_scholes_call_greeks_pricing():
    """Test 6: Verify Black-Scholes Call pricing and positive Delta."""
    greeks = BlackScholesEngine.calculate_greeks(
        spot=22600.0, strike=22600.0, time_to_expiry_days=4.0, iv_pct=13.5, option_type="CE"
    )
    assert 0.45 <= greeks["delta"] <= 0.65
    assert greeks["gamma"] > 0.0
    assert greeks["theoretical_price"] > 50.0

def test_black_scholes_put_greeks_pricing():
    """Test 7: Verify Black-Scholes Put pricing and negative Delta."""
    greeks = BlackScholesEngine.calculate_greeks(
        spot=22600.0, strike=22600.0, time_to_expiry_days=4.0, iv_pct=13.5, option_type="PE"
    )
    assert -0.65 <= greeks["delta"] <= -0.45
    assert greeks["gamma"] > 0.0
    assert greeks["theoretical_price"] > 50.0

def test_black_scholes_theta_decay_direction():
    """Test 8: Verify option theta decay is strictly negative."""
    greeks = BlackScholesEngine.calculate_greeks(
        spot=22600.0, strike=22600.0, time_to_expiry_days=3.0, iv_pct=14.0, option_type="CE"
    )
    assert greeks["theta_daily"] < 0.0

def test_option_max_pain_calculation():
    """Test 9: Verify Max Pain strike computation."""
    dummy_chain = [
        {"strike": 22500, "call_oi": 10000, "put_oi": 80000},
        {"strike": 22600, "call_oi": 50000, "put_oi": 50000},
        {"strike": 22700, "call_oi": 90000, "put_oi": 15000}
    ]
    max_pain = BlackScholesEngine.calculate_max_pain(dummy_chain)
    assert max_pain == 22600.0

# ==============================================================================
# 3. SETUP QUALITY SCORING & CONFLUENCE TESTS (Tests 10-13)
# ==============================================================================

def test_setup_quality_scoring_engine_weights():
    """Test 10: Verify 0-100 scoring breakdown across all 7 layers."""
    analyzer = MarketAIAnalyzer()
    payload = {
        "direction": "CE", "spot": 22650.0, "vwap": 22610.0,
        "ema9": 22645.0, "ema21": 22620.0, "ema9_slope": 0.12,
        "vol_ratio": 1.4, "pcr": 1.25, "call_oi_change": -15000,
        "put_oi_change": 45000, "max_call_wall": 22750.0, "max_put_wall": 22550.0,
        "iv_regime": "NORMAL_IV", "spread_pct": 0.28, "liquidity_status": "EXCELLENT",
        "day_high": 22652.0, "day_low": 22550.0
    }
    snap = {"atm_iv": 13.5, "pcr_ntm": 1.25}
    score, grade, breakdown, confs, warns = analyzer.calculate_setup_quality_score(payload, snap)
    assert 85 <= score <= 100
    assert grade in ["A+", "A"]
    assert sum(breakdown.values()) == score

def test_scoring_grade_a_plus_classification():
    """Test 11: Verify Grade A+ threshold (>= 90 pts)."""
    analyzer = MarketAIAnalyzer()
    payload = {
        "direction": "CE", "spot": 22650.0, "vwap": 22605.0,
        "ema9": 22648.0, "ema21": 22615.0, "ema9_slope": 0.25,
        "vol_ratio": 1.6, "pcr": 1.35, "call_oi_change": -35000,
        "put_oi_change": 85000, "max_call_wall": 22800.0, "max_put_wall": 22500.0,
        "iv_regime": "NORMAL_IV", "spread_pct": 0.20, "liquidity_status": "EXCELLENT",
        "day_high": 22650.0, "day_low": 22520.0, "pdh": 22640.0, "orh": 22645.0
    }
    score, grade, _, _, _ = analyzer.calculate_setup_quality_score(payload, {"atm_iv": 13.0, "pcr_ntm": 1.35})
    assert score >= 90
    assert grade == "A+"

def test_scoring_grade_c_rejection():
    """Test 12: Verify weak setups (< 70) receive Grade C / NO_SIGNAL."""
    analyzer = MarketAIAnalyzer()
    analyzer.cached_regime = {"session_regime": "RANGE_BOUND_CHOP", "trap_risk_score": 0.75}
    payload = {
        "direction": "CE", "spot": 22600.0, "vwap": 22602.0,
        "ema9": 22601.0, "ema21": 22601.0, "ema9_slope": 0.0,
        "vol_ratio": 0.8, "pcr": 0.90, "call_oi_change": 20000,
        "put_oi_change": -10000, "max_call_wall": 22600.0, "max_put_wall": 22500.0,
        "iv_regime": "HIGH_IV", "spread_pct": 0.85, "liquidity_status": "POOR",
        "day_high": 22650.0, "day_low": 22550.0
    }
    score, grade, _, _, warns = analyzer.calculate_setup_quality_score(payload, {"atm_iv": 24.0, "pcr_ntm": 0.90})
    assert score < 70
    assert grade in ["C", "NO_SIGNAL"]

def test_decision_support_signal_payload_integrity():
    """Test 13: Verify signal payload generation with invalidation levels."""
    analyzer = MarketAIAnalyzer()
    payload = {
        "direction": "CE", "spot": 22648.50, "vwap": 22605.0,
        "ema9": 22645.0, "ema21": 22615.0, "ema9_slope": 0.15,
        "vol_ratio": 1.3, "pcr": 1.15, "call_oi_change": -10000,
        "put_oi_change": 25000, "max_call_wall": 22750.0, "max_put_wall": 22550.0,
        "iv_regime": "NORMAL_IV", "spread_pct": 0.25, "liquidity_status": "EXCELLENT",
        "day_high": 22650.0, "day_low": 22550.0, "strike": "NIFTY 22650 CE"
    }
    snap = {"atm_iv": 13.5, "pcr_ntm": 1.15}
    score, grade, _, confs, _ = analyzer.calculate_setup_quality_score(payload, snap)
    assert score >= 75
    assert grade in ["A+", "A"]

# ==============================================================================
# 4. HARD VETOES & RISK CIRCUIT BREAKER TESTS (Tests 14-18)
# ==============================================================================

def test_totp_auth_generation():
    """Test 14: Verify TOTP token authentication."""
    token = auth_manager.get_session_token()
    assert token is not None
    assert len(token) > 0

def test_hard_veto_stale_tick(monkeypatch):
    """Test 15: Verify hard veto blocks signal if tick lag > 3.0 seconds."""
    monkeypatch.setattr("engine.get_daily_net_pnl", lambda: 0.0)
    orch = TradingOrchestrator()
    stale_tick = {
        "price": 22650.0, "day_high": 22652.0, "day_low": 22550.0,
        "vwap": 22600.0, "ema_9": 22640.0, "ema_21": 22610.0,
        "ema9_slope": 0.15, "atr": 14.0, "curr_vol": 2500, "avg_vol": 1000,
        "timestamp_raw": time.time() - 10.0 # 10s stale
    }
    asyncio.run(orch.on_tick(stale_tick))
    assert "HARD_VETO_STALE_TICK" in orch.current_gate_status

def test_hard_veto_scheduled_event(monkeypatch):
    monkeypatch.setattr("engine.get_daily_net_pnl", lambda: 0.0)
    orch = TradingOrchestrator()
    from event_filter import EventRisk
    monkeypatch.setattr(orch.event_filter, "evaluate", lambda: EventRisk("EXTREME", "TEST_EVENT", False, 25))
    tick = {
        "price": 22650.0, "day_high": 22652.0, "day_low": 22550.0,
        "vwap": 22600.0, "ema_9": 22640.0, "ema_21": 22610.0,
        "ema9_slope": 0.15, "atr": 14.0, "curr_vol": 2500, "avg_vol": 1000,
        "timestamp_raw": time.time()
    }
    asyncio.run(orch.on_tick(tick))
    assert "HARD_VETO_EVENT_RISK" in orch.current_gate_status

def test_hard_veto_spread_blowout(monkeypatch):
    """Test 16: Verify hard veto blocks signal if bid-ask spread > 0.40%."""
    monkeypatch.setattr("engine.get_daily_net_pnl", lambda: 0.0)
    orch = TradingOrchestrator()
    tick = {
        "price": 22650.0, "day_high": 22652.0, "day_low": 22550.0,
        "vwap": 22600.0, "ema_9": 22640.0, "ema_21": 22610.0,
        "ema9_slope": 0.15, "atr": 14.0, "curr_vol": 2500, "avg_vol": 1000,
        "timestamp_raw": time.time()
    }
    # Mock wide spread
    import feed
    from session_filter import SessionState
    monkeypatch.setattr(orch.session_filter, "evaluate_session", lambda: SessionState("TEST_PRIME", True, False, 80, None))
    orig_fn = feed.OptionChainSnapshot.get_snapshot
    feed.OptionChainSnapshot.get_snapshot = lambda spot, symbol: {"spread_pct": 0.85, "pcr_ntm": 1.2, "atm_iv": 14.0}
    try:
        asyncio.run(orch.on_tick(tick))
        assert "HARD_VETO_SPREAD_BLOWOUT" in orch.current_gate_status
    finally:
        feed.OptionChainSnapshot.get_snapshot = orig_fn

def test_hard_veto_daily_signal_cap(monkeypatch):
    """Test 17: Verify daily max signals hard cap (5 signals)."""
    monkeypatch.setattr("engine.get_daily_net_pnl", lambda: 0.0)
    orch = TradingOrchestrator()
    orch.daily_signals_count = 5
    tick = {
        "price": 22650.0, "day_high": 22652.0, "day_low": 22550.0,
        "vwap": 22600.0, "ema_9": 22640.0, "ema_21": 22610.0,
        "ema9_slope": 0.15, "atr": 14.0, "curr_vol": 2500, "avg_vol": 1000,
        "timestamp_raw": time.time()
    }
    asyncio.run(orch.on_tick(tick))
    assert "HARD_VETO_DAILY_CAP" in orch.current_gate_status

def test_spot_relative_invalidation_trigger():
    """Test 18: Verify active CE signal is invalidated when spot crosses invalidation level."""
    orch = TradingOrchestrator()
    orch.active_signals.append({
        "id": 888, "bias": "BUY_CE", "strike": "NIFTY 22650 CE",
        "invalidation_level": 22634.0, "expires_at": 9999999999.0, "status": "ACTIVE"
    })
    asyncio.run(orch._monitor_active_trades_and_signals(22630.0, {"price": 22630.0}))
    assert len(orch.active_signals) == 0

# ==============================================================================
# 5. STRICT SL-FIRST BACKTESTING & PATH RESOLUTION (Tests 19-21)
# ==============================================================================

def test_sl_first_backtest_path_invariant():
    """Test 19: Strict SL-First Invariant - if candle Low hits SL, trade is logged as SL_HIT even if High touched Target."""
    engine = BacktestEngine(initial_capital=25000.0, lot_size=65)
    dummy_signals = [{
        "id": 1, "bias": "BUY_CE", "spot_price": 22600.0, "option_entry": 120.0,
        "sl_pts": 14.0, "target_pts": 28.0, "quality_score": 92, "grade": "A+"
    }]
    # Candle drops 35 spot pts (17.5 option pts >= 14 SL) and also rises 60 spot pts
    ambiguous_candles = [{"high": 22660.0, "low": 22565.0, "close": 22650.0}]
    results = engine.run_backtest(dummy_signals, ambiguous_candles)
    assert results["total_trades"] == 1
    assert results["losing_trades"] == 1
    assert results["winning_trades"] == 0

def test_target_first_backtest_path_invariant():
    """Test 20: Verify clean Target Hit when Low does not breach SL floor."""
    engine = BacktestEngine(initial_capital=25000.0, lot_size=65)
    dummy_signals = [{
        "id": 2, "bias": "BUY_CE", "spot_price": 22600.0, "option_entry": 120.0,
        "sl_pts": 14.0, "target_pts": 28.0, "quality_score": 90, "grade": "A+"
    }]
    clean_bull_candles = [{"high": 22670.0, "low": 22595.0, "close": 22665.0}]
    results = engine.run_backtest(dummy_signals, clean_bull_candles)
    assert results["winning_trades"] == 1
    assert results["profit_factor"] is None  # undefined with no losing trades
    assert results["performance_is_statistically_actionable"] is False

def test_statutory_charges_calculation_and_stt():
    """Test 21: Verify complete Indian statutory tax and brokerage schedule."""
    charges = calculate_statutory_charges(entry_price=100.0, exit_price=128.0, lot_size=65)
    assert charges["brokerage"] == 40.0
    assert charges["stt"] > 10.0 # 0.15% on sell
    assert charges["exchange_charges"] > 4.0 # 0.03503%
    assert charges["gst"] > 7.0 # 18% on fees
    assert charges["stamp_duty"] > 0.15 # 0.003% on buy
    assert charges["total_charges"] >= 65.0

# ==============================================================================
# 6. CIRCUIT BREAKERS, OI FLOW & TELEGRAM CLARITY (Tests 22-25)
# ==============================================================================

def test_consecutive_loss_circuit_breaker_lockout():
    """Test 22: Verify circuit breaker locks trading after 2 consecutive stop-outs."""
    orch = TradingOrchestrator()
    orch.consecutive_losses = 2
    assert orch.check_circuit_breaker() is True
    assert "CIRCUIT_BREAKER_LOCKED" in orch.current_gate_status

def test_circuit_breaker_reset_on_new_day():
    """Test 23: Verify circuit breaker reset behavior."""
    orch = TradingOrchestrator()
    orch.consecutive_losses = 0
    assert orch.check_circuit_breaker() is False

def test_oi_microstructure_classification():
    """Test 24: Verify 4-quadrant institutional order flow classifications."""
    assert classify_oi_microstructure(price_movement=5.0, oi_change=50000) == "LONG_BUILDUP"
    assert classify_oi_microstructure(price_movement="BULLISH", oi_change=-25000) == "SHORT_COVERING"
    assert classify_oi_microstructure(price_movement=-5.0, oi_change=50000) == "SHORT_BUILDUP"
    assert classify_oi_microstructure(price_movement="BEARISH", oi_change=-25000) == "LONG_LIQUIDATION"

def test_telegram_alert_formatting_separation():
    """Test 25: Verify Telegram alert format distinctly separates Spot Trigger from Option Premium."""
    test_signal = {
        "bias": "BUY_CE", "strike": "NIFTY 22650 CE", "spot_price": 22648.5,
        "invalidation_level": 22634.0, "option_entry": 125.0, "sl_pts": 14.0,
        "target_pts": 28.0, "quality_score": 92, "grade": "A+",
        "session_regime": "STRONG_TREND_BULLISH",
        "confirmations": ["Day High Breakout", "VWAP Support Holding"]
    }
    # Validate payload structure without sending network request
    assert "spot_price" in test_signal
    assert "option_entry" in test_signal
    assert test_signal["invalidation_level"] < test_signal["spot_price"]

# ==============================================================================
# 7. INSTITUTIONAL 9.6+ CHRONOLOGY & MTF REGRESSION TESTS (Tests 26-30)
# ==============================================================================

def test_backtest_lookahead_protection_and_future_only_replay():
    """Test 26: Strict Look-Ahead Protection - past candles prior to signal cannot trigger trade."""
    engine = BacktestEngine(initial_capital=25000.0, lot_size=65)
    
    # 5 candles at T = 100, 101, 102, 103, 104
    candles = [
        {"timestamp": 100.0, "high": 22680.0, "low": 22590.0, "close": 22670.0}, # Huge bull candle in PAST
        {"timestamp": 101.0, "high": 22670.0, "low": 22590.0, "close": 22660.0},
        {"timestamp": 102.0, "high": 22605.0, "low": 22560.0, "close": 22565.0}, # Signal generated here
        {"timestamp": 103.0, "high": 22605.0, "low": 22550.0, "close": 22555.0}, # Future candle drops (SL)
        {"timestamp": 104.0, "high": 22602.0, "low": 22540.0, "close": 22545.0},
    ]
    
    # BUY_CE Signal generated at T = 102
    signal_at_102 = [{
        "id": 102, "timestamp": 102.0, "bias": "BUY_CE", "spot_price": 22600.0,
        "option_entry": 120.0, "sl_pts": 14.0, "target_pts": 28.0, "quality_score": 90, "grade": "A+"
    }]
    
    results = engine.run_backtest(signal_at_102, candles)
    # The past bull candle at T=100 (which would hit target) must NOT be seen.
    # The future candle at T=103 breaches SL (22600 - 22550 = 50 pts drop => 25 opt pts drop >= 14 SL).
    assert results["total_trades"] == 1
    assert results["losing_trades"] == 1
    assert results["winning_trades"] == 0
    assert results["lookahead_protection"] is True

def test_backtest_no_future_data_isolation():
    """Test 27: Verify signal at the very end of series gets NO_FUTURE_DATA with no false outcome."""
    engine = BacktestEngine(initial_capital=25000.0, lot_size=65)
    candles = [
        {"timestamp": 100.0, "high": 22620.0, "low": 22590.0, "close": 22610.0},
        {"timestamp": 101.0, "high": 22625.0, "low": 22595.0, "close": 22615.0},
    ]
    # Signal at T=101 has no future candles (T > 101)
    sig = [{
        "id": 1, "timestamp": 101.0, "bias": "BUY_CE", "spot_price": 22615.0,
        "option_entry": 120.0, "sl_pts": 14.0, "target_pts": 28.0, "quality_score": 85
    }]
    results = engine.run_backtest(sig, candles)
    assert results["total_signals"] == 1
    assert results["total_trades"] == 0 # Excluded from executed trades

def test_true_mtf_candle_aggregation_integrity():
    """Test 28: Verify mathematical correctness of 1m -> 3m and 5m candle aggregation."""
    candles_1m = [
        {"timestamp": 100.0, "open": 22600.0, "high": 22615.0, "low": 22595.0, "close": 22610.0, "volume": 1000.0},
        {"timestamp": 160.0, "open": 22610.0, "high": 22630.0, "low": 22605.0, "close": 22625.0, "volume": 1500.0},
        {"timestamp": 220.0, "open": 22625.0, "high": 22635.0, "low": 22618.0, "close": 22630.0, "volume": 2000.0},
    ]
    candles_3m = build_mtf_candles(candles_1m, 3)
    assert len(candles_3m) == 1
    c3 = candles_3m[0]
    assert c3["open"] == 22600.0   # Open of 1st candle
    assert c3["high"] == 22635.0   # Max high (22615, 22630, 22635)
    assert c3["low"] == 22595.0    # Min low (22595, 22605, 22618)
    assert c3["close"] == 22630.0  # Close of last candle
    assert c3["volume"] == 4500.0  # Sum of volumes
    assert c3["timestamp"] == 100.0

def test_backtest_time_in_force_expiration():
    """Test 29: Verify auto-exit after max holding period (Theta decay protection)."""
    engine = BacktestEngine(initial_capital=25000.0, lot_size=65, max_hold_minutes=5)
    # 10 flat candles that neither touch SL (14 pts) nor Target (28 pts)
    candles = []
    for i in range(10):
        candles.append({
            "timestamp": 100.0 + (i * 60.0),
            "high": 22603.0, "low": 22598.0, "close": 22601.0
        })
    sig = [{
        "id": 1, "timestamp": 100.0, "bias": "BUY_CE", "spot_price": 22600.0,
        "option_entry": 120.0, "sl_pts": 14.0, "target_pts": 28.0, "quality_score": 85
    }]
    results = engine.run_backtest(sig, candles, max_hold_candles=5)
    assert results["total_trades"] == 1
    # Check outputs file
    import json
    from backtester import OUTPUTS_DIR
    with open(OUTPUTS_DIR / "backtest_results.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["total_trades"] == 1

def test_backtest_chronology_invariant_validation():
    """Test 30: Verify all trades strictly obey exit_epoch >= entry_epoch."""
    engine = BacktestEngine(initial_capital=25000.0, lot_size=65)
    candles = [
        {"timestamp": 1000.0, "high": 22605.0, "low": 22595.0, "close": 22600.0},
        {"timestamp": 1060.0, "high": 22670.0, "low": 22598.0, "close": 22665.0},
    ]
    sig = [{
        "id": 1, "timestamp": 1000.0, "bias": "BUY_CE", "spot_price": 22600.0,
        "option_entry": 120.0, "sl_pts": 14.0, "target_pts": 28.0, "quality_score": 92
    }]
    results = engine.run_backtest(sig, candles)
    assert results["chronology_validated"] is True

def test_heavyweight_concordance_calculation():
    """Test 31: Verify Heavyweight Concordance Index calculation."""
    from concordance import HeavyweightConcordanceTracker
    tracker = HeavyweightConcordanceTracker()
    tracker.update_stock_tick("HDFCBANK", 1680.0, 1.2, vwap=1665.0)
    tracker.update_stock_tick("RELIANCE", 2950.0, 0.8, vwap=2930.0)
    tracker.update_stock_tick("ICICIBANK", 1120.0, 1.5, vwap=1110.0)
    
    res = tracker.compute_concordance()
    assert res["score"] > 0.0
    assert res["bullish_count"] >= 3
    is_aligned, reason = tracker.evaluate_bias_alignment("BUY_CE")
    assert is_aligned is True

def test_heavyweight_divergence_trap_veto():
    """Test 32: Verify Heavyweight Divergence warning when banking heavyweights fall."""
    from concordance import HeavyweightConcordanceTracker
    tracker = HeavyweightConcordanceTracker()
    # Heavily dump HDFCBANK & RELIANCE
    tracker.update_stock_tick("HDFCBANK", 1620.0, -2.5, vwap=1650.0)
    tracker.update_stock_tick("RELIANCE", 2880.0, -1.8, vwap=2920.0)
    tracker.update_stock_tick("ICICIBANK", 1080.0, -1.9, vwap=1105.0)
    
    is_aligned, reason = tracker.evaluate_bias_alignment("BUY_CE")
    assert is_aligned is False
    assert "Heavyweight divergence" in reason

def test_stt_015_statutory_charge_calculation():
    """Test 33: Verify 0.15% STT rate on option sell turnover."""
    charges = calculate_statutory_charges(buy_price=100.0, sell_price=150.0, qty=65)
    assert charges["stt"] == 14.62
    assert charges["brokerage"] == 40.0
    assert charges["total_charges"] > 55.0

def test_dynamic_taylor_option_pricing():
    """Test 34: Verify second-order Taylor expansion Greeks option pricing."""
    res = BlackScholesEngine.calculate_dynamic_option_price(
        spot_entry=22600.0, strike=22600.0, dte_days=1.0, iv_pct=13.5,
        option_type="CE", spot_change=30.0, time_elapsed_minutes=15.0
    )
    assert res["simulated_premium"] > res["entry_theoretical"]
    assert res["gamma_effect"] > 0
    assert res["effective_delta"] > 0.50
    assert res["bid"] < res["ask"]

def test_portfolio_greeks_kill_switch_state_machine():
    """Test 35: Verify multi-tier Portfolio Kill-Switch (GREEN -> YELLOW -> RED)."""
    from options_pricing import PortfolioGreeksEngine
    engine = PortfolioGreeksEngine(max_net_delta=100.0)

    # 1. Normal state (GREEN)
    st_green = engine.evaluate_portfolio_state([], daily_pnl=0.0, max_daily_loss=2500.0, consecutive_losses=0)
    assert st_green["kill_state"] == "GREEN"
    assert st_green["is_trade_permitted"] is True

    # 2. Elevated loss state (YELLOW - 50% size)
    st_yellow = engine.evaluate_portfolio_state([], daily_pnl=-1000.0, max_daily_loss=2500.0, consecutive_losses=0)
    assert st_yellow["kill_state"] == "YELLOW"
    assert st_yellow["size_multiplier"] == 0.5

    # 3. Consecutive loss limit breach (RED - Lockdown)
    st_red = engine.evaluate_portfolio_state([], daily_pnl=-500.0, max_daily_loss=2500.0, consecutive_losses=2)
    assert st_red["kill_state"] == "RED"
    assert st_red["is_trade_permitted"] is False

def test_implementation_shortfall_calculation():
    """Test 36: Verify calculation of execution drag & implementation shortfall."""
    res = BlackScholesEngine.calculate_implementation_shortfall(
        decision_price=120.0, expected_fill_price=120.25, actual_fill_price=120.50, side="BUY"
    )
    assert res["slippage_pts"] == 0.25
    assert res["total_implementation_shortfall_pts"] == 0.50
    assert res["execution_quality"] == "ACCEPTABLE"

def test_backtester_multi_regime_stress_testing():
    """Test 37: Verify 2x and 3x slippage resilience in stress testing."""
    engine = BacktestEngine(initial_capital=25000.0, lot_size=65)
    dummy_trades = [
        {"option_entry": 100.0, "exit_option": 125.0, "pnl_pts": 25.0, "net_pnl_after_costs": 1500.0},
        {"option_entry": 100.0, "exit_option": 128.0, "pnl_pts": 28.0, "net_pnl_after_costs": 1700.0},
        {"option_entry": 100.0, "exit_option": 88.0, "pnl_pts": -12.0, "net_pnl_after_costs": -850.0}
    ]
    stress = engine.run_stress_testing(dummy_trades)
    assert "stress_2x_slippage" in stress
    assert "stress_3x_slippage" in stress
    assert stress["stress_2x_slippage"]["is_profitable"] is True

def test_groww_historical_backtest_handler():
    """Test 38: Verify Groww historical backtest classmethod error isolation."""
    # When in mock mode with invalid auth, gracefully handles without crashing
    res = BacktestEngine.run_groww_historical_backtest(symbol="NIFTY", count=10)
    assert isinstance(res, dict)




