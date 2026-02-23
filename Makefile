.PHONY: help dev down lint test build migrate createsuperuser shell worker

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

# --- Build ---
build: ## Zbuduj produkcyjny obraz Docker
	docker build -t monolynx:latest .

# --- Setup ---
setup: ## Skonfiguruj lokalne srodowisko dev
	python -m venv .venv
	. .venv/bin/activate && pip install -e ".[dev]"
	pre-commit install
