SHELL := /bin/bash
.PHONY: help setup start admin seed create-admin status clean nuke docs docs-serve lint fmt release docker pentest pentest-setup pentest-kali realm-fixtures

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## One-time setup: keys, TLS certs, env files, deps, dev containers
	@mkdir -p keys/tls
	@# ── JWT signing keys ──────────────────────────────────────────────
	@if [ ! -f keys/private.pem ]; then \
		openssl genrsa -out keys/private.pem 2048 2>/dev/null && \
		openssl rsa -in keys/private.pem -pubout -out keys/public.pem 2>/dev/null && \
		echo "✓ Generated JWT keys (keys/)"; \
	else \
		echo "· JWT keys already exist"; \
	fi
	@# ── TLS certs for Postgres + Redis (internal CA + server cert) ──
	@if [ ! -f keys/tls/ca.crt ]; then \
		openssl req -x509 -newkey rsa:2048 \
			-keyout keys/tls/ca.key -out keys/tls/ca.crt \
			-days 3650 -nodes -subj "/CN=Duar Internal CA" 2>/dev/null && \
		openssl req -newkey rsa:2048 \
			-keyout keys/tls/server.key -out /tmp/duar-server.csr \
			-nodes -subj "/CN=duar-internal" 2>/dev/null && \
		openssl x509 -req -in /tmp/duar-server.csr \
			-CA keys/tls/ca.crt -CAkey keys/tls/ca.key -CAcreateserial \
			-out keys/tls/server.crt -days 3650 \
			-extfile <(printf "subjectAltName=DNS:localhost,DNS:postgres,DNS:redis,IP:127.0.0.1") 2>/dev/null && \
		rm -f /tmp/duar-server.csr keys/tls/ca.srl && \
		chmod 600 keys/tls/server.key keys/tls/ca.key && \
		echo "✓ Generated TLS certs (keys/tls/)"; \
	else \
		echo "· TLS certs already exist"; \
	fi
	@# ── Dev env (service/.env) ──────────────────────────────────────
	@if [ ! -f service/.env ]; then \
		SESSION_KEY=$$(python3 -c "import secrets; print(secrets.token_urlsafe(32))"); \
		sed "s|^JWT_PRIVATE_KEY_PATH=.*|JWT_PRIVATE_KEY_PATH=../keys/private.pem|; \
		     s|^JWT_PUBLIC_KEY_PATH=.*|JWT_PUBLIC_KEY_PATH=../keys/public.pem|; \
		     s|^SESSION_SECRET_KEY=.*|SESSION_SECRET_KEY=$$SESSION_KEY|; \
		     s|^REDIS_TLS_CA_CERT=.*|REDIS_TLS_CA_CERT=../keys/tls/ca.crt|" \
		  .env.dev.example > service/.env && \
		echo "✓ Created service/.env"; \
	else \
		echo "· service/.env already exists"; \
	fi
	@# ── Prod env (.env.prod) ────────────────────────────────────────
	@if [ ! -f .env.prod ]; then \
		PG_PASS=$$(openssl rand -base64 24); \
		REDIS_PASS=$$(openssl rand -base64 24); \
		SESSION_KEY=$$(python3 -c "import secrets; print(secrets.token_urlsafe(32))"); \
		sed "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$$PG_PASS|; \
		     s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=$$REDIS_PASS|; \
		     s|^SESSION_SECRET_KEY=.*|SESSION_SECRET_KEY=$$SESSION_KEY|" \
		  .env.prod.example > .env.prod && \
		echo "✓ Created .env.prod (random passwords + session secret)"; \
	else \
		echo "· .env.prod already exists"; \
	fi
	@# ── Dev dependencies + containers ───────────────────────────────
	uv sync --extra dev
	cd admin && npm install
	docker compose up -d identity-postgres identity-redis
	@until [ "$$(docker compose ps identity-postgres --format '{{.Health}}')" = "healthy" ]; do sleep 1; done
	@echo ""
	@echo "Setup complete!"
	@echo ""
	@echo "  Next:"
	@echo "    1. vim service/.env   — add OAuth creds (GOOGLE_*, GITHUB_*, etc.) + ADMIN_EMAILS"
	@echo "    2. make start         — start identity service (:9003)"
	@echo "    3. make admin         — start admin UI (:9004)"
	@echo "    make seed             — populate with test data (optional)"
	@echo ""
	@echo "  Prod:"
	@echo "    vim .env.prod   — set BASE_URL, ADMIN_URL, OAuth creds, ADMIN_EMAILS"
	@echo "    docker stack deploy -c docker-compose.prod.yml duar"

start: ## Start identity service (:9003) — auto-migrates on boot
	cd service && uv run uvicorn src.main:app --port 9003 --reload --no-server-header

admin: ## Start admin UI dev server (:9004)
	cd admin && npm run dev

seed: ## Seed database with test data
	uv run python scripts/seed.py

create-admin: ## Create or promote a user to admin
	uv run python scripts/create_admin.py

status: ## Check what's running
	@docker compose ps 2>/dev/null || echo "No containers"
	@printf "Service: " && curl -sf http://localhost:9003/health 2>/dev/null || echo "not running"
	@curl -sf -o /dev/null http://localhost:9004 && echo "Admin:   running on :9004" || echo "Admin:   not running"

lint: ## Run ruff linter and format check
	uv run ruff check service/src/ service/tests/ sdk/src/ sdk/tests/
	uv run ruff format --check service/src/ service/tests/ sdk/src/ sdk/tests/

fmt: ## Auto-fix lint and formatting issues
	uv run ruff check --fix service/src/ service/tests/ sdk/src/ sdk/tests/
	uv run ruff format service/src/ service/tests/ sdk/src/ sdk/tests/

realm-fixtures: ## Regenerate cross-SDK integration test fixtures
	uv run python service/tests/integration/gen_fixtures.py

docs: ## Build documentation site
	uv run --extra docs mkdocs build

docs-serve: ## Serve documentation site with live reload
	uv run --extra docs mkdocs serve

docker: ## Start full stack in Docker (service + postgres + redis)
	docker compose up -d --build
	@until [ "$$(docker compose ps identity-postgres --format '{{.Health}}')" = "healthy" ]; do sleep 1; done
	@echo ""
	@echo "  Service: http://localhost:9003"
	@echo "  Stop:    docker compose down"

clean: ## Stop containers and wipe database
	docker compose down -v

nuke: clean ## Full reset: wipe everything including deps, keys, and env files
	rm -rf keys/ .venv service/.venv sdk/.venv admin/node_modules sdks/*/node_modules
	rm -f service/.env .env.prod
	@echo "Run 'make setup' to start fresh."

release: ## Release all packages (usage: make release VERSION=0.6.0)
ifndef VERSION
	$(error VERSION is required — usage: make release VERSION=0.6.0)
endif
	@./scripts/release.sh $(VERSION)

pentest: ## Run pentest suite (usage: make pentest [ARGS="--sast|--custom|--zap|..."])
	@cd pentest && python run_all.py $(or $(ARGS),--all)

pentest-fast: ## Run pentest suite with relaxed rate limits (RATE_LIMIT_RPM=1000, 5s cooldown)
	@cd pentest && PENTEST_FAST=1 python run_all.py $(or $(ARGS),--all)

pentest-setup: ## Install pentest tools
	cd pentest && bash setup_tools.sh

pentest-kali: ## Launch Kali container (stop: make pentest-kali ARGS=stop)
ifeq ($(ARGS),stop)
	docker compose -f docker-compose.yml -f docker-compose.pentest.yml down kali
else
	docker compose -f docker-compose.yml -f docker-compose.pentest.yml up -d kali
	@echo ""
	@echo "  Kali ready: docker exec -it duar-kali bash"
	@echo "  Stop:       make pentest-kali ARGS=stop"
endif
