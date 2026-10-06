set dotenv-load

# Recipes target the DEV compose file by default.
# For production, override: COMPOSE="docker compose" just up
compose := env_var_or_default("COMPOSE", "docker compose -f docker-compose.dev.yml")

default:
    @just --list

# ── Docker ────────────────────────────────────────────────
up:
    {{compose}} up -d --build

down:
    {{compose}} down

logs service="api":
    {{compose}} logs -f {{service}}

restart service="api":
    {{compose}} restart {{service}}

ps:
    {{compose}} ps

# ── Secrets ───────────────────────────────────────────────
# Scaffold local dev secrets in ./secrets/ — never overwrites existing files.
# Random keys are generated; Yahoo/API key files get REPLACE_ME placeholders.
secrets-init:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p secrets
    write_secret() {
        local data
        data=$(cat)
        if [ -f "secrets/$1.txt" ]; then
            echo "  skip secrets/$1.txt (exists)"
        else
            printf '%s' "$data" > "secrets/$1.txt"
            echo "  wrote secrets/$1.txt"
        fi
    }
    write_placeholder() {
        if [ -f "secrets/$1.txt" ]; then
            echo "  skip secrets/$1.txt (exists)"
        else
            echo "REPLACE_ME" > "secrets/$1.txt"
            echo "  wrote secrets/$1.txt (placeholder — fill in)"
        fi
    }
    echo "Scaffolding local dev secrets in ./secrets/ ..."
    openssl rand -base64 48 | tr -d '\n' | write_secret jwt_secret_key
    openssl rand -base64 48 | tr -d '\n' | write_secret session_secret
    openssl rand -base64 48 | tr -d '\n' | write_secret csrf_secret
    (cd apps/api && uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") | write_secret fernet_key
    for name in yahoo_client_id yahoo_client_secret yahoo_league_id commissioner_yahoo_guid google_gemini_api_key openrouter_api_key the_odds_api_key; do
        write_placeholder "$name"
    done
    echo "Done — fill in the REPLACE_ME files, then: {{compose}} up -d --build"

# ── Database ──────────────────────────────────────────────
db-shell:
    {{compose}} exec db psql -U moose -d moose_empire

db-migrate message="auto":
    {{compose}} exec api alembic revision --autogenerate -m "{{message}}"

db-upgrade:
    {{compose}} exec api alembic upgrade head

db-downgrade:
    {{compose}} exec api alembic downgrade -1

db-reset:
    {{compose}} down -v
    {{compose}} up -d db redis
    @echo "Waiting for DB..."
    sleep 3
    {{compose}} up -d api worker web

# ── Backend ───────────────────────────────────────────────
api-shell:
    {{compose}} exec api bash

api-lint:
    cd apps/api && uv run ruff check src/

api-format:
    cd apps/api && uv run ruff format src/

api-test:
    {{compose}} exec api pytest tests/ -v

# ── Frontend ──────────────────────────────────────────────
web-shell:
    {{compose}} exec web sh

web-lint:
    cd apps/web && pnpm lint

web-build:
    cd apps/web && pnpm build

# ── Worker ────────────────────────────────────────────────
worker-logs:
    {{compose}} logs -f worker

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
    {{compose}} exec db pg_isready -U moose -d moose_empire -t 10
    if [ $? -ne 0 ]; then
        echo "ERROR: Database is not reachable. Aborting migration."
        exit 1
    fi
    echo "Database is ready. Running migrations..."
    {{compose}} exec api alembic upgrade head
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
    {{compose}} build
    @echo "Starting services..."
    {{compose}} up -d
    @echo "Waiting for services..."
    sleep 5
    @echo "Running initial migration..."
    {{compose}} exec api alembic upgrade head
    @echo "Setup complete! https://localhost (Traefik TLS)"

