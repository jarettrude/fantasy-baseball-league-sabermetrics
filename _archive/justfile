set dotenv-load

default:
    @just --list

# ── Docker ────────────────────────────────────────────────
up:
    docker compose up -d --build

down:
    docker compose down

logs service="api":
    docker compose logs -f {{service}}

restart service="api":
    docker compose restart {{service}}

ps:
    docker compose ps

# ── Database ──────────────────────────────────────────────
db-shell:
    docker compose exec db psql -U moose -d moose_empire

db-migrate message="auto":
    docker compose exec api alembic revision --autogenerate -m "{{message}}"

db-upgrade:
    docker compose exec api alembic upgrade head

db-downgrade:
    docker compose exec api alembic downgrade -1

db-reset:
    docker compose down -v
    docker compose up -d db redis
    @echo "Waiting for DB..."
    sleep 3
    docker compose up -d api worker web

# ── Backend ───────────────────────────────────────────────
api-shell:
    docker compose exec api bash

api-lint:
    cd apps/api && uv run ruff check src/

api-format:
    cd apps/api && uv run ruff format src/

api-test:
    docker compose exec api pytest tests/ -v

# ── Frontend ──────────────────────────────────────────────
web-shell:
    docker compose exec web sh

web-lint:
    cd apps/web && pnpm lint

web-build:
    cd apps/web && pnpm build

# ── Worker ────────────────────────────────────────────────
worker-logs:
    docker compose logs -f worker

# ── Sync Jobs (manual trigger) ────────────────────────────
sync-league:
    curl -sk -X POST https://localhost/api/admin/sync \
        -H "Content-Type: application/json" \
        -d '{"job_name": "sync_league_meta"}' | python3 -m json.tool

sync-matchups:
    curl -sk -X POST https://localhost/api/admin/sync \
        -H "Content-Type: application/json" \
        -d '{"job_name": "sync_matchups"}' | python3 -m json.tool

# ── OpenAPI ───────────────────────────────────────────────
openapi-export:
    curl -sk https://localhost/openapi.json > packages/shared/openapi.json

openapi-types:
    cd packages/shared && pnpm generate-types

# ── Spec-Required Aliases ─────────────────────────────────

# `just migrate` — per spec §3.5:
#   1. Refuse to run when DEMO_MODE=true (hard error).
#   2. Connectivity check (pg_isready).
#   3. Run alembic upgrade head.
migrate:
    #!/usr/bin/env bash
    set -euo pipefail
    # Guard: refuse to migrate in demo mode
    DEMO_MODE="${DEMO_MODE:-false}"
    if [ "$DEMO_MODE" = "true" ]; then
        echo "ERROR: Refusing to run migrations with DEMO_MODE=true."
        echo "       Set DEMO_MODE=false for production migrations."
        exit 1
    fi
    # Connectivity check
    echo "Checking database connectivity..."
    docker compose exec db pg_isready -U moose -d moose_empire -t 10
    if [ $? -ne 0 ]; then
        echo "ERROR: Database is not reachable. Aborting migration."
        exit 1
    fi
    echo "Database is ready. Running migrations..."
    docker compose exec api alembic upgrade head
    echo "Migrations complete."

# `just generate-types` — per spec §3.1:
#   1. Export OpenAPI schema from running API.
#   2. Generate TypeScript types into packages/shared/.
generate-types:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Exporting OpenAPI schema from API..."
    curl -sf https://localhost/api/openapi.json > packages/shared/openapi.json \
        || (echo "ERROR: Could not fetch OpenAPI schema. Is the API running?" && exit 1)
    echo "Generating TypeScript types..."
    cd packages/shared && pnpm exec openapi-typescript openapi.json -o src/generated/api.ts
    echo "Types generated at packages/shared/src/generated/api.ts"

# ── Full Setup ────────────────────────────────────────────
setup:
    @echo "Installing backend dependencies..."
    cd apps/api && uv sync
    @echo "Installing frontend dependencies..."
    pnpm install
    @echo "Building Docker images..."
    docker compose build
    @echo "Starting services..."
    docker compose up -d
    @echo "Waiting for services..."
    sleep 5
    @echo "Running initial migration..."
    docker compose exec api alembic upgrade head
    @echo "Setup complete! https://localhost (Traefik TLS)"

