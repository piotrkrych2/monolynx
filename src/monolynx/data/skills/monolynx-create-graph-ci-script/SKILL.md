---
name: monolynx-create-graph-ci-script
description: "Wygeneruj skrypt CI do synchronizacji grafu zaleznosci kodu z platforma Monolynx. Analizuje projekt Python (Django, FastAPI, Flask itp.), tworzy cicd/sync_graph.py i stage w .gitlab-ci.yml. Uzyj w dowolnym projekcie Python."
user-invocable: true
argument-hint: [monolynx-url]
---

# Generowanie skryptu synchronizacji grafu kodu z Monolynx

Twoim zadaniem jest wygenerowac skrypt `cicd/sync_graph.py` i stage w `.gitlab-ci.yml` dla **biezacego projektu Python**. Skrypt analizuje kod zrodlowy (AST) i synchronizuje graf zaleznosci z platforma Monolynx.

**Monolynx URL**: `$ARGUMENTS` (domyslnie: `https://open.monolynx.com`)

---

## KROK 1: Analiza projektu

### 1a. Znajdz pakiet Python

Przeszukaj projekt i ustal:

- **Glowny katalog zrodlowy** — szukaj w kolejnosci: `src/<nazwa>/`, `<nazwa>/`, `app/`
- **Nazwa pakietu** — z `pyproject.toml` (pole `name`), `setup.py` lub `setup.cfg`
- **Framework** — sprawdz importy: `django`, `fastapi`, `flask`, `celery`

Przyklad: jesli `pyproject.toml` ma `name = "myapp"` i istnieje `src/myapp/`, to:
- `src_dir = "src/myapp"`
- `package_name = "myapp"`

### 1b. Zmapuj strukture katalogow

Wylistuj wszystkie katalogi w pakiecie. Typowe wzorce:

| Framework | Typowe katalogi |
|---|---|
| Django | `views/`, `models/`, `serializers/`, `tasks/`, `signals/`, `admin/`, `management/`, `templatetags/` |
| FastAPI | `api/`, `routers/`, `services/`, `models/`, `schemas/`, `middleware/` |
| Flask | `views/`, `blueprints/`, `models/`, `services/` |
| Ogolne | `core/`, `utils/`, `helpers/`, `config/`, `cli/`, `tests/` (pomijaj!) |

### 1c. Wygeneruj PREFIX_MAP

Reguly generowania prefiksow dla funkcji/metod:

1. **Plik w podkatalogu** — uzyj nazwy pliku (stem) jako prefiksu:
   - `services/payment.py` → `"payment"`
   - `views/orders.py` → `"orders"`

2. **Konflikt nazw** (dwa pliki z ta sama nazwa w roznych katalogach) — dodaj katalog:
   - `views/users.py` → `"views_users"`
   - `api/users.py` → `"api_users"`

3. **Pliki w katalogu glownym** — uzyj nazwy pliku:
   - `config.py` → `"cfg"`
   - `main.py` → `"app"`
   - `celery.py` → `"celery"`

4. **Pliki `__init__.py`** — pomijaj jesli puste

### 1d. Wygeneruj MODULE_MAP

Kazdy podkatalog z plikami `.py` staje sie modulem:
- `views/` → `"Views"`
- `services/` → `"Services"`
- `models/` → `"Models"`

Pomijaj: `tests/`, `migrations/`, `__pycache__/`

### 1e. Zapytaj uzytkownika

Zapytaj:
- **Slug projektu na Monolynx** — nazwa projektu na platformie (np. `my-app`, `ecommerce`)
- **Potwierdz PREFIX_MAP** — pokaz wygenerowana mape i zapytaj czy jest OK

---

## KROK 2: Wygeneruj skrypt `cicd/sync_graph.py`

Stworz plik `cicd/sync_graph.py` na bazie ponizszej specyfikacji.

### Architektura skryptu

```
cicd/sync_graph.py
├── MonolynxClient          — komunikacja HTTP z Monolynx MCP API
├── ASTAnalyzer             — analiza AST codebase'u (2 przebiegi)
├── compute_diff()          — porownanie desired vs current
└── main()                  — CLI z argparse
```

### Klasa MonolynxClient — komunikacja z Monolynx

Skrypt komunikuje sie z Monolynx przez **MCP Streamable HTTP** (JSON-RPC). Uzyj `urllib.request` (stdlib, zero zaleznosci).

**Protokol:**

1. **Inicjalizacja sesji:**
```http
POST {monolynx_url}/mcp/ HTTP/1.1
Authorization: Bearer {token}
Content-Type: application/json

{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"sync_graph","version":"1.0"}},"id":1}
```
Odpowiedz zawiera header `Mcp-Session-Id` — zapisz go i dolaczaj do kazdego kolejnego requestu.

2. **Wywolanie narzeadzia** (po inicjalizacji):
```http
POST {monolynx_url}/mcp/ HTTP/1.1
Authorization: Bearer {token}
Mcp-Session-Id: {session_id}
Content-Type: application/json

{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_graph_nodes","arguments":{"project_slug":"myproject","limit":1000}},"id":2}
```

3. **Parsowanie odpowiedzi:**
Odpowiedz to JSON-RPC result. Wynik narzeadzia jest w `result.content[0].text` jako JSON string — parsuj go.

**Dostepne narzedzia MCP:**

| Narzedzie | Argumenty | Opis |
|---|---|---|
| `list_graph_nodes` | `project_slug, type?, search?, limit?` | Lista node'ow (max 1000) |
| `query_graph` | `project_slug, node_type?, limit?` | Caly graf (nodes + edges) |
| `get_graph_stats` | `project_slug` | Statystyki (count per typ) |
| `bulk_create_graph_nodes` | `project_slug, nodes[]` | Tworzenie node'ow. Kazdy: `{type, name, file_path?, line_number?}` |
| `bulk_create_graph_edges` | `project_slug, edges[]` | Tworzenie krawedzi. Kazdy: `{source_id, target_id, type}` |
| `delete_graph_node` | `project_slug, node_id` | Usun node + krawedzie (cascade) |
| `delete_graph_edge` | `project_slug, source_id, target_id, type` | Usun krawedz |

**WAZNE**: `bulk_create_graph_edges` wymaga `source_id` i `target_id` (UUID), nie nazw. Algorytm:
1. Pobierz istniejace node'y → `name → id` map
2. Stworz nowe node'y → zapisz ich ID z odpowiedzi
3. Polacz mapy
4. Twórz krawedzie z ID

### Klasa ASTAnalyzer — analiza kodu

Identyczna logika jak analiza AST — dwa przebiegi:

**Pass 1 — struktura:**
- `File` nodes (kazdy plik .py z wyjatkiem pustych)
- `Class` nodes (ast.ClassDef) + edge CONTAINS (File→Class)
- `Function` nodes (top-level ast.FunctionDef/AsyncFunctionDef) + edge CONTAINS (File→Function)
- `Method` nodes (FunctionDef w ClassDef, bez dunder) + edge CONTAINS (Class→Method)
- `Const` nodes (UPPER_CASE ast.Assign) + edge CONTAINS (File→Const)
- `Module` nodes (z MODULE_MAP) + edges CONTAINS (Module→File)
- `IMPORTS` edges (File→File) — tylko import wewnatrzprojektowe (`from {package_name}.X import Y`)
- `INHERITS` edges (Class→Class) — pomijaj `object`, `BaseModel`, `Base`, `Model` (Django)

**Pass 2 — CALLS:**
- Buduj mape importow per plik: `from {package_name}.X.Y import func` → `func` pochodzi z `X/Y.py`
- Buduj mape aliasow modulow: `from {package_name}.X import Y` → `Y.func()` to `func` z `X/Y.py`
- Dla kazdej funkcji/metody, przeszukaj cialo (ast.Call) i mapuj na znane funkcje

**Typy krawedzi:**
- **Fully managed (create + delete):** CONTAINS, IMPORTS, INHERITS
- **Append-only (create, nigdy delete):** CALLS
- **Nie zarzadzane:** USES, IMPLEMENTS

### Algorytm diff

1. AST → desired state (nodes + edges)
2. `query_graph(limit=1000)` → current state (nodes z ID + edges)
3. Diff:
   - Nowe node'y: w desired, nie w current (match po `type + name`)
   - Usuniete node'y: w current, nie w desired → `delete_graph_node` (cascade)
   - Nowe krawedzie: w desired, nie w current (match po `source_name + target_name + edge_type`)
   - Usuniete krawedzie: w current, nie w desired (tylko MANAGED_EDGE_TYPES)
4. Kolejnosc: usun node'y → usun krawedzie → stworz node'y → stworz krawedzie

### CLI (argparse)

```
python cicd/sync_graph.py [opcje]

--monolynx-url    URL instancji Monolynx (default: env MONOLYNX_URL lub https://open.monolynx.com)
--token           Bearer token (default: env MONOLYNX_GRAPH_TOKEN)
--project-slug    Slug projektu na Monolynx (default: env MONOLYNX_PROJECT_SLUG)
--src-dir         Katalog zrodlowy (default: auto-detekcja)
--dry-run         Tylko pokaz diff, bez zapisu
--verbose         Szczegolowe logi
```

### Wymagania techniczne

- **Zero zewnetrznych zaleznosci** — uzyj wylacznie stdlib (`ast`, `urllib.request`, `json`, `argparse`, `pathlib`, `uuid`, `re`, `logging`)
- **Skrypt standalone** — nie importuj nic z projektu docelowego
- Poprawna obsluga bledow HTTP (retry, timeout, logi)

---

## KROK 3: Dodaj stage do `.gitlab-ci.yml`

Dodaj job `sync-graph` w etapie deploy:

```yaml
sync-graph:
  stage: deploy
  image: python:3.12-slim
  script:
    - python cicd/sync_graph.py
  variables:
    MONOLYNX_URL: "${MONOLYNX_URL}"
    MONOLYNX_GRAPH_TOKEN: "${MONOLYNX_GRAPH_TOKEN}"
    MONOLYNX_PROJECT_SLUG: "${MONOLYNX_PROJECT_SLUG}"
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
      when: on_success
```

Jesli nie istnieje etap `deploy` w stages, dodaj go.

---

## KROK 4: Makefile (opcjonalnie)

Jesli w projekcie jest Makefile, dodaj:

```makefile
sync-graph: ## Synchronizuj graf kodu z Monolynx
	python cicd/sync_graph.py

sync-graph-dry: ## Pokaz zmiany w grafie bez zapisu
	python cicd/sync_graph.py --dry-run --verbose
```

---

## KROK 5: Instrukcje dla uzytkownika

Po zakonczeniu, wyswietl:

```
=== Graf kodu — konfiguracja CI ===

Dodaj te zmienne w GitLab CI/CD Settings → Variables:

  MONOLYNX_URL           = {url}
  MONOLYNX_GRAPH_TOKEN   = (token API z Monolynx → Profil → Tokeny API)
  MONOLYNX_PROJECT_SLUG  = {slug}

Test lokalny:
  MONOLYNX_URL={url} MONOLYNX_GRAPH_TOKEN=osk_xxx MONOLYNX_PROJECT_SLUG={slug} python cicd/sync_graph.py --dry-run

Graf bedzie automatycznie synchronizowany po kazdym merge do main.
```

---

## WAZNE ZASADY

1. **Pomijaj katalogi**: `tests/`, `test/`, `migrations/`, `__pycache__/`, `.venv/`, `venv/`, `node_modules/`
2. **Pomijaj puste pliki** `.py` (np. `__init__.py` bez kodu)
3. **Nazwy node'ow musza byc unikalne** w ramach typu — jesli wykryjesz duplikat, dodaj prefix z katalogu
4. **Prefiksy klas i stalych** — NIE dodawaj prefiksow do klas (`Project`, `User`) ani stalych (`MAX_RETRIES`)
5. **Prefiksy funkcji/metod** — ZAWSZE dodawaj prefix z PREFIX_MAP (np. `payment:process_order`)
6. **Nie modyfikuj istniejacego kodu projektu** — tworzysz TYLKO `cicd/sync_graph.py`, edytujesz `.gitlab-ci.yml` i opcjonalnie `Makefile`
7. **Jezyk komentarzy w skrypcie**: angielski (skrypt jest uniwersalny)
