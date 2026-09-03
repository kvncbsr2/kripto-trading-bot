# KRIPTO AGENT — Operations Manual

## 1. Quick Start Guide

### Prerequisites
* Python 3.12+
* Git
* Docker & Docker Compose (optional for local SQLite, required for full TimescaleDB/Redis)

### Local Environment Setup
```bash
# 1. Clone repository & install dependencies
pip install -e ".[dev]"

# 2. Configure environment
cp .env.example .env

# 3. Initialize database tables
python -m database.init_db

# 4. Run test suite
pytest

# 5. Run lint & typecheck
ruff check .
mypy .
```

---

## 2. Running Services

### Start FastAPI Server
```bash
uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000 --reload
```
* **API Documentation**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **Interactive Dashboard**: [http://localhost:8000/dashboard](http://localhost:8000/dashboard)

### Run 7-Day Paper Trading Validation Experiment
```bash
python -m scripts.run_7day_experiment
```

### Docker Deployment
```bash
docker compose up -d
```
Starts TimescaleDB (PostgreSQL 16), Redis, FastAPI API server, and Worker.
