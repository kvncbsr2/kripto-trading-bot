@echo off
chcp 65001 > nul
echo ===============================================================================
echo ⚡ STARTING KRIPTO AGENT — LOCAL TRADING CONTROL CENTER
echo ===============================================================================
echo Opening Local Control Dashboard: http://localhost:8000/dashboard
echo API OpenAPI Documentation:       http://localhost:8000/docs
echo Prometheus Metrics:              http://localhost:8000/metrics
echo ===============================================================================

REM Start browser after 2 seconds in background
start "" timeout /t 2 /nobreak > nul & start http://localhost:8000/dashboard

REM Start FastAPI local server
uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000 --reload
