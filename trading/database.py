import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from config import settings

from pathlib import Path

def get_db_connection():
    db_path = Path(settings.DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Signals table: records every AI-evaluated or generated trade signal with quality score & invalidation
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            spot_price REAL NOT NULL,
            day_high REAL NOT NULL,
            day_low REAL NOT NULL,
            bias TEXT NOT NULL, -- BUY_CE, BUY_PE, NO_TRADE
            strike TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            target REAL NOT NULL,
            sl_pts REAL NOT NULL,
            target_pts REAL NOT NULL,
            confidence_pct REAL NOT NULL,
            quality_score INTEGER DEFAULT 80,
            grade TEXT DEFAULT 'A',
            invalidation_level REAL,
            expires_at REAL,
            factor_breakdown TEXT,
            confirmations TEXT,
            warnings TEXT,
            estimated_pnl_pts REAL NOT NULL,
            reasoning TEXT NOT NULL,
            oi_context TEXT,
            status TEXT DEFAULT 'TRIGGERED', -- TRIGGERED, ACTIVE, INVALIDATED, EXPIRED, TARGET_HIT, SL_HIT
            is_dry_run INTEGER DEFAULT 1,
            scalper_link TEXT
        )
    """)

    # Try adding new columns if upgrading existing database
    new_cols = [
        ("quality_score", "INTEGER DEFAULT 80"),
        ("grade", "TEXT DEFAULT 'A'"),
        ("invalidation_level", "REAL"),
        ("expires_at", "REAL"),
        ("factor_breakdown", "TEXT"),
        ("confirmations", "TEXT"),
        ("warnings", "TEXT")
    ]
    for col_name, col_type in new_cols:
        try:
            cursor.execute(f"ALTER TABLE signals ADD COLUMN {col_name} {col_type}")
        except Exception as migration_err:
            # Column already exists is expected; log other errors explicitly
            if "duplicate column" not in str(migration_err).lower():
                import logging
                logging.getLogger("database").debug(f"Migration note for {col_name}: {migration_err}")

    # Executed trades table (tracks manual or paper execution and real outcome PnL)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strike TEXT NOT NULL,
            action TEXT NOT NULL, -- BUY_CE, BUY_PE
            lot_size INTEGER NOT NULL,
            lots INTEGER DEFAULT 1,
            underlying_entry REAL NOT NULL,   -- NIFTY spot price at entry (index value)
            underlying_exit REAL,              -- NIFTY spot price at exit
            underlying_sl REAL NOT NULL,       -- Spot-level Stop Loss
            underlying_target REAL NOT NULL,   -- Spot-level Target
            entry_price REAL NOT NULL,         -- Option PREMIUM at entry (e.g. Rs 120)
            exit_price REAL,                   -- Option PREMIUM at exit
            stop_loss REAL NOT NULL,           -- Option premium Stop Loss
            target REAL NOT NULL,              -- Option premium Target
            sl_pts REAL DEFAULT 14.0,          -- SL in premium points
            target_pts REAL DEFAULT 28.0,      -- Target in premium points
            pnl_pts REAL DEFAULT 0.0,
            pnl_amount REAL DEFAULT 0.0,
            outcome TEXT DEFAULT 'OPEN', -- OPEN, TARGET_HIT, SL_HIT, MANUAL_EXIT
            is_paper INTEGER DEFAULT 1,
            exit_time TEXT,
            FOREIGN KEY (signal_id) REFERENCES signals (id)
        )
    """)
    # Migration: add new columns to existing DBs
    new_trade_cols = [
        ("underlying_entry", "REAL DEFAULT 0.0"),
        ("underlying_exit", "REAL"),
        ("underlying_sl", "REAL DEFAULT 0.0"),
        ("underlying_target", "REAL DEFAULT 0.0"),
        ("sl_pts", "REAL DEFAULT 14.0"),
        ("target_pts", "REAL DEFAULT 28.0"),
    ]
    for col_name, col_type in new_trade_cols:
        try:
            cursor.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass

    # Price ticks snapshot cache (optional for charting)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            day_high REAL NOT NULL,
            day_low REAL NOT NULL,
            ema_9 REAL,
            ema_21 REAL,
            vwap REAL
        )
    """)

    conn.commit()
    # Enable foreign key enforcement
    cursor.execute("PRAGMA foreign_keys = ON")
    # Performance indexes on frequently-queried columns
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_strike_bias ON signals(strike, bias)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_outcome ON trades(outcome)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON trades(exit_time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_history_symbol ON price_history(symbol, timestamp)")
    conn.commit()
    conn.close()

def save_signal(signal_data: Dict[str, Any]) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()

    # Prevent duplicate signals for the same strike within the last 60 seconds
    strike = signal_data.get("strike", "")
    bias = signal_data.get("bias", "NO_TRADE")
    cursor.execute("""
        SELECT id FROM signals 
        WHERE strike = ? AND bias = ? AND timestamp >= datetime('now', '-60 seconds')
        ORDER BY id DESC LIMIT 1
    """, (strike, bias))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        return existing["id"]

    quality = signal_data.get("quality_score", int(signal_data.get("confidence_pct", 80)))
    grade = signal_data.get("grade", "A")

    cursor.execute("""
        INSERT INTO signals (
            timestamp, symbol, spot_price, day_high, day_low, bias,
            strike, entry_price, stop_loss, target, sl_pts, target_pts,
            confidence_pct, quality_score, grade, invalidation_level, expires_at,
            factor_breakdown, confirmations, warnings,
            estimated_pnl_pts, reasoning, oi_context,
            status, is_dry_run, scalper_link
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        signal_data.get("timestamp", datetime.now().isoformat()),
        signal_data.get("symbol", settings.SYMBOL),
        signal_data.get("spot_price", 0.0),
        signal_data.get("day_high", 0.0),
        signal_data.get("day_low", 0.0),
        signal_data.get("bias", "NO_TRADE"),
        strike,
        signal_data.get("entry_price", 0.0),
        signal_data.get("stop_loss", 0.0),
        signal_data.get("target", 0.0),
        signal_data.get("sl_pts", settings.DEFAULT_STOP_LOSS_PTS),
        signal_data.get("target_pts", settings.DEFAULT_TARGET_PTS),
        signal_data.get("confidence_pct", float(quality)),
        quality,
        grade,
        signal_data.get("invalidation_level"),
        signal_data.get("expires_at"),
        json.dumps(signal_data.get("breakdown", {})),
        json.dumps(signal_data.get("confirmations", [])),
        json.dumps(signal_data.get("warnings", [])),
        signal_data.get("estimated_pnl_pts", 0.0),
        signal_data.get("reasoning", ""),
        json.dumps(signal_data.get("oi_context", {})),
        signal_data.get("status", "TRIGGERED"),
        1 if signal_data.get("is_dry_run", True) else 0,
        signal_data.get("scalper_link", "")
    ))
    signal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return signal_id

def update_signal_status(signal_id: int, status: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE signals SET status = ? WHERE id = ?", (status, signal_id))
    conn.commit()
    conn.close()

def create_trade(signal_id: Optional[int], strike: str, action: str,
                 entry_price: float,          # Option PREMIUM at entry (e.g. Rs 120.50)
                 stop_loss: float,            # Option PREMIUM Stop Loss
                 target: float,               # Option PREMIUM Target
                 is_paper: bool = True, lots: int = 1,
                 underlying_entry: float = 0.0,   # NIFTY spot price at entry
                 underlying_sl: float = 0.0,       # Spot-level SL
                 underlying_target: float = 0.0,   # Spot-level Target
                 sl_pts: float = 14.0,
                 target_pts: float = 28.0) -> int:
    """
    Creates a new trade record with SEPARATE option-premium and underlying-spot prices.
    CRITICAL: entry_price must be the OPTION PREMIUM (e.g. Rs 120), NOT the spot index value.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO trades (
            signal_id, timestamp, symbol, strike, action, lot_size, lots,
            underlying_entry, underlying_sl, underlying_target,
            entry_price, stop_loss, target, sl_pts, target_pts,
            outcome, is_paper
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
    """, (
        signal_id,
        datetime.now().isoformat(),
        settings.SYMBOL,
        strike,
        action,
        settings.LOT_SIZE,
        lots,
        underlying_entry,
        underlying_sl,
        underlying_target,
        entry_price,     # <-- option premium, NOT spot price
        stop_loss,       # <-- option premium SL
        target,          # <-- option premium target
        sl_pts,
        target_pts,
        1 if is_paper else 0
    ))
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return trade_id

def calculate_statutory_charges(buy_price: float = 0.0, sell_price: float = 0.0, qty: int = 65,
                                entry_price: Optional[float] = None, exit_price: Optional[float] = None,
                                lot_size: Optional[int] = None) -> Dict[str, float]:
    """
    Computes exact regulatory & statutory charges for Indian equity index options:
    - Brokerage: Flat ₹20 per executed order (₹40 round-trip) via Groww
    - STT (Securities Transaction Tax): 0.15% on option premium sell turnover (post-2024 revised)
    - Exchange Turnover Charge: 0.03503% on total premium turnover (NSE)
    - GST: 18% on (Brokerage + Exchange Turnover Charge + SEBI Charge)
    - SEBI Turnover Fee: ₹10 per crore (0.0001%) on total premium turnover
    - Stamp Duty: 0.003% on buy premium turnover
    """
    if entry_price is not None:
        buy_price = entry_price
    if exit_price is not None:
        sell_price = exit_price
    if lot_size is not None:
        qty = lot_size

    buy_turnover = buy_price * qty
    sell_turnover = sell_price * qty
    total_turnover = buy_turnover + sell_turnover

    brokerage = 40.0 # ₹20 buy + ₹20 sell
    stt = round(sell_turnover * 0.0015, 2)
    exchange_charges = round(total_turnover * 0.0003503, 2)
    sebi_charges = round(total_turnover * 0.000001, 2)
    stamp_duty = round(buy_turnover * 0.00003, 2)
    gst = round((brokerage + exchange_charges + sebi_charges) * 0.18, 2)

    total_charges = round(brokerage + stt + exchange_charges + sebi_charges + stamp_duty + gst, 2)
    return {
        "brokerage": brokerage,
        "stt": stt,
        "exchange_charges": exchange_charges,
        "gst": gst,
        "sebi_charges": sebi_charges,
        "stamp_duty": stamp_duty,
        "total_charges": total_charges
    }

def close_trade(trade_id: int, exit_price: float, outcome: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return

    entry_price = row["entry_price"]
    action = row["action"]
    lot_size = row["lot_size"]
    lots = row["lots"]
    qty = lot_size * lots

    # Option buyers profit when exit > entry
    pnl_pts = round(exit_price - entry_price, 2)
    gross_amount = round(pnl_pts * qty, 2)
    
    # Calculate exact dynamic statutory charges (STT, Brokerage, GST, Exchange)
    fee_details = calculate_statutory_charges(entry_price, exit_price, qty)
    charges = fee_details["total_charges"]
    net_pnl_amount = round(gross_amount - charges, 2)

    cursor.execute("""
        UPDATE trades
        SET exit_price = ?, outcome = ?, pnl_pts = ?, pnl_amount = ?, exit_time = ?
        WHERE id = ?
    """, (exit_price, outcome, pnl_pts, net_pnl_amount, datetime.now().isoformat(), trade_id))
    conn.commit()
    conn.close()
    return net_pnl_amount

def get_daily_net_pnl() -> float:
    """Return today's realized net P&L for the hard daily-loss circuit breaker."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COALESCE(SUM(pnl_amount), 0.0) AS pnl
        FROM trades
        WHERE outcome != 'OPEN' AND date(exit_time, 'localtime') = date('now', 'localtime')
    """)
    row = cursor.fetchone()
    conn.close()
    return float(row["pnl"] or 0.0)

def get_recent_signals(limit: int = 2) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    result = [dict(row) for row in rows]
    conn.close()
    return result

def get_recent_trades(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    result = [dict(row) for row in rows]
    conn.close()
    return result

def get_accuracy_metrics() -> Dict[str, Any]:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as total FROM trades WHERE outcome != 'OPEN'")
    total_trades = cursor.fetchone()["total"]

    if total_trades == 0:
        conn.close()
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate_pct": 0.0,
            "net_expectancy_inr": 0.0,
            "net_expectancy_pts": 0.0,
            "breakeven_win_rate_pct": 36.1,
            "profit_factor": 0.0,
            "total_pnl_pts": 0.0,
            "total_pnl_amount": 0.0,
            "avg_win_inr": 0.0,
            "avg_loss_inr": 0.0,
            "avg_win_pts": 0.0,
            "avg_loss_pts": 0.0,
            "avg_accuracy_confidence": 0.0,
            "open_trades": 0
        }

    cursor.execute("SELECT COUNT(*) as wins, SUM(pnl_amount) as net_win_amt, SUM(pnl_pts) as win_pts FROM trades WHERE outcome = 'TARGET_HIT' OR pnl_pts > 0")
    win_row = cursor.fetchone()
    wins = win_row["wins"] or 0
    net_win_amt = win_row["net_win_amt"] or 0.0
    win_pts = win_row["win_pts"] or 0.0

    cursor.execute("SELECT COUNT(*) as losses, SUM(pnl_amount) as net_loss_amt, SUM(pnl_pts) as loss_pts FROM trades WHERE outcome = 'SL_HIT' OR (outcome != 'OPEN' AND pnl_pts <= 0)")
    loss_row = cursor.fetchone()
    losses = loss_row["losses"] or 0
    net_loss_amt = abs(loss_row["net_loss_amt"] or 0.0)
    loss_pts = abs(loss_row["loss_pts"] or 0.0)

    cursor.execute("SELECT SUM(pnl_pts) as total_pts, SUM(pnl_amount) as total_amt FROM trades WHERE outcome != 'OPEN'")
    pnl_row = cursor.fetchone()
    total_pnl_pts = round(pnl_row["total_pts"] or 0.0, 2)
    total_pnl_amount = round(pnl_row["total_amt"] or 0.0, 2)

    cursor.execute("SELECT COUNT(*) as open_cnt FROM trades WHERE outcome = 'OPEN'")
    open_trades = cursor.fetchone()["open_cnt"]

    cursor.execute("SELECT AVG(confidence_pct) as avg_conf FROM signals WHERE bias != 'NO_TRADE'")
    avg_conf_row = cursor.fetchone()
    avg_conf = round(avg_conf_row["avg_conf"] or 0.0, 1)

    win_rate = round((wins / total_trades) * 100, 1) if total_trades > 0 else 0.0
    avg_win_inr = round(net_win_amt / wins, 2) if wins > 0 else 0.0
    avg_loss_inr = round(net_loss_amt / losses, 2) if losses > 0 else 0.0
    avg_win_pts = round(win_pts / wins, 2) if wins > 0 else 0.0
    avg_loss_pts = round(loss_pts / losses, 2) if losses > 0 else 0.0

    # Dimensionally Rigorous Net Mathematical Expectancy:
    # In Rupees: E_₹ = p(AvgWin_INR) - (1-p)(AvgLoss_INR)
    win_prob = wins / total_trades
    loss_prob = losses / total_trades
    net_expectancy_inr = round((win_prob * avg_win_inr) - (loss_prob * avg_loss_inr), 2)
    
    # In Points: E_pts = p(AvgWin_pts) - (1-p)(AvgLoss_pts) - (AvgCost / LotSize)
    lot_q = settings.LOT_SIZE
    avg_cost_pts = (settings.ESTIMATED_ROUNDTRIP_CHARGES / lot_q) if lot_q > 0 else 1.15
    net_expectancy_pts = round((win_prob * avg_win_pts) - (loss_prob * avg_loss_pts) - avg_cost_pts, 2)

    # Statistical Break-Even Win Rate = AvgLoss / (AvgWin + AvgLoss)
    breakeven_win_rate = round((avg_loss_inr / (avg_win_inr + avg_loss_inr)) * 100, 1) if (avg_win_inr + avg_loss_inr) > 0 else 36.1

    # Profit Factor = Gross Gains / Gross Losses
    profit_factor = round(net_win_amt / net_loss_amt, 2) if net_loss_amt > 0 else (99.0 if net_win_amt > 0 else 1.0)

    conn.close()
    return {
        "total_trades": total_trades,
        "winning_trades": wins,
        "losing_trades": losses,
        "win_rate_pct": win_rate,
        "net_expectancy_inr": net_expectancy_inr,
        "net_expectancy_pts": net_expectancy_pts,
        "breakeven_win_rate_pct": breakeven_win_rate,
        "profit_factor": profit_factor,
        "avg_win_inr": avg_win_inr,
        "avg_loss_inr": avg_loss_inr,
        "avg_win_pts": avg_win_pts,
        "avg_loss_pts": avg_loss_pts,
        "total_pnl_pts": total_pnl_pts,
        "total_pnl_amount": total_pnl_amount,
        "avg_accuracy_confidence": avg_conf,
        "open_trades": open_trades
    }

def get_daily_accuracy_breakdown() -> List[Dict[str, Any]]:
    """Groups historical signals and executed trades by date to track daily accuracy and performance."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 
            substr(timestamp, 1, 10) as trade_date,
            COUNT(*) as total_trades,
            SUM(CASE WHEN outcome = 'TARGET_HIT' OR pnl_pts > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'SL_HIT' OR (outcome != 'OPEN' AND pnl_pts <= 0) THEN 1 ELSE 0 END) as losses,
            SUM(pnl_amount) as net_pnl_inr,
            SUM(pnl_pts) as net_pnl_pts
        FROM trades
        WHERE outcome != 'OPEN'
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT 30
    """)
    rows = cursor.fetchall()
    result = []
    for r in rows:
        tot = r["total_trades"]
        w = r["wins"] or 0
        l = r["losses"] or 0
        wr = round((w / tot) * 100, 1) if tot > 0 else 0.0
        result.append({
            "date": r["trade_date"],
            "total_trades": tot,
            "wins": w,
            "losses": l,
            "win_rate_pct": wr,
            "net_pnl_inr": round(r["net_pnl_inr"] or 0.0, 2),
            "net_pnl_pts": round(r["net_pnl_pts"] or 0.0, 2)
        })
    conn.close()
    return result

def record_price_tick(symbol: str, price: float, day_high: float, day_low: float,
                      ema_9: Optional[float] = None, ema_21: Optional[float] = None, vwap: Optional[float] = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO price_history (timestamp, symbol, price, day_high, day_low, ema_9, ema_21, vwap)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (datetime.now().isoformat(), symbol, price, day_high, day_low, ema_9, ema_21, vwap))
    conn.commit()
    conn.close()

def reset_to_clean_demo_data():
    """Clears duplicates and leaves exactly 2 clean representative demo records."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM trades")
    cursor.execute("DELETE FROM signals")
    cursor.execute("DELETE FROM price_history")
    
    # 1. Clean Bullish Breakout Trade (Target Hit -> +24 pts / +Rs 1,200)
    cursor.execute("""
        INSERT INTO signals (
            id, timestamp, symbol, spot_price, day_high, day_low, bias,
            strike, entry_price, stop_loss, target, sl_pts, target_pts,
            confidence_pct, quality_score, grade, invalidation_level, expires_at,
            factor_breakdown, confirmations, warnings,
            estimated_pnl_pts, reasoning, status, is_dry_run, scalper_link
        ) VALUES (
            1, datetime('now', '-25 minutes'), 'NIFTY', 22648.50, 22652.00, 22560.00, 'BUY_CE',
            'NIFTY 22650 CE', 120.00, 108.00, 144.00, 12.0, 24.0,
            88.0, 88, 'A', 22635.00, strftime('%s', 'now') + 480,
            '{"regime":20,"structure":18,"technicals":15,"volume":10,"oi_microstructure":12,"iv_liquidity":8,"trap_audit":5}',
            '["Day High Breakout (22,652)","Spot Above VWAP Support","Call Unwinding at 22,650","Normal IV Environment"]',
            '[]',
            24.0, 'Day High breakout validated. Call unwinding at 22,650 and spot holding strong above VWAP.',
            'TARGET_HIT', 1, 'groww://options/scalper?symbol=NIFTY&strike=NIFTY%2022650%20CE&type=CE'
        )
    """)
    cursor.execute("""
        INSERT INTO trades (
            id, signal_id, timestamp, symbol, strike, action, lot_size, lots,
            entry_price, exit_price, stop_loss, target, pnl_pts, pnl_amount, outcome, is_paper, exit_time
        ) VALUES (
            1, 1, datetime('now', '-25 minutes'), 'NIFTY', 'NIFTY 22650 CE', 'BUY_CE', 50, 1,
            120.00, 144.00, 108.00, 144.00, 24.0, 1200.00, 'TARGET_HIT', 1, datetime('now', '-18 minutes')
        )
    """)

    # 2. Clean Active / Trailing Breakout Signal
    cursor.execute("""
        INSERT INTO signals (
            id, timestamp, symbol, spot_price, day_high, day_low, bias,
            strike, entry_price, stop_loss, target, sl_pts, target_pts,
            confidence_pct, quality_score, grade, invalidation_level, expires_at,
            factor_breakdown, confirmations, warnings,
            estimated_pnl_pts, reasoning, status, is_dry_run, scalper_link
        ) VALUES (
            2, datetime('now', '-4 minutes'), 'NIFTY', 22654.20, 22655.00, 22560.00, 'BUY_CE',
            'NIFTY 22700 CE', 95.00, 83.00, 119.00, 12.0, 24.0,
            85.0, 85, 'A', 22642.00, strftime('%s', 'now') + 240,
            '{"regime":20,"structure":16,"technicals":15,"volume":8,"oi_microstructure":14,"iv_liquidity":7,"trap_audit":5}',
            '["Fresh Day High Expansion","Put Writing Floor at 22,600","Bullish EMA Crossover","Liquidity Good"]',
            '[]',
            24.0, 'Fresh Day High expansion. Put writing buildup at 22,600 with bullish EMA crossover.',
            'TRIGGERED', 1, 'groww://options/scalper?symbol=NIFTY&strike=NIFTY%2022700%20CE&type=CE'
        )
    """)
    cursor.execute("""
        INSERT INTO trades (
            id, signal_id, timestamp, symbol, strike, action, lot_size, lots,
            entry_price, stop_loss, target, outcome, is_paper
        ) VALUES (
            2, 2, datetime('now', '-4 minutes'), 'NIFTY', 'NIFTY 22700 CE', 'BUY_CE', 50, 1,
            95.00, 83.00, 119.00, 'OPEN', 1
        )
    """)
    conn.commit()
    conn.close()

def clear_all_records():
    """Completely purges all historical and demo records for a clean live trading session."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM trades")
    cursor.execute("DELETE FROM signals")
    cursor.execute("DELETE FROM price_history")
    conn.commit()
    conn.execute("VACUUM")
def get_db_connection_for_path(db_path: str):
    """Returns a DB connection for a specific path (used by tests for isolation)."""
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_test_db(db_path: str) -> None:
    """Initializes a fresh isolated SQLite database for test isolation. Never uses the production DB."""
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS trades")
    cursor.execute("DROP TABLE IF EXISTS signals")
    cursor.execute("DROP TABLE IF EXISTS price_history")
    conn.commit()
    conn.close()
    original_path = settings.DATABASE_PATH
    settings.DATABASE_PATH = db_path
    init_db()
    settings.DATABASE_PATH = original_path

# Initialize tables immediately on import
init_db()

