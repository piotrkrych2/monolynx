"""Feature pages content and routes for the landing site."""

from __future__ import annotations

from typing import Any

# SVG icons reused across feature pages
_ICON_500KI = '<svg class="w-6 h-6 text-red-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"/></svg>'
_ICON_SCRUM = '<svg class="w-6 h-6 text-blue-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 4.5v15m6-15v15m-10.875 0h15.75c.621 0 1.125-.504 1.125-1.125V5.625c0-.621-.504-1.125-1.125-1.125H4.125C3.504 4.5 3 5.004 3 5.625v12.75c0 .621.504 1.125 1.125 1.125Z"/></svg>'
_ICON_MONITORING = '<svg class="w-6 h-6 text-green-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z"/></svg>'
_ICON_HEARTBEAT = '<svg class="w-6 h-6 text-pink-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M21 8.25c0-2.485-2.099-4.5-4.688-4.5-1.935 0-3.597 1.126-4.312 2.733-.715-1.607-2.377-2.733-4.313-2.733C5.1 3.75 3 5.765 3 8.25c0 7.22 9 12 9 12s9-4.78 9-12Z"/></svg>'
_ICON_WIKI = '<svg class="w-6 h-6 text-amber-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"/></svg>'
_ICON_CONNECTIONS = '<svg class="w-6 h-6 text-purple-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M7.217 10.907a2.25 2.25 0 1 0 0 2.186m0-2.186c.18.324.283.696.283 1.093s-.103.77-.283 1.093m0-2.186 9.566-5.314m-9.566 7.5 9.566 5.314m0 0a2.25 2.25 0 1 0 3.935 2.186 2.25 2.25 0 0 0-3.935-2.186Zm0-12.814a2.25 2.25 0 1 0 3.933-2.185 2.25 2.25 0 0 0-3.933 2.185Z"/></svg>'
_ICON_REPORTS = '<svg class="w-6 h-6 text-cyan-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z"/></svg>'


def _other_modules(exclude: str, lang: str) -> list[dict[str, str]]:
    """Build 'other modules' list excluding current one."""
    all_modules = [
        {
            "slug": "500ki",
            "color": "red",
            "name": "500s" if lang == "en" else "500ki",
            "short": "Error tracking" if lang == "en" else "Śledzenie błędów",
        },
        {"slug": "scrum", "color": "blue", "name": "Scrum", "short": "Agile project management" if lang == "en" else "Zarządzanie projektami"},
        {"slug": "monitoring", "color": "green", "name": "Monitoring", "short": "URL health checks" if lang == "en" else "Monitorowanie URL"},
        {"slug": "heartbeat", "color": "pink", "name": "Heartbeat", "short": "Cron job monitoring" if lang == "en" else "Monitoring zadań cron"},
        {
            "slug": "wiki",
            "color": "amber",
            "name": "Wiki",
            "short": "Documentation & RAG search" if lang == "en" else "Dokumentacja i wyszukiwanie RAG",
        },
        {
            "slug": "connections",
            "color": "purple",
            "name": "Connections" if lang == "en" else "Połączenia",
            "short": "Dependency graph" if lang == "en" else "Graf zależności",
        },
        {
            "slug": "reports",
            "color": "cyan",
            "name": "Reports" if lang == "en" else "Raporty",
            "short": "Work analytics & PDF export" if lang == "en" else "Analityka pracy i eksport PDF",
        },
    ]
    return [m for m in all_modules if m["slug"] != exclude]


def get_feature_content(slug: str, lang: str) -> dict[str, Any] | None:
    """Return feature page content for given slug and language."""
    builder = _FEATURES.get(slug)
    if builder is None:
        return None
    result: dict[str, Any] = builder(lang)
    return result


def _feature_500ki(lang: str) -> dict[str, Any]:
    if lang == "pl":
        return {
            "title": "500ki — Śledzenie błędów",
            "color": "red",
            "icon": _ICON_500KI,
            "badge": "Error Tracking",
            "screenshot": "500ki-issues-list.png",
            "screenshot_2": None,
            "headline": "Przechwytuj błędy, zanim użytkownicy je zgłoszą",
            "description": (
                "500ki automatycznie grupuje wyjątki z Twoich aplikacji za pomocą inteligentnego fingerprintingu. "
                "Lekkie SDK dla Django przechwytuje błędy w tle — bez wpływu na wydajność aplikacji."
            ),
            "screenshot_hint": "Lista issues z liczbą wystąpień i statusem",
            "screenshot_hint_2": "Szczegóły błędu ze stacktrace i kontekstem requestu",
            "features": [
                {
                    "title": "Inteligentne grupowanie",
                    "desc": "Algorytm SHA256 łączy typ wyjątku z nazwami plików i funkcji z Twojej aplikacji. Zmiany numerów linii nie tworzą nowych grup — fingerprint jest stabilny.",
                },
                {
                    "title": "Pełny kontekst błędu",
                    "desc": "Stacktrace, dane requestu (URL, metoda, nagłówki), zmienne środowiskowe — wszystko zapisane w PostgreSQL JSONB.",
                },
                {
                    "title": "Licznik wystąpień",
                    "desc": "Zdenormalizowany event_count na każdym issue. Widzisz od razu, które błędy występują najczęściej — bez kosztownych zapytań COUNT.",
                },
                {
                    "title": "Konwersja do ticketu Scrum",
                    "desc": "Jednym kliknięciem utwórz ticket w backlogu z automatycznie wypełnionym opisem: stacktrace, URL requestu i środowisko.",
                },
                {
                    "title": "SDK, które nie crashuje aplikacji",
                    "desc": "Każda publiczna funkcja SDK jest opakowana w try/except. Wysyłka w tle przez ThreadPoolExecutor — zero wpływu na czas odpowiedzi.",
                },
                {
                    "title": "Status resolved/unresolved",
                    "desc": "Oznaczaj błędy jako rozwiązane. Filtruj listę, aby skupić się na aktywnych problemach.",
                },
            ],
            "steps": [
                {"title": "Zainstaluj SDK", "desc": "pip install monolynx-sdk i dodaj middleware do Django settings.py. Konfiguracja to 3 linijki."},
                {
                    "title": "Aplikacja wysyła błędy automatycznie",
                    "desc": "Middleware przechwytuje każdy nieobsłużony wyjątek i wysyła go w tle do API Monolynx.",
                },
                {
                    "title": "Fingerprint i grupowanie",
                    "desc": "System oblicza fingerprint (SHA256) i przypisuje event do istniejącego issue lub tworzy nowy.",
                },
                {
                    "title": "Analizuj w dashboardzie",
                    "desc": "Lista issues posortowana po czasie ostatniego wystąpienia. Klikasz → widzisz pełny stacktrace i kontekst.",
                },
                {"title": "Napraw i zamknij", "desc": "Oznacz issue jako resolved lub utwórz ticket Scrum do naprawy w sprincie."},
            ],
            "ai_intro": "Twój agent AI może przeglądać błędy, analizować stacktrace i tworzyć z nich tickety — wszystko przez MCP.",
            "mcp_tools": [
                {"name": "list_issues", "desc": "Lista błędów z filtrowaniem po statusie i wyszukiwaniem tekstowym"},
                {"name": "get_issue", "desc": "Pełne szczegóły: stacktrace, dane requestu, historia eventów"},
                {"name": "update_issue_status", "desc": "Oznacz błąd jako rozwiązany lub otwórz ponownie"},
                {"name": "create_ticket_from_issue", "desc": "Utwórz ticket Scrum z automatycznie wypełnionym opisem błędu"},
            ],
            "tech_details": [
                {
                    "label": "Fingerprinting",
                    "value": "SHA256(exception_type + app_frame_filenames:functions). Ignoruje numery linii i ramki z bibliotek (site-packages, stdlib).",
                },
                {"label": "Storage", "value": "PostgreSQL JSONB dla danych wyjątku, requestu i środowiska. UUID primary keys."},
                {"label": "SDK", "value": "Django middleware, zero zależności zewnętrznych. ThreadPoolExecutor(max_workers=2) do wysyłki w tle."},
                {"label": "API", "value": "POST /api/v1/events z nagłówkiem X-Monolynx-Key. Odpowiedź zawiera issue_id do śledzenia."},
            ],
            "other_modules": _other_modules("500ki", lang),
        }
    return {
        "title": "500s — Error Tracking",
        "color": "red",
        "icon": _ICON_500KI,
        "badge": "Error Tracking",
        "screenshot": "500ki-issues-list.png",
        "screenshot_2": None,
        "headline": "Catch errors before your users report them",
        "description": (
            "500s automatically groups exceptions from your applications using smart fingerprinting. "
            "The lightweight Django SDK captures errors in the background — zero performance impact."
        ),
        "screenshot_hint": "Issue list with occurrence counts and status",
        "screenshot_hint_2": "Error details with stacktrace and request context",
        "features": [
            {
                "title": "Smart grouping",
                "desc": "SHA256 algorithm combines exception type with app-frame filenames and functions. Line number changes don't create new groups — the fingerprint is stable.",
            },
            {
                "title": "Full error context",
                "desc": "Stacktrace, request data (URL, method, headers), environment variables — all stored in PostgreSQL JSONB.",
            },
            {
                "title": "Occurrence counter",
                "desc": "Denormalized event_count on each issue. See which errors occur most often — no expensive COUNT queries.",
            },
            {
                "title": "Convert to Scrum ticket",
                "desc": "One click to create a backlog ticket with auto-filled description: stacktrace, request URL, and environment.",
            },
            {
                "title": "SDK that never crashes your app",
                "desc": "Every public SDK function is wrapped in try/except. Background sending via ThreadPoolExecutor — zero impact on response time.",
            },
            {"title": "Resolved/unresolved status", "desc": "Mark errors as resolved. Filter the list to focus on active problems."},
        ],
        "steps": [
            {"title": "Install the SDK", "desc": "pip install monolynx-sdk and add middleware to Django settings.py. Configuration is 3 lines."},
            {
                "title": "Your app sends errors automatically",
                "desc": "Middleware catches every unhandled exception and sends it in the background to the Monolynx API.",
            },
            {
                "title": "Fingerprint and grouping",
                "desc": "System computes a fingerprint (SHA256) and assigns the event to an existing issue or creates a new one.",
            },
            {"title": "Analyze in the dashboard", "desc": "Issue list sorted by last occurrence. Click to see full stacktrace and context."},
            {"title": "Fix and close", "desc": "Mark the issue as resolved or create a Scrum ticket for sprint work."},
        ],
        "ai_intro": "Your AI agent can browse errors, analyze stacktraces, and create tickets from them — all via MCP.",
        "mcp_tools": [
            {"name": "list_issues", "desc": "List errors with status filtering and text search"},
            {"name": "get_issue", "desc": "Full details: stacktrace, request data, event history"},
            {"name": "update_issue_status", "desc": "Mark error as resolved or reopen"},
            {"name": "create_ticket_from_issue", "desc": "Create a Scrum ticket with auto-filled error description"},
        ],
        "tech_details": [
            {
                "label": "Fingerprinting",
                "value": "SHA256(exception_type + app_frame_filenames:functions). Ignores line numbers and library frames (site-packages, stdlib).",
            },
            {"label": "Storage", "value": "PostgreSQL JSONB for exception, request, and environment data. UUID primary keys."},
            {"label": "SDK", "value": "Django middleware, zero external dependencies. ThreadPoolExecutor(max_workers=2) for background sending."},
            {"label": "API", "value": "POST /api/v1/events with X-Monolynx-Key header. Response includes issue_id for tracking."},
        ],
        "other_modules": _other_modules("500ki", lang),
    }


def _feature_scrum(lang: str) -> dict[str, Any]:
    if lang == "pl":
        return {
            "title": "Scrum — Zarządzanie projektami",
            "color": "blue",
            "icon": _ICON_SCRUM,
            "badge": "Project Management",
            "screenshot": "scrum-kanban-board.png",
            "screenshot_2": "scrum-ticket-detail.png",
            "headline": "Backlog, tablica Kanban i sprinty w jednym miejscu",
            "description": (
                "Pełny zestaw narzędzi Scrum: backlog z zaawansowanym filtrowaniem, tablica Kanban z aktualizacją statusu przez HTMX, "
                "sprinty z velocity tracking, komentarze w Markdown i logowanie czasu pracy."
            ),
            "screenshot_hint": "Tablica Kanban z kolumnami: Do zrobienia, W trakcie, Review, Gotowe",
            "screenshot_hint_2": "Backlog z filtrowaniem po statusie, priorytecie i przypisanej osobie",
            "features": [
                {
                    "title": "Tablica Kanban",
                    "desc": "Cztery kolumny statusu: todo → in_progress → in_review → done. Aktualizacja statusu przez HTMX — bez przeładowania strony.",
                },
                {
                    "title": "Backlog z filtrowaniem",
                    "desc": "Filtruj po statusie, priorytecie, przypisanej osobie, sprincie i wyszukuj po tytule. Paginacja po 20 ticketów.",
                },
                {
                    "title": "Sprinty",
                    "desc": "Tworzenie, startowanie i zamykanie sprintów. Przy zamknięciu niezakończone tickety wracają do backlogu. Tylko jeden aktywny sprint naraz.",
                },
                {
                    "title": "Story points i velocity",
                    "desc": "Przypisuj punkty do ticketów. System agreguje je per sprint, żebyś mógł śledzić velocity zespołu.",
                },
                {
                    "title": "Komentarze Markdown",
                    "desc": "Dyskusje pod ticketami z pełnym wsparciem Markdown. Renderowane z Tailwind prose dla czytelnego formatowania.",
                },
                {"title": "Logowanie czasu", "desc": "Dodawaj wpisy czasu pracy z opisem. Workflow statusów: draft → submitted → approved/rejected."},
                {
                    "title": "Automatyczna numeracja",
                    "desc": "Każdy ticket dostaje unikalny numer w projekcie (np. PIM-1, PIM-2). Auto-inkrementacja per projekt.",
                },
                {
                    "title": "Tworzenie z błędów 500ki",
                    "desc": "Utwórz ticket bezpośrednio z błędu — opis wypełnia się automatycznie ze stacktrace i kontekstem requestu.",
                },
            ],
            "steps": [
                {"title": "Utwórz sprint", "desc": "Nadaj nazwę, cel, daty rozpoczęcia i zakończenia."},
                {"title": "Dodaj tickety do backlogu", "desc": "Twórz tickety z tytułem, opisem Markdown, priorytetem i story points."},
                {"title": "Wystartuj sprint", "desc": "Przypisz tickety do sprintu i wystartuj go. Tablica Kanban staje się aktywna."},
                {"title": "Pracuj i przesuwaj karty", "desc": "Zmieniaj statusy na tablicy Kanban. Dodawaj komentarze i loguj czas."},
                {"title": "Zakończ sprint", "desc": "Niedokończone tickety automatycznie wracają do backlogu. Sprawdź velocity w raportach."},
            ],
            "ai_intro": "Agent AI może zarządzać całym cyklem Scrum: tworzyć tickety, przenosić między sprintami, dodawać komentarze i logować czas — 16 narzędzi MCP.",
            "mcp_tools": [
                {"name": "list_tickets", "desc": "Filtrowanie po statusie, priorytecie, sprincie, wyszukiwanie"},
                {"name": "create_ticket", "desc": "Tworzenie z auto-numeracją, opcjonalnym sprintem i przypisaniem"},
                {"name": "update_ticket", "desc": "Zmiana tytułu, opisu, statusu, priorytetu, story points"},
                {"name": "get_board", "desc": "Aktualny stan tablicy Kanban ze wszystkimi kolumnami"},
                {"name": "create_sprint", "desc": "Nowy sprint z nazwą, celem i datami"},
                {"name": "start_sprint / complete_sprint", "desc": "Zarządzanie cyklem życia sprintu"},
                {"name": "add_comment", "desc": "Dodaj komentarz Markdown do ticketu"},
                {"name": "log_time", "desc": "Zaloguj godziny pracy z opisem"},
            ],
            "tech_details": [
                {"label": "Paginacja", "value": "20 ticketów na stronę. Filtry aplikowane przed paginacją, sumy story points obliczane po filtrach."},
                {
                    "label": "HTMX",
                    "value": "Aktualizacja statusu ticketu przez PATCH bez przeładowania strony. Kanban board aktualizuje się w miejscu.",
                },
                {
                    "label": "Numeracja",
                    "value": "Auto-inkrementacja per projekt. Bezpieczna przy równoległych requestach (SELECT MAX + 1 z row-level lock).",
                },
                {
                    "label": "Markdown",
                    "value": "render_markdown_html() z Tailwind prose prose-invert. Używane w opisach ticketów, komentarzach i wiki.",
                },
            ],
            "other_modules": _other_modules("scrum", lang),
        }
    return {
        "title": "Scrum — Project Management",
        "color": "blue",
        "icon": _ICON_SCRUM,
        "badge": "Project Management",
        "screenshot": "scrum-kanban-board.png",
        "screenshot_2": "scrum-ticket-detail.png",
        "headline": "Backlog, Kanban board, and sprints in one place",
        "description": (
            "Full Scrum toolkit: backlog with advanced filtering, Kanban board with HTMX status updates, "
            "sprints with velocity tracking, Markdown comments, and time logging."
        ),
        "screenshot_hint": "Kanban board with columns: Todo, In Progress, Review, Done",
        "screenshot_hint_2": "Backlog with status, priority, and assignee filtering",
        "features": [
            {
                "title": "Kanban board",
                "desc": "Four status columns: todo → in_progress → in_review → done. Status updates via HTMX — no page reload.",
            },
            {
                "title": "Backlog with filtering",
                "desc": "Filter by status, priority, assignee, sprint, and search by title. Pagination at 20 tickets per page.",
            },
            {
                "title": "Sprints",
                "desc": "Create, start, and complete sprints. On completion, unfinished tickets return to backlog. Only one active sprint at a time.",
            },
            {"title": "Story points & velocity", "desc": "Assign points to tickets. System aggregates them per sprint to track team velocity."},
            {
                "title": "Markdown comments",
                "desc": "Discussions on tickets with full Markdown support. Rendered with Tailwind prose for clean formatting.",
            },
            {"title": "Time logging", "desc": "Add time entries with descriptions. Status workflow: draft → submitted → approved/rejected."},
            {"title": "Auto-numbering", "desc": "Each ticket gets a unique number per project (e.g., PIM-1, PIM-2). Auto-increment per project."},
            {
                "title": "Create from 500s errors",
                "desc": "Create a ticket directly from an error — description auto-fills with stacktrace and request context.",
            },
        ],
        "steps": [
            {"title": "Create a sprint", "desc": "Set a name, goal, start and end dates."},
            {"title": "Add tickets to backlog", "desc": "Create tickets with title, Markdown description, priority, and story points."},
            {"title": "Start the sprint", "desc": "Assign tickets to the sprint and start it. The Kanban board becomes active."},
            {"title": "Work and move cards", "desc": "Update statuses on the Kanban board. Add comments and log time."},
            {"title": "Complete the sprint", "desc": "Unfinished tickets automatically return to backlog. Check velocity in reports."},
        ],
        "ai_intro": "Your AI agent can manage the entire Scrum cycle: create tickets, move between sprints, add comments, and log time — 16 MCP tools.",
        "mcp_tools": [
            {"name": "list_tickets", "desc": "Filter by status, priority, sprint, text search"},
            {"name": "create_ticket", "desc": "Create with auto-numbering, optional sprint and assignee"},
            {"name": "update_ticket", "desc": "Change title, description, status, priority, story points"},
            {"name": "get_board", "desc": "Current Kanban board state with all columns"},
            {"name": "create_sprint", "desc": "New sprint with name, goal, and dates"},
            {"name": "start_sprint / complete_sprint", "desc": "Sprint lifecycle management"},
            {"name": "add_comment", "desc": "Add Markdown comment to a ticket"},
            {"name": "log_time", "desc": "Log work hours with description"},
        ],
        "tech_details": [
            {"label": "Pagination", "value": "20 tickets per page. Filters applied before pagination, story point sums computed after filters."},
            {"label": "HTMX", "value": "Ticket status update via PATCH without page reload. Kanban board updates in place."},
            {"label": "Numbering", "value": "Auto-increment per project. Safe under concurrent requests (SELECT MAX + 1 with row-level lock)."},
            {
                "label": "Markdown",
                "value": "render_markdown_html() with Tailwind prose prose-invert. Used in ticket descriptions, comments, and wiki.",
            },
        ],
        "other_modules": _other_modules("scrum", lang),
    }


def _feature_monitoring(lang: str) -> dict[str, Any]:
    if lang == "pl":
        return {
            "title": "Monitoring — Sprawdzanie URL",
            "color": "green",
            "icon": _ICON_MONITORING,
            "badge": "Uptime Monitoring",
            "screenshot": "monitoring-list.png",
            "screenshot_2": "monitoring-detail.png",
            "headline": "Wiedz, że Twoje serwisy działają — zanim klienci zauważą problem",
            "description": (
                "Monitoruj dostępność URL z konfigurowalnymi interwałami. Śledź uptime w perspektywie 24h, 7 dni i 30 dni, "
                "mierz czasy odpowiedzi i przeglądaj pełną historię checków."
            ),
            "screenshot_hint": "Lista monitorów z kolorowymi wskaźnikami statusu i procentem uptime",
            "screenshot_hint_2": "Szczegóły monitora z historią checków i czasami odpowiedzi",
            "features": [
                {
                    "title": "Konfigurowalny interwał",
                    "desc": "Sprawdzaj co minuty, godziny lub dni. Elastyczne jednostki z przechowywaniem surowych wartości w bazie.",
                },
                {"title": "Uptime 24h / 7d / 30d", "desc": "Procent dostępności obliczany z SQL CASE statement. Widzisz trend na pierwszy rzut oka."},
                {
                    "title": "Pomiar czasu odpowiedzi",
                    "desc": "Czas w milisekundach dla każdego checka. Średni czas z ostatnich 24h wyświetlany na stronie szczegółów.",
                },
                {
                    "title": "Historia checków z paginacją",
                    "desc": "25 checków na stronę. Każdy ze statusem (sukces/błąd), czasem odpowiedzi i timestampem.",
                },
                {"title": "Włącz/wyłącz bez usuwania", "desc": "Toggle pozwala wstrzymać monitoring bez utraty historii i konfiguracji."},
                {
                    "title": "Ochrona przed SSRF",
                    "desc": "Blokuje localhost, prywatne IP i adresy link-local. Rozwiązuje DNS i sprawdza IP przed wykonaniem requestu.",
                },
            ],
            "steps": [
                {"title": "Dodaj monitor", "desc": "Podaj URL i interwał sprawdzania (np. co 5 minut). Maksymalnie 20 monitorów na projekt."},
                {
                    "title": "Worker sprawdza periodycznie",
                    "desc": "Oddzielny worker wykonuje requesty HTTP w tle z asyncio.gather() dla równoległych sprawdzeń.",
                },
                {"title": "Wyniki zapisywane w historii", "desc": "Każdy check to rekord w bazie: timestamp, czas odpowiedzi (ms), sukces/porażka."},
                {"title": "Analizuj w dashboardzie", "desc": "Strona szczegółów pokazuje trend uptime, średni czas odpowiedzi i pełną historię."},
            ],
            "ai_intro": "Agent AI widzi status wszystkich monitorów i ich metryki — może szybko zidentyfikować problemy z dostępnością.",
            "mcp_tools": [
                {"name": "list_monitors", "desc": "Wszystkie monitory z ostatnim statusem checka"},
                {"name": "get_monitor", "desc": "Pełna historia z paginacją, metryki uptime, średni czas odpowiedzi"},
            ],
            "tech_details": [
                {
                    "label": "Worker",
                    "value": "Osobny proces (python -m monolynx.worker). Advisory lock w bazie zapewnia, że tylko jeden worker działa jednocześnie.",
                },
                {
                    "label": "Równoległe sprawdzanie",
                    "value": "asyncio.gather() uruchamia wszystkie check'i jednocześnie. ThreadPoolExecutor dla samych requestów HTTP.",
                },
                {
                    "label": "SSRF",
                    "value": "Walidacja: DNS resolution → ipaddress module → blokada prywatnych/loopback/link-local. Zabezpiecza przed atakami na sieć wewnętrzną.",
                },
                {"label": "Limit", "value": "20 monitorów na projekt. Chroni przed nadmiernym obciążeniem workera."},
            ],
            "other_modules": _other_modules("monitoring", lang),
        }
    return {
        "title": "Monitoring — URL Health Checks",
        "color": "green",
        "icon": _ICON_MONITORING,
        "badge": "Uptime Monitoring",
        "screenshot": "monitoring-list.png",
        "screenshot_2": "monitoring-detail.png",
        "headline": "Know your services are running — before customers notice",
        "description": (
            "Monitor URL availability with configurable intervals. Track uptime over 24h, 7 days, and 30 days, "
            "measure response times, and browse full check history."
        ),
        "screenshot_hint": "Monitor list with colored status indicators and uptime percentage",
        "screenshot_hint_2": "Monitor details with check history and response times",
        "features": [
            {"title": "Configurable interval", "desc": "Check every minutes, hours, or days. Flexible units stored as raw values in the database."},
            {"title": "Uptime 24h / 7d / 30d", "desc": "Availability percentage computed with SQL CASE statement. See the trend at a glance."},
            {
                "title": "Response time measurement",
                "desc": "Time in milliseconds for each check. Average time from last 24h displayed on detail page.",
            },
            {"title": "Check history with pagination", "desc": "25 checks per page. Each with status (success/error), response time, and timestamp."},
            {"title": "Toggle without deleting", "desc": "On/off toggle pauses monitoring without losing history and configuration."},
            {
                "title": "SSRF protection",
                "desc": "Blocks localhost, private IPs, and link-local addresses. Resolves DNS and validates IP before making the request.",
            },
        ],
        "steps": [
            {"title": "Add a monitor", "desc": "Enter URL and check interval (e.g., every 5 minutes). Maximum 20 monitors per project."},
            {
                "title": "Worker checks periodically",
                "desc": "Separate worker process makes HTTP requests in the background with asyncio.gather() for concurrent checks.",
            },
            {"title": "Results saved to history", "desc": "Each check is a database record: timestamp, response time (ms), success/failure."},
            {"title": "Analyze in the dashboard", "desc": "Detail page shows uptime trend, average response time, and full history."},
        ],
        "ai_intro": "Your AI agent sees the status of all monitors and their metrics — quickly identifying availability issues.",
        "mcp_tools": [
            {"name": "list_monitors", "desc": "All monitors with last check status"},
            {"name": "get_monitor", "desc": "Full history with pagination, uptime metrics, average response time"},
        ],
        "tech_details": [
            {
                "label": "Worker",
                "value": "Separate process (python -m monolynx.worker). Database advisory lock ensures only one worker runs at a time.",
            },
            {
                "label": "Concurrent checking",
                "value": "asyncio.gather() runs all checks simultaneously. ThreadPoolExecutor for the actual HTTP requests.",
            },
            {
                "label": "SSRF",
                "value": "Validation: DNS resolution → ipaddress module → block private/loopback/link-local. Protects against internal network attacks.",
            },
            {"label": "Limit", "value": "20 monitors per project. Prevents excessive worker load."},
        ],
        "other_modules": _other_modules("monitoring", lang),
    }


def _feature_heartbeat(lang: str) -> dict[str, Any]:
    if lang == "pl":
        return {
            "title": "Heartbeat — Monitoring cron",
            "color": "pink",
            "icon": _ICON_HEARTBEAT,
            "badge": "Cron Monitoring",
            "screenshot": "heartbeat-list.png",
            "screenshot_2": "heartbeat-detail.png",
            "headline": "Twoje crony działają? Heartbeat pilnuje za Ciebie",
            "description": (
                "Dead man's switch dla zadań cron i procesów w tle. Każdy heartbeat ma unikalny URL do pingowania — "
                'jeśli ping nie przyjdzie w oczekiwanym czasie, status zmienia się na "down".'
            ),
            "screenshot_hint": "Lista heartbeatów z kolorowymi kropkami statusu (zielony/czerwony/szary)",
            "screenshot_hint_2": "Szczegóły heartbeatu z URL do pingowania i instrukcją integracji cURL + cron",
            "features": [
                {
                    "title": "Dead man's switch",
                    "desc": "Heartbeat oczekuje pingu w zadanym interwale. Brak pingu = alarm. Prosta i niezawodna zasada.",
                },
                {
                    "title": "Unikalne URL-e pingowania",
                    "desc": "Każdy heartbeat generuje token (hb_*). Twój cron job pinguje GET lub POST na /hb/{token}.",
                },
                {"title": "Period + grace", "desc": "Oczekiwany interwał (period) + dodatkowa tolerancja (grace) na opóźnienia sieciowe i zegar."},
                {
                    "title": "Trzy statusy",
                    "desc": "pending (brak pingów), up (ping w terminie), down (przekroczony deadline). Status obliczany on-demand.",
                },
                {"title": "Limit 50 per projekt", "desc": "Wystarczająco dla nawet rozbudowanych systemów z wieloma zadaniami."},
                {"title": "Edycja bez utraty historii", "desc": "Zmień nazwę, period lub grace bez resetowania ostatniego pingu i statusu."},
            ],
            "steps": [
                {"title": "Utwórz heartbeat", "desc": 'Podaj nazwę (np. "Backup nocny"), oczekiwany interwał (np. 60 min) i grace (np. 5 min).'},
                {
                    "title": "Skopiuj URL pingowania",
                    "desc": "System generuje unikalny URL: /hb/hb_abc123xyz. Pokaże Ci przykład konfiguracji cURL i crontab.",
                },
                {"title": "Dodaj ping do zadania cron", "desc": "Na końcu skryptu dodaj: curl -s https://your-instance/hb/hb_abc123xyz"},
                {
                    "title": "Monitoruj status",
                    "desc": 'Dashboard pokazuje status: zielony (up), czerwony (down), szary (pending). Badge w sidebarze liczy heartbeaty "down".',
                },
            ],
            "ai_intro": "Agent AI może sprawdzać status heartbeatów, tworzyć nowe i modyfikować konfigurację — 5 narzędzi MCP.",
            "mcp_tools": [
                {"name": "list_heartbeats", "desc": "Wszystkie heartbeaty z obliczonym statusem"},
                {"name": "get_heartbeat", "desc": "Szczegóły z URL pingowania, period/grace, status"},
                {"name": "create_heartbeat", "desc": "Nowy heartbeat z nazwą, period i grace (w minutach)"},
                {"name": "update_heartbeat", "desc": "Zmień nazwę, period lub grace"},
                {"name": "delete_heartbeat", "desc": "Usuń heartbeat"},
            ],
            "tech_details": [
                {"label": "Token", "value": "Format hb_ + 16 znaków URL-safe base64. Unikalne w ramach bazy danych."},
                {"label": "Status", "value": "Obliczany on-demand: elapsed_time = now - last_ping_at. Jeśli elapsed > period + grace → down."},
                {"label": "Ping endpoint", "value": "GET/POST /hb/{token}. Publiczny endpoint — nie wymaga autoryzacji. Aktualizuje last_ping_at."},
                {"label": "Przechowywanie", "value": "period i grace przechowywane w sekundach w bazie, konwertowane do/z minut w UI."},
            ],
            "other_modules": _other_modules("heartbeat", lang),
        }
    return {
        "title": "Heartbeat — Cron Monitoring",
        "color": "pink",
        "icon": _ICON_HEARTBEAT,
        "badge": "Cron Monitoring",
        "screenshot": "heartbeat-list.png",
        "screenshot_2": "heartbeat-detail.png",
        "headline": "Are your cron jobs running? Heartbeat watches for you",
        "description": (
            "Dead man's switch for cron jobs and background processes. Each heartbeat has a unique ping URL — "
            'if the ping doesn\'t arrive within the expected time, status changes to "down".'
        ),
        "screenshot_hint": "Heartbeat list with colored status dots (green/red/gray)",
        "screenshot_hint_2": "Heartbeat details with ping URL and cURL + cron integration instructions",
        "features": [
            {"title": "Dead man's switch", "desc": "Heartbeat expects a ping at a set interval. No ping = alarm. Simple and reliable principle."},
            {"title": "Unique ping URLs", "desc": "Each heartbeat generates a token (hb_*). Your cron job pings GET or POST to /hb/{token}."},
            {"title": "Period + grace", "desc": "Expected interval (period) + additional tolerance (grace) for network delays and clock drift."},
            {"title": "Three statuses", "desc": "pending (no pings), up (pinged on time), down (deadline exceeded). Status computed on-demand."},
            {"title": "50 per project limit", "desc": "Enough even for complex systems with many scheduled tasks."},
            {"title": "Edit without losing history", "desc": "Change name, period, or grace without resetting last ping and status."},
        ],
        "steps": [
            {
                "title": "Create a heartbeat",
                "desc": 'Set a name (e.g., "Nightly backup"), expected interval (e.g., 60 min), and grace (e.g., 5 min).',
            },
            {"title": "Copy the ping URL", "desc": "System generates a unique URL: /hb/hb_abc123xyz. Shows example cURL and crontab configuration."},
            {"title": "Add ping to your cron job", "desc": "At the end of your script add: curl -s https://your-instance/hb/hb_abc123xyz"},
            {
                "title": "Monitor status",
                "desc": 'Dashboard shows status: green (up), red (down), gray (pending). Sidebar badge counts "down" heartbeats.',
            },
        ],
        "ai_intro": "Your AI agent can check heartbeat statuses, create new ones, and modify configuration — 5 MCP tools.",
        "mcp_tools": [
            {"name": "list_heartbeats", "desc": "All heartbeats with computed status"},
            {"name": "get_heartbeat", "desc": "Details with ping URL, period/grace, status"},
            {"name": "create_heartbeat", "desc": "New heartbeat with name, period, and grace (in minutes)"},
            {"name": "update_heartbeat", "desc": "Change name, period, or grace"},
            {"name": "delete_heartbeat", "desc": "Remove heartbeat"},
        ],
        "tech_details": [
            {"label": "Token", "value": "Format hb_ + 16 chars URL-safe base64. Unique across the database."},
            {"label": "Status", "value": "Computed on-demand: elapsed_time = now - last_ping_at. If elapsed > period + grace → down."},
            {"label": "Ping endpoint", "value": "GET/POST /hb/{token}. Public endpoint — no auth required. Updates last_ping_at."},
            {"label": "Storage", "value": "period and grace stored in seconds in DB, converted to/from minutes in UI."},
        ],
        "other_modules": _other_modules("heartbeat", lang),
    }


def _feature_wiki(lang: str) -> dict[str, Any]:
    if lang == "pl":
        return {
            "title": "Wiki — Baza wiedzy",
            "color": "amber",
            "icon": _ICON_WIKI,
            "screenshot": "wiki-tree.png",
            "screenshot_2": "wiki-search.png",
            "badge": "Knowledge Base",
            "headline": "Dokumentacja, którą Twój agent AI potrafi przeszukać semantycznie",
            "description": (
                "Hierarchiczne strony Markdown z edytorem WYSIWYG, uploadem obrazów do MinIO i wyszukiwaniem semantycznym "
                "przez pgvector + OpenAI embeddings. To wiki zaprojektowane dla ludzi i agentów AI jednocześnie."
            ),
            "screenshot_hint": "Drzewo stron wiki z zagnieżdżoną hierarchią",
            "screenshot_hint_2": "Edytor EasyMDE z podglądem Markdown i uploadem obrazów",
            "features": [
                {
                    "title": "Hierarchia stron",
                    "desc": "Drzewo stron z relacją parent/child. Breadcrumby do nawigacji. Podstrony wyświetlane na stronie rodzica.",
                },
                {
                    "title": "Edytor WYSIWYG (EasyMDE)",
                    "desc": "Markdown z podglądem na żywo, formatowaniem i wstawianiem obrazów przez drag & drop. Dark theme dopasowany do UI.",
                },
                {
                    "title": "Upload obrazów do MinIO",
                    "desc": "Obrazy przechowywane w object storage (MinIO). Ścieżka: {slug}/attachments/{filename}. Serwowane bezpośrednio.",
                },
                {
                    "title": "Wyszukiwanie semantyczne (RAG)",
                    "desc": "pgvector z HNSW index + OpenAI text-embedding-3-small. Pytasz naturalnym językiem — system znajduje najlepiej pasujące fragmenty.",
                },
                {
                    "title": "Chunking z overlapping",
                    "desc": "Strony dzielone na fragmenty ~500 tokenów z nakładaniem się. Tokenizacja przez tiktoken. Każdy chunk ma osobny embedding.",
                },
                {"title": "Śledzenie autorów", "desc": "Kto utworzył stronę, kto ostatnio edytował. Pełna przejrzystość zmian."},
                {
                    "title": "Graceful degradation",
                    "desc": "Brak klucza OpenAI? Wiki działa normalnie — wyszukiwanie semantyczne jest po prostu wyłączone.",
                },
                {
                    "title": "Markdown rendering",
                    "desc": "render_markdown_html() z Tailwind prose prose-invert. Ten sam renderer używany w ticketach i komentarzach Scrum.",
                },
            ],
            "steps": [
                {"title": "Utwórz stronę główną", "desc": 'Np. "Architektura systemu". Użyj edytora EasyMDE do pisania w Markdown.'},
                {"title": "Dodaj podstrony", "desc": 'Twórz strony-dzieci, np. "Baza danych" pod "Architekturą". Drzewo buduje się automatycznie.'},
                {"title": "Wstaw obrazy", "desc": "Przeciągnij obrazy do edytora — automatycznie uploadują się do MinIO i wstawiają jako Markdown."},
                {
                    "title": "Wyszukuj semantycznie",
                    "desc": "Wpisz pytanie w polu szukaj — system znajdzie najlepiej pasujące fragmenty stron na podstawie znaczenia, nie tylko słów kluczowych.",
                },
                {
                    "title": "AI przeszukuje wiki przez MCP",
                    "desc": "Agent AI może przeczytać dowolną stronę i wyszukać informacje semantycznie — tak jakby był członkiem zespołu.",
                },
            ],
            "ai_intro": "Wiki to serce AI-first podejścia Monolynx. Agent AI przeszukuje Twoją dokumentację semantycznie i czyta strony jak człowiek — 6 narzędzi MCP.",
            "mcp_tools": [
                {"name": "list_wiki_pages", "desc": "Pełne drzewo hierarchii z parent_id i głębokością"},
                {"name": "get_wiki_page", "desc": "Treść strony + breadcrumby do nawigacji"},
                {"name": "create_wiki_page", "desc": "Nowa strona z opcjonalnym parent_id (podstrona)"},
                {"name": "update_wiki_page", "desc": "Edycja treści, tytułu, pozycji"},
                {"name": "delete_wiki_page", "desc": "Usuwanie kaskadowe (strona + podstrony)"},
                {"name": "search_wiki", "desc": "Wyszukiwanie semantyczne RAG (jeśli embeddingi aktywne)"},
            ],
            "tech_details": [
                {
                    "label": "Embeddings",
                    "value": "OpenAI text-embedding-3-small (1536 wymiarów). Chunking przez tiktoken (model gpt-4o), ~500 tokenów z overlap.",
                },
                {"label": "Wyszukiwanie", "value": "pgvector z HNSW index, cosine similarity. Wyniki posortowane po trafności z podglądem snippetu."},
                {
                    "label": "Content storage",
                    "value": "Markdown w MinIO ({slug}/pages/{page_id}.md). Metadata (tytuł, hierarchia, pozycja) w PostgreSQL.",
                },
                {"label": "Graceful degradation", "value": "OPENAI_API_KEY='' wyłącza embeddingi. Wiki działa bez wyszukiwania semantycznego."},
            ],
            "other_modules": _other_modules("wiki", lang),
        }
    return {
        "title": "Wiki — Knowledge Base",
        "color": "amber",
        "icon": _ICON_WIKI,
        "screenshot": "wiki-tree.png",
        "screenshot_2": "wiki-search.png",
        "badge": "Knowledge Base",
        "headline": "Documentation your AI agent can search semantically",
        "description": (
            "Hierarchical Markdown pages with a WYSIWYG editor, image uploads to MinIO, and semantic search "
            "via pgvector + OpenAI embeddings. A wiki designed for humans and AI agents alike."
        ),
        "screenshot_hint": "Wiki page tree with nested hierarchy",
        "screenshot_hint_2": "EasyMDE editor with Markdown preview and image uploads",
        "features": [
            {
                "title": "Page hierarchy",
                "desc": "Page tree with parent/child relationships. Breadcrumbs for navigation. Child pages displayed on parent page.",
            },
            {
                "title": "WYSIWYG editor (EasyMDE)",
                "desc": "Markdown with live preview, formatting, and drag & drop image insertion. Dark theme matching the UI.",
            },
            {
                "title": "Image uploads to MinIO",
                "desc": "Images stored in object storage (MinIO). Path: {slug}/attachments/{filename}. Served directly.",
            },
            {
                "title": "Semantic search (RAG)",
                "desc": "pgvector with HNSW index + OpenAI text-embedding-3-small. Ask in natural language — system finds best matching fragments.",
            },
            {
                "title": "Chunking with overlap",
                "desc": "Pages split into ~500 token chunks with overlap. Tokenization via tiktoken. Each chunk has its own embedding.",
            },
            {"title": "Author tracking", "desc": "Who created the page, who last edited. Full change transparency."},
            {"title": "Graceful degradation", "desc": "No OpenAI key? Wiki works normally — semantic search is simply disabled."},
            {
                "title": "Markdown rendering",
                "desc": "render_markdown_html() with Tailwind prose prose-invert. Same renderer used in Scrum tickets and comments.",
            },
        ],
        "steps": [
            {"title": "Create a root page", "desc": 'E.g., "System Architecture". Use the EasyMDE editor to write in Markdown.'},
            {"title": "Add child pages", "desc": 'Create children, e.g., "Database Design" under "Architecture". Tree builds automatically.'},
            {"title": "Insert images", "desc": "Drag images into the editor — they auto-upload to MinIO and insert as Markdown."},
            {
                "title": "Search semantically",
                "desc": "Type a question in the search field — system finds best matching page fragments based on meaning, not just keywords.",
            },
            {"title": "AI searches wiki via MCP", "desc": "Your AI agent can read any page and search semantically — as if it were a team member."},
        ],
        "ai_intro": "Wiki is the heart of Monolynx's AI-first approach. Your AI agent searches documentation semantically and reads pages like a human — 6 MCP tools.",
        "mcp_tools": [
            {"name": "list_wiki_pages", "desc": "Full hierarchy tree with parent_id and depth"},
            {"name": "get_wiki_page", "desc": "Page content + breadcrumbs for navigation"},
            {"name": "create_wiki_page", "desc": "New page with optional parent_id (child page)"},
            {"name": "update_wiki_page", "desc": "Edit content, title, position"},
            {"name": "delete_wiki_page", "desc": "Cascading delete (page + children)"},
            {"name": "search_wiki", "desc": "Semantic RAG search (if embeddings are active)"},
        ],
        "tech_details": [
            {
                "label": "Embeddings",
                "value": "OpenAI text-embedding-3-small (1536 dimensions). Chunking via tiktoken (gpt-4o model), ~500 tokens with overlap.",
            },
            {"label": "Search", "value": "pgvector with HNSW index, cosine similarity. Results sorted by relevance with snippet preview."},
            {
                "label": "Content storage",
                "value": "Markdown in MinIO ({slug}/pages/{page_id}.md). Metadata (title, hierarchy, position) in PostgreSQL.",
            },
            {"label": "Graceful degradation", "value": "OPENAI_API_KEY='' disables embeddings. Wiki works without semantic search."},
        ],
        "other_modules": _other_modules("wiki", lang),
    }


def _feature_connections(lang: str) -> dict[str, Any]:
    if lang == "pl":
        return {
            "title": "Połączenia — Graf zależności",
            "screenshot": "connections-graph.png",
            "screenshot_2": None,
            "color": "purple",
            "icon": _ICON_CONNECTIONS,
            "badge": "Dependency Graph",
            "headline": "Wizualizuj, jak elementy Twojego kodu łączą się ze sobą",
            "description": (
                "Interaktywny graf zależności oparty na Neo4j z wizualizacją Cytoscape.js. "
                "Modeluj strukturę kodu: pliki, klasy, metody, funkcje i ich relacje. Agent AI importuje graf automatycznie."
            ),
            "screenshot_hint": "Interaktywny graf Cytoscape.js z kolorowymi node'ami i krawędziami",
            "screenshot_hint_2": "Panel boczny ze szczegółami node'a i listą połączeń",
            "features": [
                {"title": "6 typów node'ów", "desc": "File, Class, Method, Function, Const, Module. Każdy z własnym kolorem na grafie."},
                {"title": "6 typów krawędzi", "desc": "CONTAINS, CALLS, IMPORTS, INHERITS, USES, IMPLEMENTS. Pełne modelowanie zależności kodu."},
                {
                    "title": "Wizualizacja Cytoscape.js",
                    "desc": "Interaktywny graf z force-directed layout (cose). Zoom, pan, klikanie node'ów, filtrowanie po typie.",
                },
                {
                    "title": "Bulk import",
                    "desc": "Importuj setki node'ów i krawędzi jednym requestem. Idealne do automatycznej synchronizacji z kodem.",
                },
                {"title": "Wyszukiwanie ścieżki", "desc": "Znajdź najkrótszą ścieżkę między dwoma elementami. Cypher query w Neo4j."},
                {"title": "Graceful degradation", "desc": "Neo4j niedostępny? Dashboard działa — pokazuje komunikat o niedostępności."},
            ],
            "steps": [
                {"title": "Twórz node'y", "desc": "Dodaj elementy kodu: pliki, klasy, metody. Opcjonalnie podaj ścieżkę do pliku i numer linii."},
                {"title": "Łącz krawędziami", "desc": "Zdefiniuj relacje: klasa CONTAINS metodę, metoda CALLS inną metodę, plik IMPORTS moduł."},
                {
                    "title": "Eksploruj graf wizualnie",
                    "desc": "Otwórz widok grafu. Klikaj node'y, aby zobaczyć szczegóły i połączenia w panelu bocznym.",
                },
                {
                    "title": "Automatyzuj z AI",
                    "desc": "Agent AI może zaimportować graf zależności Twojego kodu — bulk create nodes i edges przez MCP.",
                },
            ],
            "ai_intro": "Agent AI może zbudować graf zależności Twojego kodu automatycznie i odpytywać go — 9 narzędzi MCP + bulk operacje.",
            "mcp_tools": [
                {"name": "create_graph_node", "desc": "Utwórz node z typem, nazwą i opcjonalnymi metadanymi"},
                {"name": "bulk_create_graph_nodes", "desc": "Importuj wiele node'ów jednym requestem"},
                {"name": "create_graph_edge", "desc": "Połącz dwa node'y krawędzią danego typu"},
                {"name": "bulk_create_graph_edges", "desc": "Importuj wiele krawędzi jednym requestem"},
                {"name": "query_graph", "desc": "Pobierz cały graf lub podgraf filtrowany po typie"},
                {"name": "find_graph_path", "desc": "Najkrótsza ścieżka między dwoma node'ami"},
                {"name": "get_graph_stats", "desc": "Statystyki: liczba node'ów i krawędzi per typ"},
                {"name": "delete_graph_node", "desc": "Usuń node i kaskadowo jego krawędzie"},
                {"name": "delete_graph_edge", "desc": "Usuń krawędź między node'ami"},
            ],
            "tech_details": [
                {
                    "label": "Baza grafowa",
                    "value": "Neo4j 5 Community. Async driver z izolacją danych per projekt (property project_id na node'ach).",
                },
                {"label": "Wizualizacja", "value": "Cytoscape.js v3.30.4 (CDN). Force-directed layout (cose). Niestandardowe kolorowanie po typie."},
                {
                    "label": "Izolacja danych",
                    "value": "Każdy node i krawędź tagowane project_id. Constraint unikalności per typ. HNSW index na project_id.",
                },
                {"label": "Graceful degradation", "value": "ENABLE_GRAPH_DB=false wyłącza moduł. is_enabled() + try/except zwraca pusty graf."},
            ],
            "other_modules": _other_modules("connections", lang),
        }
    return {
        "title": "Connections — Dependency Graph",
        "screenshot": "connections-graph.png",
        "screenshot_2": None,
        "color": "purple",
        "icon": _ICON_CONNECTIONS,
        "badge": "Dependency Graph",
        "headline": "Visualize how your code elements connect to each other",
        "description": (
            "Interactive dependency graph powered by Neo4j with Cytoscape.js visualization. "
            "Model your code structure: files, classes, methods, functions, and their relationships. AI agent imports the graph automatically."
        ),
        "screenshot_hint": "Interactive Cytoscape.js graph with colored nodes and edges",
        "screenshot_hint_2": "Side panel with node details and connections list",
        "features": [
            {"title": "6 node types", "desc": "File, Class, Method, Function, Const, Module. Each with its own color on the graph."},
            {"title": "6 edge types", "desc": "CONTAINS, CALLS, IMPORTS, INHERITS, USES, IMPLEMENTS. Full code dependency modeling."},
            {
                "title": "Cytoscape.js visualization",
                "desc": "Interactive graph with force-directed layout (cose). Zoom, pan, click nodes, filter by type.",
            },
            {"title": "Bulk import", "desc": "Import hundreds of nodes and edges in one request. Perfect for automatic code sync."},
            {"title": "Path finding", "desc": "Find shortest path between two elements. Cypher query in Neo4j."},
            {"title": "Graceful degradation", "desc": "Neo4j unavailable? Dashboard works — shows unavailability message."},
        ],
        "steps": [
            {"title": "Create nodes", "desc": "Add code elements: files, classes, methods. Optionally specify file path and line number."},
            {"title": "Connect with edges", "desc": "Define relationships: class CONTAINS method, method CALLS another method, file IMPORTS module."},
            {"title": "Explore the graph visually", "desc": "Open graph view. Click nodes to see details and connections in the side panel."},
            {"title": "Automate with AI", "desc": "AI agent can import your code's dependency graph — bulk create nodes and edges via MCP."},
        ],
        "ai_intro": "Your AI agent can build your code's dependency graph automatically and query it — 9 MCP tools + bulk operations.",
        "mcp_tools": [
            {"name": "create_graph_node", "desc": "Create node with type, name, and optional metadata"},
            {"name": "bulk_create_graph_nodes", "desc": "Import multiple nodes in one request"},
            {"name": "create_graph_edge", "desc": "Connect two nodes with an edge of given type"},
            {"name": "bulk_create_graph_edges", "desc": "Import multiple edges in one request"},
            {"name": "query_graph", "desc": "Get full graph or type-filtered subgraph"},
            {"name": "find_graph_path", "desc": "Shortest path between two nodes"},
            {"name": "get_graph_stats", "desc": "Stats: node and edge counts per type"},
            {"name": "delete_graph_node", "desc": "Delete node and cascade its edges"},
            {"name": "delete_graph_edge", "desc": "Delete edge between nodes"},
        ],
        "tech_details": [
            {"label": "Graph database", "value": "Neo4j 5 Community. Async driver with per-project data isolation (project_id property on nodes)."},
            {"label": "Visualization", "value": "Cytoscape.js v3.30.4 (CDN). Force-directed layout (cose). Custom coloring by type."},
            {
                "label": "Data isolation",
                "value": "Every node and edge tagged with project_id. Uniqueness constraint per type. HNSW index on project_id.",
            },
            {"label": "Graceful degradation", "value": "ENABLE_GRAPH_DB=false disables the module. is_enabled() + try/except returns empty graph."},
        ],
        "other_modules": _other_modules("connections", lang),
    }


def _feature_reports(lang: str) -> dict[str, Any]:
    if lang == "pl":
        return {
            "title": "Raporty — Analityka pracy",
            "screenshot": None,
            "screenshot_2": None,
            "color": "cyan",
            "icon": _ICON_REPORTS,
            "badge": "Work Analytics",
            "headline": "Raportuj godziny pracy z wielu projektów jednocześnie",
            "description": (
                "Globalne raporty pracy z filtrowaniem po projektach, użytkownikach, sprintach i zakresie dat. "
                "Eksport do PDF dla stakeholderów. Oddzielne śledzenie wpisów AI vs. ręcznych."
            ),
            "screenshot_hint": "Dashboard raportów z multi-select filtrami i tabelą wpisów czasu",
            "screenshot_hint_2": None,
            "features": [
                {
                    "title": "Cross-project agregacja",
                    "desc": "Jeden widok raportów ze wszystkich projektów. Superuser widzi wszystko, członek projektu widzi tylko swoje projekty.",
                },
                {
                    "title": "Multi-select filtry",
                    "desc": "Wybierz wiele projektów, użytkowników i sprintów jednocześnie. Zakres dat z domyślnymi ostatnimi 30 dniami.",
                },
                {"title": "Statystyki", "desc": "Łączne godziny, średnia na wpis, liczba unikalnych użytkowników, podział wpisów AI vs. ręczne."},
                {"title": "Eksport PDF", "desc": "Sformatowany raport generowany server-side przez weasyprint. Gotowy do wysłania stakeholderom."},
                {"title": "Tabela wpisów", "desc": "Użytkownik, klucz ticketu (PIM-1), sprint, data, godziny, opis. Paginacja po 20 wpisów."},
                {"title": "Wykrywanie wpisów AI", "desc": "Flaga created_via_ai na każdym wpisie. Oddzielne liczniki dla AI i ręcznych wpisów."},
            ],
            "steps": [
                {"title": "Otwórz Raporty", "desc": "Dashboard raportów jest globalny — nie wymaga wybrania projektu. Widoczny z głównej nawigacji."},
                {"title": "Ustaw filtry", "desc": "Wybierz projekty, użytkowników i sprinty. Domyślnie: wszystkie projekty, ostatnie 30 dni."},
                {"title": "Przejrzyj statystyki", "desc": "Na górze widoczne podsumowanie: łączne godziny, średnia, użytkownicy, podział AI/ręczne."},
                {"title": "Eksportuj do PDF", "desc": "Kliknij przycisk eksportu — raport generuje się server-side i pobiera jako PDF."},
            ],
            "ai_intro": "Raporty agregują dane logowane przez narzędzie log_time w module Scrum. Agent AI loguje czas pracy przez MCP — wpisy oznaczane automatycznie jako AI.",
            "mcp_tools": [
                {"name": "log_time", "desc": "Zaloguj czas pracy do ticketu (w module Scrum). Wpis oznaczony jako AI-created."},
                {"name": "list_tickets", "desc": "Przejrzyj tickety z zalogowanym czasem"},
                {"name": "get_ticket", "desc": "Szczegóły ticketu z listą wpisów czasu"},
            ],
            "tech_details": [
                {
                    "label": "Agregacja",
                    "value": "Serwisowa warstwa agreguje godziny per projekt, sprint i użytkownik. Filtry walidowane przeciwko uprawnieniom.",
                },
                {"label": "PDF", "value": "weasyprint generuje PDF server-side. CSS styling w szablonie HTML."},
                {"label": "AI detection", "value": "Flaga created_via_ai na modelu TimeTrackingEntry. Wpisy z MCP automatycznie oznaczane."},
                {"label": "Uprawnienia", "value": "Superuser widzi wszystkie projekty. Członek widzi tylko projekty, do których należy."},
            ],
            "other_modules": _other_modules("reports", lang),
        }
    return {
        "title": "Reports — Work Analytics",
        "screenshot": None,
        "screenshot_2": None,
        "color": "cyan",
        "icon": _ICON_REPORTS,
        "badge": "Work Analytics",
        "headline": "Report work hours across multiple projects at once",
        "description": (
            "Global work reports with filtering by projects, users, sprints, and date ranges. "
            "PDF export for stakeholders. Separate tracking of AI vs. manual entries."
        ),
        "screenshot_hint": "Reports dashboard with multi-select filters and time entries table",
        "screenshot_hint_2": None,
        "features": [
            {
                "title": "Cross-project aggregation",
                "desc": "Single view of reports across all projects. Superuser sees everything, project member sees only their projects.",
            },
            {"title": "Multi-select filters", "desc": "Select multiple projects, users, and sprints at once. Date range defaults to last 30 days."},
            {"title": "Statistics", "desc": "Total hours, average per entry, unique user count, AI vs. manual entry breakdown."},
            {"title": "PDF export", "desc": "Formatted report generated server-side with weasyprint. Ready to send to stakeholders."},
            {"title": "Entry table", "desc": "User, ticket key (PIM-1), sprint, date, hours, description. Pagination at 20 entries."},
            {"title": "AI entry detection", "desc": "created_via_ai flag on each entry. Separate counters for AI and manual entries."},
        ],
        "steps": [
            {"title": "Open Reports", "desc": "Reports dashboard is global — no project selection needed. Accessible from main navigation."},
            {"title": "Set filters", "desc": "Choose projects, users, and sprints. Default: all projects, last 30 days."},
            {"title": "Review statistics", "desc": "Summary at the top: total hours, average, users, AI/manual breakdown."},
            {"title": "Export to PDF", "desc": "Click the export button — report generates server-side and downloads as PDF."},
        ],
        "ai_intro": "Reports aggregate data logged via the log_time tool in the Scrum module. AI agent logs work time via MCP — entries automatically marked as AI-created.",
        "mcp_tools": [
            {"name": "log_time", "desc": "Log work time to a ticket (in the Scrum module). Entry marked as AI-created."},
            {"name": "list_tickets", "desc": "Browse tickets with logged time"},
            {"name": "get_ticket", "desc": "Ticket details with time entries list"},
        ],
        "tech_details": [
            {"label": "Aggregation", "value": "Service layer aggregates hours per project, sprint, and user. Filters validated against permissions."},
            {"label": "PDF", "value": "weasyprint generates PDF server-side. CSS styling in HTML template."},
            {"label": "AI detection", "value": "created_via_ai flag on TimeTrackingEntry model. MCP entries automatically flagged."},
            {"label": "Permissions", "value": "Superuser sees all projects. Member sees only projects they belong to."},
        ],
        "other_modules": _other_modules("reports", lang),
    }


_FEATURES: dict[str, Any] = {
    "500ki": _feature_500ki,
    "scrum": _feature_scrum,
    "monitoring": _feature_monitoring,
    "heartbeat": _feature_heartbeat,
    "wiki": _feature_wiki,
    "connections": _feature_connections,
    "reports": _feature_reports,
}
