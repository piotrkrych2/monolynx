"""Stale uzywane w modulach (statusy, priorytety, etykiety)."""

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
