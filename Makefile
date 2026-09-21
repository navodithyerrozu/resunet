# ---------------------------------------------------------------------------
# ResuNet developer entrypoints. `make help` lists everything.
# ---------------------------------------------------------------------------
.DEFAULT_GOAL := help
.PHONY: help bootstrap up down logs ps reset api web worker-render worker-auto \
        indexes gen-types typecheck lint fmt test test-api verify

SHELL := /bin/bash
COMPOSE := docker compose

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

bootstrap: ## First-time setup: env file, JS deps, Python venvs
	@test -f .env || (cp .env.example .env && echo "Created .env -- fill in your API keys")
	pnpm install
	cd apps/api && python -m venv .venv && .venv/bin/pip install -q -e ".[dev]"
	cd apps/render-worker && python -m venv .venv && .venv/bin/pip install -q -e "."
	cd apps/automation-worker && python -m venv .venv && .venv/bin/pip install -q -e "." \
		&& .venv/bin/playwright install chromium
	@echo "Bootstrap complete. Next: make up && make indexes"

up: ## Start the full stack
	$(COMPOSE) up -d --build

down: ## Stop the stack (volumes preserved)
	$(COMPOSE) down

reset: ## Stop the stack and DESTROY all data volumes
	$(COMPOSE) down -v

logs: ## Tail logs from all services
	$(COMPOSE) logs -f --tail=100

ps: ## Show service status
	$(COMPOSE) ps

api: ## Run the API on the host with reload
	cd apps/api && .venv/bin/uvicorn app.main:app --reload --port 8000

web: ## Run the frontend on the host
	pnpm --filter @resunet/web dev

worker-render: ## Run the LaTeX render worker on the host
	cd apps/render-worker && .venv/bin/arq worker.main.WorkerSettings

worker-auto: ## Run the Playwright automation worker on the host
	cd apps/automation-worker && .venv/bin/arq worker.main.WorkerSettings

indexes: ## Create/sync all MongoDB indexes (idempotent)
	cd apps/api && .venv/bin/python -m app.db.indexes

gen-types: ## Regenerate shared TypeScript types from the OpenAPI schema
	bash scripts/gen-types.sh

typecheck: ## Static type checks across Python and TypeScript
	cd apps/api && .venv/bin/mypy app
	pnpm -r typecheck

lint: ## Lint Python and TypeScript
	cd apps/api && .venv/bin/ruff check app tests
	pnpm lint

fmt: ## Auto-format everything
	cd apps/api && .venv/bin/ruff format app tests && .venv/bin/ruff check --fix app tests
	pnpm exec prettier --write "apps/web/src/**/*.{ts,tsx,css}" "packages/shared/src/**/*.ts"

test-api: ## Run backend tests
	cd apps/api && .venv/bin/pytest -q

test: test-api ## Run all tests

verify: lint typecheck test ## Everything CI runs
