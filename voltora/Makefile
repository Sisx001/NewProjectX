# VOLTORA developer/operator entry points. `make help` lists targets.
SHELL := /bin/bash
BACKEND := cd backend &&
WEB := cd web &&

.PHONY: help install-dev dev dev-db dev-api dev-web test test-backend test-web lint build migrate migration regen-migration \
        backup backup-verify e2e check-scripts docker-build docker-up

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | sort

install-dev: ## install backend (uv) and web (npm ci) dependencies
	$(BACKEND) uv sync
	$(WEB) npm ci --no-audit --no-fund

dev-db: ## start a local PostgreSQL on :5433 unless one is running
	scripts/dev-db.sh

dev-api:
	$(BACKEND) uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

dev-web:
	$(WEB) npm run dev

dev: dev-db migrate ## PostgreSQL + API (:8000) + web (:5173)
	$(MAKE) -j2 dev-api dev-web

test: test-backend test-web ## backend pytest + web unit tests

test-backend:
	$(BACKEND) uv run pytest -q

test-web:
	$(WEB) npx vitest run

lint: ## ruff + strict TypeScript
	$(BACKEND) uv run ruff check .
	$(WEB) npx tsc -p . --noEmit

build: ## production web build (web/dist, served by the API)
	$(WEB) npm run build

migrate: ## alembic upgrade head against VOLTORA_DATABASE_URL
	$(BACKEND) uv run alembic upgrade head

migration: ## autogenerate a revision: make migration m="add foo"
	@test -n "$(m)" || { echo 'usage: make migration m="message"'; exit 2; }
	scripts/new-migration.sh "$(m)"

regen-migration: ## pre-release only: collapse to one initial revision
	scripts/regen-initial-migration.sh

backup: ## encrypted backup now (pg_dump + media)
	$(BACKEND) uv run python -m app.modules.ops.backup create --encrypt

backup-verify: ## restore drill of the newest backup into a scratch database
	$(BACKEND) uv run python -m app.modules.ops.backup verify

e2e: ## browser journeys (needs the API on :8000 and a built web app)
	$(WEB) npm run e2e

check-scripts: ## syntax-check every shell script (shellcheck too when installed)
	@for f in scripts/*.sh deploy/*.sh; do bash -n $$f && echo "ok $$f"; done
	@command -v shellcheck >/dev/null && shellcheck scripts/*.sh deploy/*.sh || echo "shellcheck not installed (skipped)"

docker-build: ## build the production image (needs Docker)
	docker build -f deploy/Dockerfile -t voltora:local .

docker-up: ## start the production stack (needs Docker and deploy/.env)
	docker compose -f deploy/docker-compose.yml up -d --build
