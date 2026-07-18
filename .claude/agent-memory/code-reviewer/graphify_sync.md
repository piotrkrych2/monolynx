---
name: graphify-sync
description: MON-115/116/118 graphify->Monolynx graf - kontrakt replace_graph, mapowanie taksonomii, transport MCP w cicd/sync_graph.py
metadata:
  type: project
---

# Graphify -> Monolynx sync (moduł Połączenia)

Podział ról: graphify (zewn. ekstraktor tree-sitter AST, pakiet PyPI `graphifyy` - podwójne y) generuje `graphify-out/graph.json` (NetworkX node-link: klucze `nodes` + **`links`**, NIE `edges`). `cicd/sync_graph.py` (cienki mapper, zero AST, stdlib only) mapuje i wypycha przez MCP tool `replace_graph`.

## Kontrakt replace_graph (services/graph.py:342 `_validate_replace_payload` L316)
- Node: {id, name, type} WYMAGANE (truthy), type in GRAPH_NODE_TYPES (File/Class/Method/Function/Const/Module, constants.py:87), id unikalne. Opcjonalne: file_path, line_number, metadata.
- Edge: {source_id, target_id, type} WYMAGANE, type in GRAPH_EDGE_TYPES (CONTAINS/CALLS/IMPORTS/INHERITS/USES/IMPLEMENTS, constants.py:89). Opcjonalne: metadata.
- Walidacja PRZED kasowaniem (fail-fast) - błędny payload NIE zostawia pustego grafu. Delete+insert w jednej tx. Limity REPLACE_MAX_NODES=20000/REPLACE_MAX_EDGES=60000. clear_first=False dokłada batche. Batch UNWIND 500.
- id brany z wejścia (odróżnia od create_node). MCP wrapper: mcp_server.py:4157.

## Mapowanie (wiki page 2e2ac4a6-828f-44dd-ae52-3ab5701c6410 "Mapowanie taksonomii Graphify -> Monolynx", MON-115)
- classify_node kolejność (pierwsza wygrywa): rationale/pusty source_file/type==package -> skip; label.endswith(.py)->File; method_target|label."."->Method; method_source->Class; "()"->Function; CamelCase(isupper+isidentifier)->Class; else Const.
- RELATION_TO_EDGE_TYPE: calls/indirect_call->CALLS, contains/method->CONTAINS, imports/imports_from/re_exports->IMPORTS, inherits->INHERITS, uses->USES. references/rationale_for pomijane (code-only v1).
- metadata edge = {source_relation, confidence(EXTRACTED|INFERRED)}; metadata node = {community}. line_number ze source_location "L42"->42. name = label bez "()" i wiodącej kropki.
- Wolumen monolynx po mapowaniu: ~748 nodes / 2201 edges (dry-run). Wiki symulacja ~742/2192.

## Transport MCP (wzorzec) - referencja: cicd/wiki_post_merge.py
- endpoint `{url}/mcp/` (trailing slash!), Bearer, initialize (protocolVersion "2025-03-26") -> zapisz nagłówek `Mcp-Session-Id` z odpowiedzi -> doklejaj do tools/call. result.content[0].text = JSON string. isError -> błąd. retry x3, HTTP<500 rzuca od razu. Oba skrypty OMIJAJĄ notifications/initialized (serwer nie wymaga) - spójne.
- Token env: MONOLYNX_MCP_TOKEN (NIE stare MONOLYNX_GRAPH_TOKEN).

## CI (.gitlab-ci.yml job sync-graph)
- allow_failure:true, guard `command -v graphify || exit 0`, NIE instaluje graphify (setup runnera), BEZ `image:` (leci na obrazie runnera z graphify+python; brak globalnego default:image w pliku). Sąsiedni wiki-post-merge ma python:3.12-slim (nie potrzebuje graphify).

## Pitfall złapany w MON-118
- Em-dash (U+2014) w SKILL.md: BŁĄD. Reguła CRITICAL Piotra + checklist. Wszystkie inne pluginowe SKILL.md mają 0 em-dash; static/ wersje work/ticket-create/review mają dużo (rozjazd). Sprawdzaj `grep -c "—"` na obu kopiach.
- Parytet static/ vs plugin/ SKILL.md: `diff` musi zwracać TYLKO linię 2 `name: monolynx-<skill>`. Reszta identyczna.
