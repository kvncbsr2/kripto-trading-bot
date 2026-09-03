.PHONY: help install lint typecheck test run-api docker-up docker-down clean

help:
	@echo "KRIPTO AGENT Development Commands:"
	@echo "  make install     - Install all dependencies"
	@echo "  make lint        - Run ruff linter"
	@echo "  make typecheck   - Run mypy type checker"
	@echo "  make test        - Run all pytest test suites"
	@echo "  make run-api     - Run FastAPI development server"
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

run-api:
	uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000 --reload

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
