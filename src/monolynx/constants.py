"""Stale uzywane w modulach (statusy, priorytety, etykiety)."""

# 500ki / Issues
ISSUE_STATUSES = ("unresolved", "resolved", "ignored")

ISSUE_STATUS_LABELS = {
    "unresolved": "Nierozwiązane",
    "resolved": "Rozwiązane",
    "ignored": "Zignorowane",
}

ISSUE_SORT_FIELDS = ("last_seen", "event_count")

ISSUE_SORT_FIELD_LABELS = {
    "last_seen": "Ostatnie wystąpienie",
    "event_count": "Liczba wystąpień",
}

ISSUE_SORT_ORDERS = ("asc", "desc")

TICKET_STATUSES = ("backlog", "todo", "in_progress", "in_review", "done")

BOARD_STATUSES = ("todo", "in_progress", "in_review", "done")

PRIORITIES = ("low", "medium", "high", "critical")

SPRINT_STATUSES = ("planning", "active", "completed")

MEMBER_ROLES = ("owner", "admin", "member")

STATUS_LABELS = {
    "backlog": "Backlog",
    "todo": "Do zrobienia",
    "in_progress": "W trakcie",
    "in_review": "Review",
    "done": "Gotowe",
}

PRIORITY_LABELS = {
    "low": "Niski",
    "medium": "Sredni",
    "high": "Wysoki",
    "critical": "Krytyczny",
}

PRIORITY_COLORS = {
    "low": "gray",
    "medium": "blue",
    "high": "orange",
    "critical": "red",
}

SPRINT_STATUS_LABELS = {
    "planning": "Planowanie",
    "active": "Aktywny",
    "completed": "Zakonczony",
}

ROLE_LABELS = {
    "owner": "Wlasciciel",
    "admin": "Administrator",
    "member": "Czlonek",
}

INTERVAL_UNITS = ("minutes", "hours", "days")

INTERVAL_UNIT_LABELS = {
    "minutes": "min.",
    "hours": "godz.",
    "days": "dni",
}

# Time tracking
TIME_TRACKING_STATUSES = ("draft", "submitted", "approved", "rejected")

TIME_TRACKING_STATUS_LABELS = {
    "draft": "Projekt",
    "submitted": "Wysłany",
    "approved": "Zatwierdzony",
    "rejected": "Odrzucony",
}

DEFAULT_REPORT_DATE_RANGE_DAYS = 30
DEFAULT_REPORT_PAGE_SIZE = 20

# Graph (modul polaczen — baza grafowa)
GRAPH_NODE_TYPES = ("File", "Class", "Method", "Function", "Const", "Module")

GRAPH_EDGE_TYPES = ("CONTAINS", "CALLS", "IMPORTS", "INHERITS", "USES", "IMPLEMENTS")

GRAPH_NODE_LABELS = {
    "File": "Plik",
    "Class": "Klasa",
    "Method": "Metoda",
    "Function": "Funkcja",
    "Const": "Stała",
    "Module": "Moduł",
}

GRAPH_EDGE_LABELS = {
    "CONTAINS": "Zawiera",
    "CALLS": "Wywołuje",
    "IMPORTS": "Importuje",
    "INHERITS": "Dziedziczy",
    "USES": "Używa",
    "IMPLEMENTS": "Implementuje",
}

# Labels
LABEL_COLOR_PALETTE = [
    "#e74c3c",
    "#e67e22",
    "#f1c40f",
    "#2ecc71",
    "#1abc9c",
    "#3498db",
    "#9b59b6",
    "#e91e63",
    "#00bcd4",
    "#8bc34a",
]

# Activity log
ACTIVITY_ENTITY_TYPES = {"ticket", "sprint", "monitor", "wiki", "member"}

# Invitations
INVITATION_DAYS = 7

# File type icons -- mapping rozszerzen do kategorii ikon
FILE_TYPE_CATEGORIES = {
    "pdf": "pdf",
    "xls": "excel",
    "xlsx": "excel",
    "xlsm": "excel",
    "csv": "excel",
    "doc": "word",
    "docx": "word",
    "ppt": "powerpoint",
    "pptx": "powerpoint",
    "zip": "archive",
    "rar": "archive",
    "7z": "archive",
    "tar": "archive",
    "gz": "archive",
    "py": "code",
    "js": "code",
    "ts": "code",
    "json": "code",
    "xml": "code",
    "html": "code",
    "css": "code",
    "sql": "code",
    "txt": "text",
    "md": "text",
    "log": "text",
    "rst": "text",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "gif": "image",
    "webp": "image",
    "svg": "image",
}

FILE_TYPE_LABELS_PL = {
    "pdf": "PDF",
    "excel": "Excel",
    "word": "Word",
    "powerpoint": "PowerPoint",
    "archive": "Archiwum",
    "code": "Kod źródłowy",
    "text": "Tekst",
    "image": "Obraz",
    "default": "Plik",
}

# --- Permissions (RBAC) ---
PERMISSION_MODULES = (
    "500ki",
    "scrum",
    "monitoring",
    "heartbeat",
    "wiki",
    "connections",
    "settings",
    "reports",
    "users",
    "rozliczenia",
)

PERMISSION_ACTIONS = ("read", "write", "delete")

MODULE_LABELS = {
    "500ki": "500ki (Błędy)",
    "scrum": "Scrum",
    "monitoring": "Monitoring",
    "heartbeat": "Heartbeat",
    "wiki": "Wiki",
    "connections": "Połączenia",
    "settings": "Ustawienia",
    "reports": "Raporty",
    "users": "Użytkownicy",
    "rozliczenia": "Rozliczenia",
}

ACTION_LABELS = {
    "read": "Odczyt",
    "write": "Zapis",
    "delete": "Usuwanie",
}

# Settlement attachments
SETTLEMENT_ATTACHMENT_MAX_SIZE = 200 * 1024 * 1024  # 200 MB
MAX_ATTACHMENTS_PER_SETTLEMENT = 50

SETTLEMENT_CATEGORIES = frozenset({"invoice", "report", "acceptance_protocol", "other"})

SETTLEMENT_STATES = frozenset({"draft", "sent", "paid"})

SETTLEMENT_ATTACHMENT_STATES = frozenset({"draft", "signed"})

SETTLEMENT_ATTACHMENT_STATE_LABELS: dict[str, str] = {
    "draft": "Szkic",
    "signed": "Podpisany",
}

SETTLEMENT_ALLOWED_EXT = frozenset(
    {
        ".pdf",
        ".xls",
        ".xlsx",
        ".doc",
        ".docx",
        ".odt",
        ".ods",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".txt",
        ".csv",
        ".md",
        ".rtf",
        ".zip",
        ".7z",
    }
)

SETTLEMENT_CATEGORY_LABELS = {
    "invoice": "Faktura",
    "report": "Raport",
    "acceptance_protocol": "Protokół odbioru",
    "other": "Inne",
}

SETTLEMENT_STATE_LABELS = {
    "draft": "Szkic",
    "sent": "Wysłane",
    "paid": "Opłacone",
}

# Default permissions for system roles
DEFAULT_ROLE_PERMISSIONS = {
    "owner": {m: list(PERMISSION_ACTIONS) for m in PERMISSION_MODULES},
    "admin": {m: list(PERMISSION_ACTIONS) if m not in ("users", "settings", "rozliczenia") else ["read", "write"] for m in PERMISSION_MODULES},
    "member": {
        "500ki": ["read"],
        "scrum": ["read", "write"],
        "monitoring": ["read"],
        "heartbeat": ["read"],
        "wiki": ["read", "write"],
        "connections": ["read"],
        "settings": ["read"],
        "reports": ["read"],
        "users": ["read"],
        "rozliczenia": [],
    },
}
