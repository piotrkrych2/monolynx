#!/usr/bin/env python3
"""PreToolUse guard (Bash) - egzekwuje reguly git/test workflow Monolynx.

Zasady:
- Subagent NIGDY nie robi git add/commit/push/reset --hard/rebase ani nie
  odpala lintu/testow - to nalezy do Team Managera (glowna sesja)
  i uzytkownika. Dotad byla to tylko prosba w prompcie; hook ja egzekwuje.
- Operacje destrukcyjne (rm -rf, force-push, curl|sh, zapis do ~/.ssh,
  DROP/TRUNCATE w SQL, mkfs/dd na urzadzenie): subagent -> deny,
  glowna sesja -> ask.
- Glowna sesja nie jest blokowana na twardo: git push / reset --hard / rebase /
  clean -f oraz lint/testy dostaja "ask" (potwierdzenie uzytkownika); push
  przechodzi bez pytania przy MONOLYNX_AUTOPUSH=true, lint/testy przy
  MONOLYNX_AUTOTEST=true. git add/commit w glownej sesji przechodza normalnie
  (zwykly system uprawnien).

Wyjscie: brak outputu + exit 0 = przepusc; JSON z permissionDecision
deny/ask = zablokuj / zapytaj uzytkownika.
"""

import json
import os
import re
import sys


def decide(decision: str, reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() == "true"


try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if data.get("tool_name") != "Bash":
    sys.exit(0)

cmd = (data.get("tool_input") or {}).get("command") or ""

# Pola agent_id/agent_type sa obecne, gdy tool wywoluje subagent. Brak pol
# traktujemy jak glowna sesje (bezpieczny default: nie blokowac uzytkownika).
agent_type = (data.get("agent_type") or "").strip()
is_subagent = bool(data.get("agent_id")) and agent_type not in ("", "main")

GIT_WRITE = re.compile(
    r"\bgit\b[^|;&]*?\b(push|commit|add|reset\s+--hard|rebase|clean\s+(?:-[a-zA-Z]*f[a-zA-Z]*|--force)\b)"
)
TEST_LINT = re.compile(
    r"\b(pytest|ruff|mypy|black|isort|flake8|eslint|prettier|vitest|playwright"
    r"|makemessages|compilemessages)\b"
)
DESTRUCTIVE = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+"), "rm z flagami -r/-f"),
    (re.compile(r"\bgit\s+push\b[^|;&]*?(--force\b|\s-f\b)"), "git push --force"),
    (
        re.compile(r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z)?sh\b"),
        "pobranie skryptu z sieci i wykonanie (curl | sh)",
    ),
    (re.compile(r"(>>?|\btee\b)\s*\S*\.(ssh|aws)/"), "zapis do ~/.ssh lub ~/.aws"),
    (
        re.compile(r"\b(DROP\s+(TABLE|DATABASE|SCHEMA)|TRUNCATE(\s+TABLE)?\s)\b", re.I),
        "destrukcyjny SQL (DROP/TRUNCATE)",
    ),
    (re.compile(r"\bmkfs\b|\bdd\b[^|;&]*\bof=/dev/"), "operacja niszczaca na urzadzeniu"),
]

for pattern, label in DESTRUCTIVE:
    if pattern.search(cmd):
        if is_subagent:
            decide(
                "deny",
                f"Subagent nie wykonuje operacji destrukcyjnych ({label}). "
                "Jesli to konieczne dla zadania - opisz potrzebe w raporcie "
                "koncowym, decyzje podejmie Team Manager z uzytkownikiem.",
            )
        decide(
            "ask",
            f"Operacja destrukcyjna ({label}) - wymaga potwierdzenia uzytkownika.",
        )

git_match = GIT_WRITE.search(cmd)
if git_match:
    op = git_match.group(1).split()[0]
    if is_subagent:
        decide(
            "deny",
            f"Subagent nie wykonuje 'git {op}' - operacje zapisu w gicie naleza "
            "do Team Managera i uzytkownika (workflow Monolynx). Zglos zmiany "
            "w raporcie koncowym zamiast commitowac.",
        )
    if op == "push" and not flag("MONOLYNX_AUTOPUSH"):
        decide(
            "ask",
            "git push wymaga potwierdzenia uzytkownika "
            "(MONOLYNX_AUTOPUSH != true).",
        )
    if op in ("reset", "rebase", "clean"):
        decide(
            "ask",
            f"git {op} przepisuje/kasuje stan repo - wymaga potwierdzenia "
            "uzytkownika.",
        )
    sys.exit(0)

if TEST_LINT.search(cmd):
    if is_subagent:
        decide(
            "deny",
            "Subagent nie odpala lintu/testow - robi to Team Manager w fazie "
            "lint/test, wg MONOLYNX_AUTOTEST (workflow Monolynx). Zglos "
            "gotowosc kodu w raporcie koncowym.",
        )
    if not flag("MONOLYNX_AUTOTEST"):
        decide(
            "ask",
            "MONOLYNX_AUTOTEST != true - lint/testy domyslnie uruchamia "
            "uzytkownik. Potwierdz, jesli Claude ma je odpalic sam.",
        )

sys.exit(0)
