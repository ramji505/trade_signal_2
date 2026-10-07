import sys
import os
import logging

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

from config import settings
from database import init_db
from server import run_app
from feed import OptionChainSnapshot
from groww_client import live_client

def main():
    print("=" * 65)
    print(">>> NIFTY DECISION SUPPORT & SIGNAL ENGINE (GROWW + GEMINI)")
    print("=" * 65)
    print(f"Symbol:               {settings.SYMBOL} (Lot Size: {settings.LOT_SIZE})")
    print(f"Setup Quality Engine: 0–100 Factor Breakdown Active")
    print(f"Market Regimes:       6 Explicit Quantitative Regimes")
    print(f"AI Anomaly Auditor:   {settings.GEMINI_MODEL} (Decoupled Background)")
    print(f"Risk Management:      Floor Protected SL >= {settings.MIN_SL_FLOOR_PTS}pt | 1:{settings.RISK_REWARD_RATIO} R:R")
    print(f"Database Path:        {settings.DATABASE_PATH}")
    print(f"Web Dashboard:        http://{settings.SERVER_HOST}:{settings.SERVER_PORT}")
    print("=" * 65)

    # Initialize SQLite database tables
    init_db()

    if settings.MARKET_DATA_MODE == "GROWW":
        # LIVE mode is strict: failed auth/data is surfaced, never replaced by fake credentials.
        OptionChainSnapshot.set_provider(live_client)
    elif settings.MARKET_DATA_MODE == "UPSTOX":
        from upstox_client import upstox_client
        OptionChainSnapshot.set_provider(upstox_client)

    # Launch Web Server & Feed Engine
    run_app()

if __name__ == "__main__":
    main()
