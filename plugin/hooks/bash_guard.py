#!/usr/bin/env python3
"""PreToolUse guard (Bash) - egzekwuje reguly git/test workflow Monolynx.

Zasady:
- Subagent NIGDY nie robi git add/commit/push/reset --hard/rebase ani nie
  odpala lintu/testow - to nalezy do Team Managera (glowna sesja)
  i uzytkownika. Dotad byla to tylko prosba w prompcie; hook ja egzekwuje.
- Operacje destrukcyjne (rm -rf, force-push, curl|sh, zapis do ~/.ssh,
  DROP/TRUNCATE w SQL, mkfs/dd na urzadzenie): subagent -> deny,
  glowna sesja -> ask.
- `glab mr create`: subagent -> deny; glowna sesja -> ask, chyba ze
  MONOLYNX_AUTOMR=true.
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
GLAB_MR_CREATE = re.compile(r"\bglab\b[^|;&]*?\bmr\b[^|;&]*?\bcreate\b(?!-)")
TEST_LINT = re.compile(
    r"\b(pytest|ruff|mypy|black|isort|flake8|eslint|prettier|vitest|playwright"
    r"|makemessages|compilemessages)\b"
)
TMP_CLEANUP_COMPOUND = re.compile(r"[|;&`\n\r]|\$\(")
TMP_CLEANUP_PATH = re.compile(r"^/(private/)?tmp/(?:[^/]+/)*[^/]*\.(log|txt|md)$")


def is_tmp_cleanup(cmd: str) -> bool:
    """Allow-lista: `rm -f` na plikach *.log/*.txt/*.md pod /tmp lub /private/tmp.

    Wyjatek od reguly DESTRUCTIVE, zeby agent mogl posprzatac wlasne pliki
    robocze bez pytania uzytkownika. Celowo BEZ -r/-R/--recursive - glob
    z rozszerzeniem (np. /tmp/*.md) moze trafic w katalog o takiej nazwie
    i skasowac go rekurencyjnie. Nowa linia/CR w komendzie liczy sie jak
    separator instrukcji (shell wykona kazda linie osobno), wiec konczy
    sie False tak samo jak `;`/`|`/`&`. Argument zawierajacy `$` lub `~`
    (ekspansja zmiennej/home shella) tez konczy sie False - hook widzi
    literalny tekst PRZED ekspansja, wiec nie ma jak bezpiecznie sprawdzic
    faktycznej sciezki. Podkatalogi sa dozwolone (scratchpad sesji lezy
    w /private/tmp/claude-<uid>/<projekt>/<sesja>/scratchpad/), ale sciezka
    nadal musi konczyc sie plikiem o dozwolonym rozszerzeniu - sam katalog
    nigdy nie przechodzi. Kazda watpliwosc (nieznana flaga, zlozona komenda,
    sciezka spoza /tmp, ekspansja shella) konczy sie False i normalnym
    przebiegiem do DESTRUCTIVE (fail-closed). Patrz wiki spec MON-147.
    """
    stripped = cmd.strip()
    if not stripped or TMP_CLEANUP_COMPOUND.search(stripped):
        return False

    tokens = stripped.split()
    if tokens[0] != "rm":
        return False

    paths = []
    for arg in tokens[1:]:
        if arg.startswith("-"):
            if arg not in ("-f", "--force"):
                return False
            continue
        if "$" in arg or "~" in arg:
            return False
        paths.append(arg)

    if not paths:
        return False

    for path in paths:
        if ".." in path or not TMP_CLEANUP_PATH.match(path):
            return False

    return True


if is_tmp_cleanup(cmd):
    sys.exit(0)

_RM_LONG_FLAG_NAMES = ("recursive", "force", "dir")
_RM_SEGMENT_SPLIT = re.compile(r"[;&|`\n\r]+|\$\(")


def _rm_short_cluster_is_destructive(token: str) -> bool:
    """Klaster krotkich flag (`-rf`, `-Rf`, `-iRf`, `-d`...) - kazda litera osobno,
    niezaleznie od wielkosci i pozycji w klastrze."""
    return any(ch.lower() in ("r", "f", "d") for ch in token[1:])


def _rm_long_flag_is_destructive(token: str) -> bool:
    """Forma dluga i jej jednoznaczny prefiks getopt_long (`--r`, `--rec`,
    `--recursive`, opcjonalnie `--recursive=wartosc`)."""
    name = token[2:].split("=", 1)[0]
    if not name:
        return False
    return any(long_name.startswith(name) for long_name in _RM_LONG_FLAG_NAMES)


def _is_destructive_rm(cmd: str) -> bool:
    """True, jesli jakies wywolanie `rm` w komendzie ma flage recursive/force/dir.

    Parsuje tokeny zamiast wyliczac pisownie flag (regresja MON-147: staly
    wzorzec z klasa `[rf]` gubil `-R` samo w sobie, prefiksy dlugich opcji
    typu `--rec` i krotka forme `-d`, a takze flage nie na pierwszej pozycji
    jak w `rm -i -rf /`). Komenda jest najpierw dzielona na proste polecenia
    po separatorach powloki (`;`, `&`, `|`, `` ` ``, `$(`, nowa linia/CR), zeby
    flaga z sasiedniego polecenia (np. `rm plik && npm i --force`) nie zostala
    przypisana do `rm`. Slowo `rm` musi byc osobnym tokenem (whitespace-split) -
    `charm -rf x` czy `confirm --force` nie zawieraja tokenu `rm`. To NIE jest
    pelny parser powloki - swiadomy kompromis na rzecz prostoty i czytelnosci.
    """
    for segment in _RM_SEGMENT_SPLIT.split(cmd):
        tokens = segment.split()
        if "rm" not in tokens:
            continue
        for token in tokens:
            if token == "rm":
                continue
            if token.startswith("--"):
                if _rm_long_flag_is_destructive(token):
                    return True
            elif token.startswith("-") and len(token) > 1:
                if _rm_short_cluster_is_destructive(token):
                    return True
    return False


DESTRUCTIVE = [
    (
        _is_destructive_rm,
        "rm z flaga recursive/force/dir (krotka, dluga lub jednoznaczny prefiks)",
    ),
    (lambda c: bool(re.search(r"\bgit\s+push\b[^|;&]*?(--force\b|\s-f\b)", c)), "git push --force"),
    (
        lambda c: bool(re.search(r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z)?sh\b", c)),
        "pobranie skryptu z sieci i wykonanie (curl | sh)",
    ),
    (lambda c: bool(re.search(r"(>>?|\btee\b)\s*\S*\.(ssh|aws)/", c)), "zapis do ~/.ssh lub ~/.aws"),
    (
        lambda c: bool(re.search(r"\b(DROP\s+(TABLE|DATABASE|SCHEMA)|TRUNCATE(\s+TABLE)?\s)\b", c, re.I)),
        "destrukcyjny SQL (DROP/TRUNCATE)",
    ),
    (
        lambda c: bool(re.search(r"\bmkfs\b|\bdd\b[^|;&]*\bof=/dev/", c)),
        "operacja niszczaca na urzadzeniu",
    ),
]

for matcher, label in DESTRUCTIVE:
    if matcher(cmd):
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

# glab przed GIT_WRITE (nie po): galaz GIT_WRITE konczy hook przez
# sys.exit(0), wiec `git push && glab mr create` nigdy by tu nie dotarlo.
if GLAB_MR_CREATE.search(cmd):
    if is_subagent:
        decide(
            "deny",
            "Subagent nie tworzy merge requestów - MR zakłada Team Manager "
            "(główna sesja) po pushu, wg MONOLYNX_AUTOMR. Zgłoś gotowość "
            "w raporcie końcowym.",
        )
    if not flag("MONOLYNX_AUTOMR"):
        decide(
            "ask",
            "glab mr create wymaga potwierdzenia użytkownika "
            "(MONOLYNX_AUTOMR != true).",
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
