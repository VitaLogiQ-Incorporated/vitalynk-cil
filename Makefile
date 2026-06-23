# VitaLynk CIL — common dev commands. Run `make help` for the list.
# Everything runs through uv; no need to activate a venv.

.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test check run demo docker-build docker-run clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Create the venv and install all deps (incl. dev)
	uv sync

lint: ## Lint + format check (ruff)
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-format the code (ruff)
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Static type-check (mypy, strict)
	uv run mypy

test: ## Run the test suite (pytest)
	uv run pytest

check: lint typecheck test ## Run the full gate: lint + typecheck + test

run: ## Start the service on :8000 (telemetry ingest loop runs)
	uv run cil

demo: ## Run the telemetry simulator -> store demo (no server needed)
	uv run python scripts/demo_telemetry.py

docker-build: ## Build the container image
	docker build -t vitalynk-cil:dev .

docker-run: ## Run the container on :8000
	docker run --rm -p 8000:8000 vitalynk-cil:dev

clean: ## Remove venv, caches, and local data
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache data
