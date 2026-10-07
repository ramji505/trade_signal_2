@echo off
title NIFTY Backtest Engine
cd /d "%~dp0\trading"
echo ============================================
echo  NIFTY Options Backtest Engine  v2.1
echo  NOTE: Uses fixed random seed (20261005) for reproducibility
echo  MARKET_DATA_MODE=MOCK (synthetic candles)
echo  To use Groww historical data: set MARKET_DATA_MODE=GROWW
echo ============================================
echo.
python -c "from backtester import BacktestEngine; import json; res = BacktestEngine.run_stress_testing(symbol='NIFTY', count=200); print(json.dumps(res, indent=2))"
pause
