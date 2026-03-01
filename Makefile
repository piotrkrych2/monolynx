.PHONY: help dev down lint test build migrate createsuperuser shell worker backfill-embeddings sync-graph

help: ## Pokaz dostepne komendy
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

# --- Docker ---
dev: ## Uruchom srodowisko deweloperskie
	docker compose --profile dev up --build -d

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

# --- Uzytkownicy ---
createsuperuser: ## Stworz superuzytkownika
	docker compose --profile dev exec app python -m monolynx.cli createsuperuser

# --- Wiki RAG ---
backfill-embeddings: ## Wygeneruj embeddingi dla istniejacych stron wiki
	docker compose --profile dev exec app python -c "\
import asyncio; \
from monolynx.services.embeddings import update_page_embeddings; \
from monolynx.services.wiki import get_page_content; \
from monolynx.database import async_session_factory; \
from monolynx.models.wiki_page import WikiPage; \
from sqlalchemy import select, text; \
async def backfill(): \
    async with async_session_factory() as db: \
        result = await db.execute(select(WikiPage)); \
        pages = list(result.scalars().all()); \
        [print(f'Generuje embeddingi: {p.title}') or await update_page_embeddings(p.id, get_page_content(p), db) for p in pages]; \
        count = (await db.execute(text('SELECT count(*) FROM wiki_embeddings'))).scalar(); \
        print(f'Gotowe! Laczna liczba embeddingow: {count}'); \
asyncio.run(backfill())"

# --- Graf kodu ---
sync-graph: ## Synchronizuj graf zaleznosci kodu (AST -> Neo4j)
	docker compose --profile dev exec app python cicd/sync_graph.py

sync-graph-dry: ## Pokaz zmiany w grafie bez zapisu
	docker compose --profile dev exec app python cicd/sync_graph.py --dry-run --verbose

# --- Build ---
build: ## Zbuduj produkcyjny obraz Docker
	docker build -t monolynx:latest .

# --- Setup ---
setup: ## Skonfiguruj lokalne srodowisko dev
	python -m venv .venv
	. .venv/bin/activate && pip install -e ".[dev]"
	pre-commit install
