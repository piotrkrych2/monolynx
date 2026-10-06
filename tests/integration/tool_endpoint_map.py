"""Mapa kontraktu MCP <-> API v2 (MON-131).

`TOOL_TO_ENDPOINT` wiąże 49 narzędzi MCP z endpointami `/api/v2`, które wystawiają
tę samą operację (Projects, Scrum, Wiki, 5 odczytów grafu Połączeń, MON-248, oraz 4 narzędzia Bloga, MON-261 i MON-262).
`INTENTIONALLY_UNMAPPED` opisuje powód,
dla którego pozostałe narzędzia nie mają odpowiednika w v2. Każde narzędzie
zarejestrowane w `mcp_server.mcp` musi wystąpić dokładnie w jednym z tych słowników;
pilnuje tego `tests/integration/test_api_v2_parity.py`.

Plik jest kontraktem także dla testów trwałości commita
(`tests/integration/test_api_v2_persistence.py`), które filtrują wpisy po `is_write`.
Nazwy i pola `ToolEndpointSpec` są częścią tego kontraktu.

Placeholdery w `path` mają stałe nazwy: `{project_slug}`, `{ticket_id}`,
`{criterion_id}`, `{sprint_id}`, `{page_id}`, `{member_id}`, `{node_id}`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolEndpointSpec:
    """Endpoint API v2 odpowiadający narzędziu MCP."""

    method: str
    path: str
    is_write: bool
    # Nazwa parametru/pola MCP -> nazwa w v2, tylko gdy się różnią.
    field_map: dict[str, str] = field(default_factory=dict)
    # Świadome, uzasadnione różnice kontraktu, które testy parytetu pomijają jawnie.
    known_differences: str = ""


_PROJECTS = "/api/v2/projects"
_PROJECT = "/api/v2/projects/{project_slug}"
_TICKETS = "/api/v2/projects/{project_slug}/tickets"
_TICKET = "/api/v2/projects/{project_slug}/tickets/{ticket_id}"
_CRITERIA = "/api/v2/projects/{project_slug}/tickets/{ticket_id}/acceptance-criteria"
_SPRINTS = "/api/v2/projects/{project_slug}/sprints"
_SPRINT = "/api/v2/projects/{project_slug}/sprints/{sprint_id}"
_WIKI_PAGES = "/api/v2/projects/{project_slug}/wiki/pages"
_WIKI_PAGE = "/api/v2/projects/{project_slug}/wiki/pages/{page_id}"
_GRAPH = "/api/v2/projects/{project_slug}/graph"
_BLOG_POSTS = "/api/v2/projects/{project_slug}/blog/posts"
_BLOG_POST = "/api/v2/projects/{project_slug}/blog/posts/{page_id}"
_BLOG_LINT = "/api/v2/projects/{project_slug}/blog/posts/{page_id}/lint"
_BLOG_PUBLISH = "/api/v2/projects/{project_slug}/blog/posts/{page_id}/publish"
_BLOG_UNPUBLISH = "/api/v2/projects/{project_slug}/blog/posts/{page_id}/unpublish"
_BLOG_WRITE_NOTE = (
    "Zapis wymaga uprawnienia blog:write na koncie (MON-270): MCP ukrywa to narzędzie na liście dla konta bez uprawnienia "
    "(wywołanie wprost zwraca Brak uprawnienia blog:write), v2 zwraca 403 problem+json z tym samym tekstem w detail."
)


TOOL_TO_ENDPOINT: dict[str, ToolEndpointSpec] = {
    # --- Projects (11) ---
    "list_projects": ToolEndpointSpec(
        "GET",
        _PROJECTS,
        is_write=False,
        known_differences="v2 dodatkowo zwraca id, code i description; MCP zwraca tylko name, slug, role, created_at.",
    ),
    "get_project": ToolEndpointSpec(
        "GET",
        _PROJECT,
        is_write=False,
        known_differences="v2 dodatkowo zwraca code.",
    ),
    "create_project": ToolEndpointSpec(
        "POST",
        _PROJECTS,
        is_write=True,
        known_differences="MCP dokłada do odpowiedzi pole message.",
    ),
    "update_project": ToolEndpointSpec(
        "PATCH",
        _PROJECT,
        is_write=True,
        field_map={"new_slug": "slug"},
    ),
    "delete_project": ToolEndpointSpec(
        "DELETE",
        _PROJECT,
        is_write=True,
        known_differences=(
            "MCP wymaga confirm=true (bez niego ValueError) i zwraca message oraz deleted_at; "
            "v2 nie ma parametru confirm, zawsze wykonuje soft delete i odpowiada 204 bez body."
        ),
    ),
    "invite_member": ToolEndpointSpec(
        "POST",
        "/api/v2/projects/{project_slug}/members",
        is_write=True,
        field_map={"user_email": "email"},
        known_differences=(
            "MCP zwraca message, user_email, role i (tylko dla nowego konta) expires_at; "
            "v2 zwraca email, role, is_new_user oraz expires_at (null dla istniejącego konta). "
            "Żaden kanał nie zwraca tokenu zaproszenia - trafia wyłącznie do maila."
        ),
    ),
    "list_members": ToolEndpointSpec("GET", "/api/v2/projects/{project_slug}/members", is_write=False),
    "remove_member": ToolEndpointSpec(
        "DELETE",
        "/api/v2/projects/{project_slug}/members/{member_id}",
        is_write=True,
        field_map={"email": "member_id"},
        known_differences=(
            "MCP identyfikuje członka adresem email i zwraca message; v2 przyjmuje w ścieżce user_id członka "
            "(to samo pole co user_id z list_members) i odpowiada 204 bez body."
        ),
    ),
    "get_project_summary": ToolEndpointSpec(
        "GET",
        "/api/v2/projects/{project_slug}/summary",
        is_write=False,
        known_differences="MCP dokłada klucz project z polami name i slug; v2 go nie zwraca.",
    ),
    "list_labels": ToolEndpointSpec("GET", "/api/v2/projects/{project_slug}/labels", is_write=False),
    "create_label": ToolEndpointSpec(
        "POST",
        "/api/v2/projects/{project_slug}/labels",
        is_write=True,
        known_differences="MCP dokłada do odpowiedzi pole message.",
    ),
    # --- Tickets (14) ---
    "list_tickets": ToolEndpointSpec(
        "GET",
        _TICKETS,
        is_write=False,
        known_differences=(
            "MCP zwraca tekst (tabela pipe-separated z tytułem obciętym do 40 znaków i nazwami sprintu oraz etykiet); "
            "v2 zwraca PaginatedResponse z id sprintu, id etykiet i pełnym tytułem. Blokery: MCP podaje klucze w ostatniej kolumnie "
            "tekstowej, v2 pełne obiekty blocked_by (id, key, title, status). "
            "v2 ma dodatkowo parametr query compact (elementy bez description), którego tool MCP nie ma."
        ),
    ),
    "search_tickets": ToolEndpointSpec(
        "GET",
        f"{_TICKETS}/search",
        is_write=False,
        field_map={"results": "items"},
    ),
    "get_ticket": ToolEndpointSpec(
        "GET",
        _TICKET,
        is_write=False,
        known_differences=(
            "MCP zwraca tekst i dokłada sekcję Rozliczenia, której v2 nie zwraca. "
            "MCP pokazuje autora komentarza utworzonego przez AI jako 'ai', v2 jako email."
        ),
    ),
    "create_ticket": ToolEndpointSpec(
        "POST",
        _TICKETS,
        is_write=True,
        known_differences=(
            "MCP zwraca skrót (id, key, title, status, due_date, spec_page_id, created_via_ai, acceptance_criteria_count, "
            "message); v2 zwraca pełny TicketDetailResponse. Uprawnienia: MCP wymaga tylko scrum:write; v2 "
            "czyta ticket po zapisie, więc przed zapisem wymaga też scrum:read (rola z samym scrum:write dostaje 403 "
            "'Brak uprawnienia scrum:read' i nic nie zapisuje)."
        ),
    ),
    "update_ticket": ToolEndpointSpec(
        "PATCH",
        _TICKET,
        is_write=True,
        known_differences=(
            "MCP zwraca skrót (id, key, title, status, due_date, message); v2 zwraca pełny TicketDetailResponse. Uprawnienia: "
            "MCP wymaga tylko scrum:write; v2 czyta ticket po zapisie, więc przed zapisem wymaga też scrum:read (rola z samym "
            "scrum:write dostaje 403 'Brak uprawnienia scrum:read' i nic nie zapisuje)."
        ),
    ),
    "delete_ticket": ToolEndpointSpec(
        "DELETE",
        _TICKET,
        is_write=True,
        known_differences="MCP zwraca message z tytułem; v2 odpowiada 204 bez body.",
    ),
    "bulk_update_tickets": ToolEndpointSpec("PATCH", f"{_TICKETS}/bulk", is_write=True),
    "get_board": ToolEndpointSpec(
        "GET",
        "/api/v2/projects/{project_slug}/board",
        is_write=False,
        known_differences=("MCP zwraca tekst (bez sprintu: '(Brak aktywnego sprintu)'); v2 zwraca sprint (null bez aktywnego sprintu) i kolumny."),
    ),
    "list_comments": ToolEndpointSpec("GET", f"{_TICKET}/comments", is_write=False),
    "add_comment": ToolEndpointSpec(
        "POST",
        f"{_TICKET}/comments",
        is_write=True,
        known_differences="MCP zwraca tylko id, message i created_via_ai; v2 zwraca pełny komentarz.",
    ),
    "list_acceptance_criteria": ToolEndpointSpec(
        "GET",
        _CRITERIA,
        is_write=False,
        known_differences=(
            "MCP zwraca tekst z autorem i datą ukończenia kryterium; v2 zwraca id, description, is_completed, "
            "created_via_ai, position i created_at (bez autora)."
        ),
    ),
    "add_acceptance_criterion": ToolEndpointSpec(
        "POST",
        _CRITERIA,
        is_write=True,
        known_differences="MCP zwraca tylko id, message, created_via_ai i position; v2 zwraca pełne kryterium.",
    ),
    "update_acceptance_criterion": ToolEndpointSpec(
        "PATCH",
        f"{_CRITERIA}/{{criterion_id}}",
        is_write=True,
        known_differences="MCP zwraca id, message, is_completed i description; v2 zwraca pełne kryterium.",
    ),
    "delete_acceptance_criterion": ToolEndpointSpec(
        "DELETE",
        f"{_CRITERIA}/{{criterion_id}}",
        is_write=True,
        known_differences="MCP zwraca message i id; v2 odpowiada 204 bez body.",
    ),
    # --- Sprints (7) ---
    "list_sprints": ToolEndpointSpec("GET", _SPRINTS, is_write=False),
    "get_sprint": ToolEndpointSpec(
        "GET",
        _SPRINT,
        is_write=False,
        known_differences="MCP zwraca tekst z tabelą ticketów sprintu i sumami punktów; v2 zwraca SprintResponse bez listy ticketów.",
    ),
    "create_sprint": ToolEndpointSpec(
        "POST",
        _SPRINTS,
        is_write=True,
        known_differences="MCP zwraca skrót (id, name, status, message); v2 zwraca pełny SprintResponse.",
    ),
    "update_sprint": ToolEndpointSpec("PATCH", _SPRINT, is_write=True),
    "start_sprint": ToolEndpointSpec(
        "POST",
        f"{_SPRINT}/start",
        is_write=True,
        known_differences=(
            "MCP zwraca message i sprint_id; v2 zwraca pełny SprintResponse ze statusem active. Uprawnienia: MCP wymaga tylko "
            "scrum:write; v2 czyta sprint po zapisie, więc przed zapisem wymaga też scrum:read (rola z samym scrum:write dostaje "
            "403 'Brak uprawnienia scrum:read' i nic nie zapisuje)."
        ),
    ),
    "complete_sprint": ToolEndpointSpec(
        "POST",
        f"{_SPRINT}/complete",
        is_write=True,
        known_differences=(
            "MCP zwraca message i sprint_id; v2 zwraca pełny SprintResponse ze statusem completed. Uprawnienia: MCP wymaga tylko "
            "scrum:write; v2 czyta sprint po zapisie, więc przed zapisem wymaga też scrum:read (rola z samym scrum:write dostaje "
            "403 'Brak uprawnienia scrum:read' i nic nie zapisuje)."
        ),
    ),
    "get_burndown": ToolEndpointSpec("GET", "/api/v2/projects/{project_slug}/burndown", is_write=False),
    # --- Wiki (8) ---
    "list_wiki_pages": ToolEndpointSpec(
        "GET",
        _WIKI_PAGES,
        is_write=False,
        known_differences="MCP zwraca tekst (drzewo z wcięciami, data updated_at bez godziny); v2 zwraca depth, parent_id i pełne updated_at.",
    ),
    "get_wiki_page": ToolEndpointSpec("GET", _WIKI_PAGE, is_write=False),
    "create_wiki_page": ToolEndpointSpec(
        "POST",
        _WIKI_PAGES,
        is_write=True,
        known_differences=(
            "MCP tworzy stronę z is_ai_touched=true, v2 z is_ai_touched=false (router v2 woła serwis z is_ai=False); "
            "MCP dokłada message; v2 zwraca też parent_id i position."
        ),
    ),
    "update_wiki_page": ToolEndpointSpec(
        "PATCH",
        _WIKI_PAGE,
        is_write=True,
        known_differences=(
            "MCP ustawia is_ai_touched=true, v2 nie zmienia tej flagi (is_ai=false); MCP dokłada message; v2 zwraca też parent_id i position."
        ),
    ),
    "delete_wiki_page": ToolEndpointSpec(
        "DELETE",
        _WIKI_PAGE,
        is_write=True,
        known_differences="MCP zwraca message z tytułem; v2 odpowiada 204 bez body.",
    ),
    "search_wiki": ToolEndpointSpec(
        "GET",
        "/api/v2/projects/{project_slug}/wiki/search",
        is_write=False,
        known_differences="v2 zwraca goły JSON array bez koperty paginacji (tak jak MCP zwraca listę).",
    ),
    "get_wiki_config": ToolEndpointSpec("GET", "/api/v2/projects/{project_slug}/wiki/config", is_write=False),
    "set_wiki_config": ToolEndpointSpec(
        "PATCH",
        "/api/v2/projects/{project_slug}/wiki/config",
        is_write=True,
        known_differences="MCP zwraca wiki_llm_enabled i message; v2 zwraca tylko wiki_llm_enabled.",
    ),
    # --- Blog (6) ---
    "list_blog_posts": ToolEndpointSpec(
        "GET",
        _BLOG_POSTS,
        is_write=False,
        known_differences="MCP zwraca goły JSON array wpisów; v2 owija go w kopertę paginacji (items, page, per_page, total, total_pages).",
    ),
    "get_blog_post": ToolEndpointSpec("GET", _BLOG_POST, is_write=False),
    "lint_blog_post": ToolEndpointSpec(
        "GET",
        _BLOG_LINT,
        is_write=False,
        known_differences="MCP dokłada do wyniku pole table (tabela problemów z linią oceny); v2 zwraca tylko score, liczniki i issues.",
    ),
    "set_blog_post_meta": ToolEndpointSpec(
        "PATCH",
        _BLOG_POST,
        is_write=True,
        known_differences=(
            "MCP: pominięty parametr (None) = bez zmian, pusty tekst w translation_of_id, cover_attachment_id i last_verified_at "
            "czyści pole; v2: pole nieobecne w body = bez zmian, null czyści pole. " + _BLOG_WRITE_NOTE
        ),
    ),
    "publish_blog_post": ToolEndpointSpec(
        "POST",
        _BLOG_PUBLISH,
        is_write=True,
        known_differences=(
            "force to parametr MCP, a w v2 opcjonalne pole body JSON (brak body = false); odmowa przy błędach lintu bez force "
            "to ValueError z tekstem w MCP i 422 problem+json w v2 (ten sam komunikat w detail). " + _BLOG_WRITE_NOTE
        ),
    ),
    "unpublish_blog_post": ToolEndpointSpec("POST", _BLOG_UNPUBLISH, is_write=True, known_differences=_BLOG_WRITE_NOTE),
    # --- Graph (5) ---
    "list_graph_nodes": ToolEndpointSpec(
        "GET",
        f"{_GRAPH}/nodes",
        is_write=False,
        known_differences="v2 zwraca ten sam goły JSON array co MCP, bez koperty paginacji; limit 1..1000 jest walidowany po stronie v2 (poza: 422).",
    ),
    "get_graph_node": ToolEndpointSpec(
        "GET",
        f"{_GRAPH}/nodes/{{node_id}}",
        is_write=False,
        known_differences=(
            "MCP zwraca tekst Arrow DSL (sekcje Depth i krawędzie pogrupowane po typie relacji); v2 zwraca JSON "
            "{node, nodes, edges, depth_map} i dodatkowo node (węzeł startowy). Nieistniejący node: MCP ValueError "
            "'Node nie istnieje', v2 404 problem+json z tym samym detail. depth >= 1 jest walidowane tylko w v2 "
            "(depth=0: 422 problem+json); tool MCP nie sprawdza dolnej granicy depth."
        ),
    ),
    "query_graph": ToolEndpointSpec(
        "GET",
        f"{_GRAPH}/query",
        is_write=False,
        known_differences=(
            "MCP zwraca tekst Arrow DSL; v2 zwraca JSON {nodes, edges}, z którego format_graph_dsl daje ten sam tekst. "
            "Puste search: MCP ValueError, v2 422 problem+json z tym samym detail. limit 1..1000 jest walidowany tylko "
            "w v2 (poza zakresem: 422 problem+json); tool MCP nie sprawdza granic limit."
        ),
    ),
    "find_graph_path": ToolEndpointSpec(
        "GET",
        f"{_GRAPH}/path",
        is_write=False,
        known_differences=(
            "MCP zwraca tekst Arrow DSL; v2 zwraca JSON {nodes, edges}, z którego format_graph_dsl daje ten sam tekst (brak ścieżki: puste listy)."
        ),
    ),
    "get_graph_stats": ToolEndpointSpec("GET", f"{_GRAPH}/stats", is_write=False),
}


_OUT_OF_SCOPE_MODULE = "moduł {module} poza zakresem API v2 (MON-142 obejmuje tylko Projects, Scrum i Wiki)"
_GRAPH_WRITE_REASON = (
    "zapis grafu Połączeń robi wyłącznie synchronizacja cicd/sync_graph.py przez MCP (replace_graph); "
    "API v2 i CLI wystawiają tylko odczyt grafu (MON-248)"
)

INTENTIONALLY_UNMAPPED: dict[str, str] = {
    **dict.fromkeys(
        ["list_issues", "get_issue", "update_issue_status", "create_issue", "create_ticket_from_issue"],
        _OUT_OF_SCOPE_MODULE.format(module="500ki (Error Tracking)"),
    ),
    **dict.fromkeys(
        ["list_monitors", "get_monitor", "create_monitor", "update_monitor", "delete_monitor"],
        _OUT_OF_SCOPE_MODULE.format(module="Monitoring"),
    ),
    **dict.fromkeys(
        ["list_heartbeats", "get_heartbeat", "create_heartbeat", "update_heartbeat", "delete_heartbeat"],
        _OUT_OF_SCOPE_MODULE.format(module="Heartbeat"),
    ),
    **dict.fromkeys(
        [
            "create_graph_node",
            "delete_graph_node",
            "create_graph_edge",
            "delete_graph_edge",
            "bulk_create_graph_nodes",
            "bulk_create_graph_edges",
            "replace_graph",
        ],
        _GRAPH_WRITE_REASON,
    ),
    **dict.fromkeys(
        [
            "bootstrap_wiki_llm",
            "lint_wiki",
            "get_wiki_backlinks",
            "regenerate_wiki_index",
            "append_wiki_log",
            "get_wiki_attachment",
            "add_wiki_page_attachment",
            "list_wiki_page_attachments",
            "add_wiki_file",
            "get_wiki_file",
            "update_wiki_file",
            "list_wiki_files",
        ],
        "metoda LLM Wiki, załączniki i pliki wiki poza zakresem API v2 (MON-142 obejmuje 8 narzędzi wiki: strony, szukanie, konfigurację)",
    ),
    **dict.fromkeys(
        ["add_attachment", "get_attachment", "log_time", "get_work_report", "get_activity_log"],
        "załączniki ticketów, czas pracy, raporty i dziennik aktywności poza zakresem API v2",
    ),
    **dict.fromkeys(
        ["list_roles", "create_role", "update_role", "delete_role", "assign_role", "get_member_permissions"],
        _OUT_OF_SCOPE_MODULE.format(module="Role i uprawnienia (RBAC)"),
    ),
    **dict.fromkeys(
        [
            "list_settlements",
            "get_settlement",
            "create_settlement",
            "update_settlement",
            "delete_settlement",
            "change_settlement_status",
            "link_ticket_to_settlement",
            "unlink_ticket_from_settlement",
            "list_settlement_tickets",
            "list_settlement_attachments",
            "add_settlement_attachment",
            "get_settlement_attachment",
            "delete_settlement_attachment",
        ],
        _OUT_OF_SCOPE_MODULE.format(module="Rozliczenia"),
    ),
    **dict.fromkeys(
        [
            "schedule_ticket",
            "update_work_plan_entry",
            "delete_work_plan_entry",
            "list_work_plan",
            "get_today_tasks",
            "get_ticket_schedule",
        ],
        _OUT_OF_SCOPE_MODULE.format(module="Plan pracy (Gantt)"),
    ),
    **dict.fromkeys(
        [
            "create_pipeline",
            "create_pipeline_job",
            "update_pipeline_job",
            "append_job_log",
            "finish_pipeline",
            "list_pipelines",
            "get_pipeline",
            "get_pipeline_job_log",
            "clean_pipeline_logs",
        ],
        _OUT_OF_SCOPE_MODULE.format(module="Pipelines"),
    ),
    "install_monolynx_skills": "dystrybucja skilli wyłącznie przez MCP, bez odpowiednika REST",
}
