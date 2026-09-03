.PHONY: help install lint format typecheck test migrate dev docker-up docker-down paper backtest report clean

help:
	@echo "KRIPTO AGENT Development Commands:"
	@echo "  make install     - Install all dependencies"
	@echo "  make lint        - Run ruff linter"
	@echo "  make format      - Auto-format code with ruff"
	@echo "  make typecheck   - Run mypy type checker"
	@echo "  make test        - Run all pytest test suites"
	@echo "  make migrate     - Initialize database tables"
	@echo "  make dev         - Run FastAPI development server"
	@echo "  make paper       - Run 7-day paper trading validation experiment"
	@echo "  make backtest    - Run historical backtest runner"
	@echo "  make report      - View experiment results"
	@echo "  make docker-up   - Start PostgreSQL, TimescaleDB, Redis and API in Docker"
	@echo "  make docker-down - Stop Docker containers"
	@echo "  make clean       - Remove cache and temporary files"

install:
	pip install -e ".[dev]"

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy .

test:
	pytest

migrate:
	python -m database.init_db

dev:
	uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000 --reload

run-api: dev

paper:
	python -m scripts.run_7day_experiment

backtest:
	python -m backtesting.runner

report:
	python -m scripts.run_7day_experiment

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	python -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
