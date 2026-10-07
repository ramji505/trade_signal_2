@echo off
title NIFTY Signal Engine - Main App
cd /d "%~dp0\trading"
echo ============================================
echo  NIFTY Options Signal Engine  v2.1
echo  Mode: PAPER (DRY_RUN=true)
echo  Dashboard: http://localhost:8000
echo ============================================
echo.
python main.py
pause
