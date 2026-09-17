## KROK 3: Wygeneruj `cicd/sync_graph.py`

Utworz katalog `cicd/` i plik `cicd/sync_graph.py`. **Zero analizy AST** - to czysty mapper + klient MCP. Wymagania:

### 3a. Klient MCP (stdlib only, bez zaleznosci)

Klasa `MonolynxClient` - MCP Streamable HTTP (JSON-RPC przez `urllib.request`):
- naglowki: `Authorization: Bearer {token}`, `Content-Type: application/json`, `Accept: application/json`
- endpoint `{url}/mcp/`; sekwencja: `initialize` (protocolVersion `2025-03-26`) -> zapisz naglowek odpowiedzi `Mcp-Session-Id` -> doklejaj go do kolejnych requestow -> `tools/call`
- wynik toola: `result.content[0].text` to JSON string -> `json.loads`; jesli `result.isError` -> blad
- retry x3 z backoff; HTTP < 500 rzuca od razu
- jedna metoda domenowa: `replace_graph(nodes, edges, clear_first=True)` wolajaca tool `replace_graph` z argumentami `{project_slug, nodes, edges, clear_first}`

### 3b. Mapowanie taksonomii (zrodlo prawdy: wiki "Mapowanie taksonomii Graphify -> Monolynx")

`graph.json` to format NetworkX node-link: klucze `nodes` + **`links`** (nie `edges`).

**Krawedzie** - mapa relacji (relacje spoza mapy pomijamy: `references`, `rationale_for` - graf code-only):

```python
RELATION_TO_EDGE_TYPE = {
    "calls": "CALLS", "indirect_call": "CALLS",
    "contains": "CONTAINS", "method": "CONTAINS",
    "imports": "IMPORTS", "imports_from": "IMPORTS", "re_exports": "IMPORTS",
    "inherits": "INHERITS", "uses": "USES",
}
```

Kazda krawedz dostaje `metadata: {"source_relation": <relacja graphify>, "confidence": <EXTRACTED|INFERRED>}`. Krawedzie INFERRED zostaja (odroznialne w UI). Krawedz, ktorej endpoint odpadl po filtrze wezlow - pomijana.

**Wezly** - graphify NIE typuje wezlow kodu; typ wnioskuj heurystykami (pierwsza pasujaca wygrywa; zweryfikowane na graphify 0.9.18):

| Warunek | Wynik |
|---|---|
| `file_type == "rationale"` / pusty `source_file` / `type == "package"` | pomin (v1 code-only) |
| `label` konczy sie `.py` (lub rozszerzeniem jezyka projektu) | `File` |
| target krawedzi `method` lub `label` zaczyna sie od `.` | `Method` |
| source krawedzi `method` | `Class` |
| `label` konczy sie `()` | `Function` |
| `label` CamelCase (identyfikator z wielkiej litery) | `Class` |
| pozostale | `Const` |

Pola wezla: `id` (z graph.json - WYMAGANE, krawedzie referuja po nim), `name` (label bez sufiksu `()` i wiodacej kropki), `file_path` = `source_file`, `line_number` = `source_location` bez prefiksu `L`, `metadata.community` = `community`.

### 3c. Obsluga brakow (twarde wymagania)

- Brak `graphify-out/graph.json` -> `log.error("Missing ... - run graphify update .")` + **`exit 0`** (bez tracebacku; build projektu przechodzi)
- Brak `MONOLYNX_GRAPH_TOKEN` -> log + `exit 0` (skip)
- Brak sluga -> log.error + `exit 1`
- Payload > 20 000 wezlow / 60 000 krawedzi -> log.error ("tighten .graphifyignore") + `exit 1`
- `--dry-run` -> tylko mapowanie i liczby, bez requestow

### 3d. CLI

`argparse`: `--monolynx-url` (env `MONOLYNX_URL`), `--token` (env `MONOLYNX_GRAPH_TOKEN`), `--project-slug` (env `MONOLYNX_PROJECT_SLUG`), `--graph-json` (default `graphify-out/graph.json`), `--dry-run`, `--verbose`. Komentarze w skrypcie po angielsku.

**Wzorzec referencyjny**: repo monolynx ma taki skrypt w `cicd/sync_graph.py` - mozesz go skopiowac i dostosowac rozszerzenie plikow w heurystyce `File` do jezyka projektu.

---
