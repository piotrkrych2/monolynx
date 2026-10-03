.PHONY: help dev down lint test build migrate prune-test-dbs createsuperuser shell worker backfill-embeddings backfill-backlinks backfill-dependencies sync-graph sync-graph-dry clear-all-graphs sync-agents sync-agents-check

# Zaladuj .env, zeby targety hostowe (sync-graph) widzialy MONOLYNX_MCP_TOKEN itd.
-include .env
export

help: ## Pokaz dostepne komendy
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

# --- Docker ---
dev: ## Uruchom srodowisko deweloperskie
	docker compose --profile dev up -d

down: ## Zatrzymaj srodowisko
	docker compose --profile dev --profile worker down

worker: ## Uruchom worker monitoringu (dev)
	docker compose --profile dev --profile worker up --build -d worker

logs: ## Pokaz logi aplikacji
	docker compose --profile dev logs -f -n 100 app

# --- Jakosc kodu ---
lint: ## Uruchom ruff --fix + format + mypy (w Docker)
	docker compose --profile dev exec app ruff check --fix src/ tests/
	docker compose --profile dev exec app ruff format src/ tests/
	docker compose --profile dev exec app mypy src/

fmt: ## Auto-formatuj kod (w Docker)
	docker compose --profile dev exec app ruff check --fix src/ tests/
	docker compose --profile dev exec app ruff format src/ tests/

test: ## Uruchom testy z coverage (w Docker)
	docker compose --profile dev exec app pytest tests/ --cov=src/monolynx --cov-report=term

# --- Baza danych ---
migrate: ## Uruchom migracje Alembic
	docker compose --profile dev exec app alembic upgrade head

migration: ## Stworz migracje (usage: make migration msg="add events table")
	docker compose --profile dev exec app alembic revision --autogenerate -m "$(msg)"

prune-test-dbs: ## Usun porzucone bazy testowe open_sentry_test_* po przerwanych przebiegach
	docker compose --profile dev exec app python -m monolynx.cli prune-test-dbs

# --- Uzytkownicy ---
createsuperuser: ## Stworz superuzytkownika
	docker compose --profile dev exec app python -m monolynx.cli createsuperuser

# --- Wiki RAG ---
backfill-embeddings: ## Wygeneruj embeddingi dla istniejacych stron wiki
	docker compose --profile dev exec app python -m monolynx.cli backfill-embeddings

backfill-backlinks: ## Wygeneruj backlinki dla istniejacych stron wiki
	docker compose --profile dev exec app python -m monolynx.cli backfill-backlinks

# --- Scrum ---
backfill-dependencies: ## Zaproponuj/zapisz zaleznosci ze starych opisow/komentarzy (dry-run; ARGS=--apply zeby zapisac)
	docker compose --profile dev exec app python -m monolynx.cli backfill-dependencies $(ARGS)

# --- Skille pluginowe ---
sync-skills: ## Skopiuj plugin/skills -> static/skills (zrodlo dla install_monolynx_skills)
	python3 scripts/sync_skills.py

sync-skills-check: ## Sprawdz czy static/skills jest zgodne z plugin/skills (exit 1 gdy nie)
	python3 scripts/sync_skills.py --check

sync-agents: ## Skopiuj plugin/agents -> .claude/agents (zrodlo tresci rol dla obu klientow)
	python3 scripts/sync_agents.py

sync-agents-check: ## Sprawdz czy .claude/agents jest zgodne z plugin/agents (exit 1 gdy nie)
	python3 scripts/sync_agents.py --check

validate-plugin: ## Sprawdz spojnosc plikow pluginu
	python3 scripts/validate_plugin_consistency.py

# --- Graf kodu ---
sync-graph: ## Synchronizuj graf zaleznosci kodu z Monolynx (wymaga graphify)
	graphify update . && python cicd/sync_graph.py

sync-graph-dry: ## Zmapuj graf bez wysylki (wymaga graphify)
	graphify update . && python cicd/sync_graph.py --dry-run --verbose

clear-all-graphs: ## Zaoraj grafy Neo4j WSZYSTKICH projektow (interaktywne potwierdzenie)
	docker compose --profile dev exec app python -m monolynx.cli clear-all-graphs

# --- Build ---
build: ## Zbuduj produkcyjny obraz Docker
	docker build -t monolynx:latest .

# --- Setup ---
setup: ## Skonfiguruj lokalne srodowisko dev
	python -m venv .venv
	. .venv/bin/activate && pip install -e ".[dev]"
	pre-commit install

dev-build: ## Uruchom srodowisko dev z przebudowa obrazu
	docker compose --profile dev up --build -d
