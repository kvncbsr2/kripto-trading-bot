Write-Host "===============================================================================" -ForegroundColor Cyan
Write-Host "⚡ STARTING KRIPTO AGENT — LOCAL TRADING CONTROL CENTER" -ForegroundColor Green
Write-Host "===============================================================================" -ForegroundColor Cyan
Write-Host "Opening Local Control Dashboard: http://localhost:8000/dashboard" -ForegroundColor Yellow
Write-Host "API OpenAPI Documentation:       http://localhost:8000/docs" -ForegroundColor Yellow
Write-Host "Prometheus Metrics:              http://localhost:8000/metrics" -ForegroundColor Yellow
Write-Host "===============================================================================" -ForegroundColor Cyan

Start-Process "http://localhost:8000/dashboard"
uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000 --reload
