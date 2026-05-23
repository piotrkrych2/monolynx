# Metoda LLM Wiki (MON-73)

Moduł "LLM Wiki" wg Karpathy'ego: wiki jako narastający artefakt utrzymywany przez agenta AI.

## Pliki
- `services/wiki.py` — extract_wiki_links, sync_backlinks, is_wiki_llm_enabled, ensure_system_page (get-or-create), regenerate_index (zwraca tuple[WikiPage, int]), append_log (append-only), svc_update_system_page (helper MinIO+commit)
- `services/wiki_lint.py` — lint_wiki (async orchestrator) + sub-funkcje SYNC: _find_orphans (async, DB), _find_dead_links(pages, slug_to_id, id_set, content_cache)->tuple[list, dict], _find_contradictions(pages, content_cache)->list, _find_gaps, _resolve_ref. content_cache budowany RAZ w lint_wiki (jeden odczyt MinIO/stronę), przekazywany do sub-funkcji
- `services/wiki_bootstrap.py` — bootstrap_wiki_llm(*, project, user_id, db): włącz flagę+commit -> ensure wiki-schema -> regenerate_index -> append_log. Idempotentny. WYMAGA project z bieżącej sesji
- `services/wiki_templates.py` — DEFAULT_WIKI_SCHEMA (pure str const, regulamin metody)
- `schemas/wiki.py` — WikiBacklinkResponse: source_page_id, target_page_id, anchor_text (NIE "anchor"!)

## Konwencje wykrywane przez lint
- Marker sprzeczności: `_CONTRADICTION_RE = re.compile(r">\s*\*\*Sprzeczność", re.IGNORECASE)` w wiki_lint.py. DEFAULT_WIKI_SCHEMA zawiera ten marker jako przykład — test pilnuje spójności wzorca
- RESERVED_SLUGS = {"wiki-index", "wiki-log", "wiki-schema"} (frozenset w wiki.py) — wykluczane z indeksu i z orphans; create_wiki_page blokuje je dla userów, ensure_system_page omija blokadę
- extract_wiki_links zwraca dict ref->anchor. [[slug]] anchor=slug. [tekst](path) anchor=tekst (lub None gdy pusty). http/https/mailto/# pomijane. Dedup: pierwszy wygrywa (wikilink przed md-link)

## Modele
- Project.wiki_llm_enabled: Bool, default=False, server_default="false"
- WikiBacklink.anchor_text: String(1024) nullable; CASCADE na source+target
- WikiPage: is_ai_touched (Bool), position, minio_path nullable=False

## Patch MinIO w testach
- wiki.py importuje upload_markdown/get_markdown przez `from minio_client import ...` -> patch po stronie konsumenta `monolynx.services.wiki.*` (poprawne)
- UWAGA: wiki_lint.py ma WŁASNY import get_markdown (wiki_lint.py:15) -> patch dla lint MUSI być `monolynx.services.wiki_lint.get_markdown`, nie wiki ani minio_client.
- Warstwa 5: integration (test_wiki_llm_integration.py:620) patchuje target POPRAWNIE. ALE test_mcp_server.py:test_lint_wiki_returns_report_keys patchuje ZŁY target `monolynx.services.minio_client.get_markdown` + docstring klasy mylący. Przechodzi tylko bo projekt ma 0 stron (wiki_lint.py:42-44 pętla pusta, get_markdown nigdy nie wołane) -> brak false-pass, ale patch martwy.

## Pokrycie MON-73 (test_wiki_llm_unit.py 44 + test_wiki_llm_integration.py 18)
- Pokryte: extract_wiki_links, is_wiki_llm_enabled, WikiBacklinkResponse, DEFAULT_WIKI_SCHEMA, _find_dead_links, _find_contradictions (unit); sync_backlinks, ensure_system_page, regenerate_index, append_log, bootstrap_wiki_llm (integration)
- LUKA: lint_wiki (orchestrator), _find_orphans, _find_gaps, _resolve_ref bez testów (tylko wzmianka w test_mcp_server.py:285)

## Sygnatury MCP wiki-LLM (mcp_server.py ~3528-3732, potwierdzone przy review Warstwy 4)
- create_wiki_page(project_slug, title, content, parent_id?, position?) — BRAK type/slug/status. Typ strony = frontmatter YAML w content. Slug auto z tytułu.
- update_wiki_page(project_slug, page_id, title?, content?, position?) — bez type.
- get_wiki_config(project_slug) -> {wiki_llm_enabled, index_page_id, log_page_id, schema_page_id}; NIE wymaga flagi.
- set_wiki_config(project_slug, enabled: bool) — wymaga settings:write.
- bootstrap_wiki_llm(project_slug) — idempotentny, sam włącza flagę, wymaga settings:write.
- lint_wiki(project_slug) -> {orphans, dead_links, contradictions, gaps}; wymaga flagi.
- get_wiki_backlinks(project_slug, page_id) -> {incoming, outgoing}; wymaga flagi.
- regenerate_wiki_index(project_slug), append_wiki_log(project_slug, entry) — wymagają flagi.
- Gating: lint/backlinks/regenerate/append wymagają wiki_llm_enabled; get_wiki_config/set_wiki_config/bootstrap działają bez.

## Warstwa 4 — skille metody (MON-73)
- Plugin skille: plugin/skills/{wiki-init,wiki-ingest,wiki-lint}/SKILL.md (frontmatter BEZ name:, z user-invocable+argument-hint+allowed-tools+Bash); static kopie src/monolynx/static/skills/monolynx-{wiki-init,wiki-ingest,wiki-lint}/SKILL.md (name: monolynx-<skill>).
- Body plugin vs static MUSI być identyczne (różnica tylko frontmatter). Wyjątek: search — static używa placeholderów <PROJECT-ID> i nie ma bash-snippetu "Ustalenie slug" (podstawiane przez install_monolynx_skills) — to design, nie błąd.
- plugin/README.md: licznik 10 skilli + 7 agentów (oba "7" = agenci). plugin.json version bump 1.1.1. marketplace.json (root .claude-plugin/) bez pola version, nie ruszany.
- Review Warstwa 4 MON-73: 90/100 APPROVED. LOW: rozjazd body wiki-ingest plugin:165 "czlowiekowi" vs static:170 "czlowiek".

## Warstwa 5 (finalna) MON-73 — review
- frontend 95/100, qa 88/100, writer 86/100, wszyscy APPROVED, brak blokerów.
- Frontend: panel "Linki przychodzące" page_detail.html:142-170 + handler wiki.py:365,379. Gating poprawny, selectinload source_page (brak MissingGreenlet), pełne diakrytyki, import bez duplikatu.
- QA: klasa TestWikiLlmMcpTools w test_mcp_server.py:4837 — 12 testów, 7 narzędzi + idempotencja + 4x gating-OFF. settings:write przez mcp_member role="owner" (DEFAULT_ROLE_PERMISSIONS legacy fallback, BEZ patcha check_permission). Patch MinIO dla bootstrap/regenerate/append POPRAWNY (wiki.* namespace). MINUS: zły patch target lint (patrz wyżej) + zbędne patche MinIO w set_wiki_config (nie używa MinIO).
- Writer CLAUDE.md: liczniki narzędzia 107 (=faktyczne @mcp.tool()), modele 23 (+WikiBacklink), plugin 1.1.1, skille 10 (7+3 wiki). em-dash w DODANYCH liniach=0 (5 em-dash w diff to cudze niezmienione fragmenty). MINUS: NIE dodał head migracji f2a3b4c5d6e7 do CLAUDE.md (ticket żądał; CLAUDE.md normalnie nie listuje headów).
- PROJEKTOWO: repo ma 4 heady alembica (g7b8c9d0e1f2, a79c5a7c5c7b, 1694c9ca760f, f2a3b4c5d6e7) — 3 pre-existing, NIE z tej pracy. f2a3 chainuje c571fb82cd74->17420ab13509. make migrate może wymagać alembic merge.
