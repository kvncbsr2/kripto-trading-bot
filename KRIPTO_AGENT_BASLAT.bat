@echo off
chcp 65001 >nul
title KRIPTO AGENT — Otonom Kripto İşlem Merkezi
color 0B

echo ========================================================================
echo           ⚡ KRIPTO AGENT — OTONOM TICARET ^& KONTROL MERKEZI ⚡
echo ========================================================================
echo.
echo [+] Proje Dizini : %~dp0
echo [+] Web Paneli   : http://127.0.0.1:8000/dashboard
echo.
echo [1/2] Kontrol paneli tarayicida aciliyor...
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8000/dashboard

echo [2/2] Web sunucusu (Uvicorn) baslatiliyor...
echo.
echo ========================================================================
echo 💡 Sistemi durdurmak istediginizde bu pencereyi kapatmaniz yeterlidir.
echo ========================================================================
echo.

cd /d "%~dp0"
python -m uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000
pause
