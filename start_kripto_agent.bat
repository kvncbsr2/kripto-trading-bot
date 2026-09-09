@echo off
title KRIPTO AGENT - Binance Spot Algoritmik Trading
echo ========================================================
echo   KRIPTO AGENT BASLATILIYOR (Port: 8000)
echo   Adres: http://127.0.0.1:8000/dashboard.html
echo ========================================================
start http://127.0.0.1:8000/dashboard.html
python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
pause
