@echo off
title NIFTY Signal Engine - Dashboard Only
cd /d "%~dp0\trading"
echo ============================================
echo  NIFTY Options Dashboard Server
echo  URL: http://localhost:8000
echo  WARNING: Uses asyncio HTTP server (no uvicorn needed)
echo ============================================
echo.
REM server.py uses asyncio.start_server directly - NOT uvicorn
python server.py
pause
