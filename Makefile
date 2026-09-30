# InventoryMantra developer commands (see CLAUDE.md §3). Run from the repo root.
.PHONY: up down db-shell api web install test test-backend test-frontend lint seed eval

up:            ## start Postgres and wait until healthy
	docker compose up -d --wait db

down:          ## stop containers (data volume is kept)
	docker compose down

db-shell:      ## psql into the dev database
	docker compose exec db psql -U inventorymantra -d inventorymantra

install:       ## install backend + frontend dependencies
	cd backend && uv sync
	cd frontend && npm ci

api:           ## FastAPI on http://localhost:8000
	cd backend && uv run uvicorn app.main:app --reload --port 8000

web:           ## Next.js on http://localhost:3000
	cd frontend && npm run dev

test: test-backend test-frontend   ## all offline checks (no LLM calls)

test-backend:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest -q

test-frontend:
	cd frontend && npm run lint && npm run typecheck

seed:          ## implemented in C1.2/C1.3
	@echo "seed: not implemented yet (arrives in C1.2)"

eval:          ## implemented in C7.1; costs OpenAI tokens, so announce before running
	@echo "eval: not implemented yet (arrives in C7.1)"
