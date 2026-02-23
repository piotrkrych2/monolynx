# ============================================================
# Stage 1: Builder -- instalacja zaleznosci
# ============================================================
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements/base.txt requirements/base.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements/base.txt

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir --prefix=/install .

# ============================================================
# Stage: Dev -- development z hot reload
# ============================================================
FROM python:3.12-slim AS dev

WORKDIR /app

COPY requirements/dev.txt requirements/dev.txt
COPY requirements/base.txt requirements/base.txt
RUN pip install --no-cache-dir -r requirements/dev.txt

COPY pyproject.toml .
COPY src/ src/
COPY tests/ tests/
COPY sdk/ sdk/
RUN pip install --no-cache-dir -e ".[dev]"

# ============================================================
# Stage 2: Runtime -- minimalny obraz produkcyjny
# ============================================================
FROM python:3.12-slim AS runtime

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --no-create-home appuser

COPY --from=builder /install /usr/local

WORKDIR /app
COPY alembic.ini .
COPY alembic/ alembic/
RUN chown -R appuser:appuser /app
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"

EXPOSE 8000
CMD ["uvicorn", "monolynx.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
