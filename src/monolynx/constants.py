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
