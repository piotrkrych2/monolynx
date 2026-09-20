#!/usr/bin/env python3
"""PreToolUse guard (Bash) - egzekwuje reguly git/test workflow Monolynx.

Zasady:
- Subagent NIGDY nie robi git add/commit/push/reset --hard/rebase ani nie
  odpala lintu/testow - to nalezy do Team Managera (glowna sesja)
  i uzytkownika. Dotad byla to tylko prosba w prompcie; hook ja egzekwuje.
- Operacje destrukcyjne (rm -rf, force-push, curl|sh, zapis do ~/.ssh,
  DROP/TRUNCATE w SQL, mkfs/dd na urzadzenie): subagent -> deny,
  glowna sesja -> ask. Wyjatek `rm -rf` (MON-174): cel pod /tmp, pod
  katalogiem projektu albo pod katalogiem .claude przechodzi bez pytania
  (takze subagent); cel poza nimi to deny dla wszystkich; cel nie do
  zweryfikowania (ekspansja, glob, komenda zlozona) -> jak dotad.
- `glab mr create`: subagent -> deny; glowna sesja -> ask, chyba ze
  MONOLYNX_AUTOMR=true.
- `glab mr merge|accept` i `gh pr merge` (skill mr-queue): subagent -> deny;
  glowna sesja -> ask, chyba ze MONOLYNX_AUTOMERGE=true.
- `glab mr approve` i `gh pr review --approve`: subagent -> deny; glowna
  sesja -> zawsze ask. Approve jest decyzja czlowieka, zadna flaga go nie znosi.
- Glowna sesja nie jest blokowana na twardo: git push / reset --hard / rebase /
  clean -f oraz lint/testy dostaja "ask" (potwierdzenie uzytkownika); push
  przechodzi bez pytania przy MONOLYNX_AUTOPUSH=true, lint/testy przy
  MONOLYNX_AUTOTEST=true. git add/commit w glownej sesji przechodza normalnie
  (zwykly system uprawnien).

Wyjscie: brak outputu + exit 0 = przepusc; JSON z permissionDecision
deny/ask = zablokuj / zapytaj uzytkownika.

Ladowanie i zaufanie (MON-184): ten skrypt jest deklarowany w
plugin/hooks/hooks.json (matcher PreToolUse/Bash) i uruchamiany przez klienta
(Claude Code albo Codex CLI, patrz plugin.json / .codex/hooks.json) PRZED
kazdym wywolaniem Bash - wymaga wiec, zeby uzytkownik zainstalowal/zaufal
pluginowi Monolynx w danym kliencie; bez tego zaufania klient w ogole nie
uruchomi tego procesu i zadna z ponizszych regul nie zadziala (deny/ask nie
zastepuje decyzji o zaufaniu, tylko dziala PO niej). Zeby ten brak aktywnej
ochrony byl widoczny zamiast cichy, kazde wywolanie z rozpoznanym
`session_id` dotyka best-effort markera `~/.cache/monolynx/hook-active/
<session_id>` (patrz `touch_hook_active_marker` nizej) - jego NIEOBECNOSC
dla biezacej sesji jest sygnalem, ze hook nie zdazyl jeszcze zadzialac albo
nie jest zaladowany/zaufany. Marker niesie tez, czy ten konkretny payload
mial pole `agent_id` - Codex CLI nigdy go nie wysyla w PreToolUse (patrz
tests/fixtures/codex/README.md, Znalezisko 1), wiec seria markerow bez tego
pola w calej sesji sygnalizuje degradacje ochrony subagenta, nie tylko brak
hooka. Zapis markera jest fail-open: blad IO nigdy nie zmienia decyzji.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def hook_active_marker_path(session_id: str) -> Path:
    """Sciezka markera aktywnego hooka - ta sama konwencja sanitizacji
    identyfikatora sesji co marker brief_guard.py (znaki spoza
    [A-Za-z0-9_-] -> "_"), zeby przetestowac ja identycznie po obu stronach."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
    return Path.home() / ".cache" / "monolynx" / "hook-active" / safe


def touch_hook_active_marker(session_id: str, agent_fields_present: bool) -> None:
    """Best-effort marker potwierdzajacy, ze hook zyje i przetworzyl to
    wywolanie PreToolUse (AC6, MON-184). Fail-open: kazdy blad IO jest
    polykany i NIGDY nie zmienia decyzji hooka (jak brief_guard.py:69-75).
    Brak `session_id` -> marker sie nie zapisuje, hook dziala dalej normalnie.
    `agent_fields_present` niesie, czy TEN payload mial niepuste `agent_id` -
    to sygnal, czy klient w ogole identyfikuje subagentow w PreToolUse (patrz
    docstring modulu)."""
    session_id = (session_id or "").strip()
    if not session_id:
        return
    try:
        marker = hook_active_marker_path(session_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "subagent_identification": agent_fields_present,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def decide(decision: str, reason: str) -> None:
    # Tryb sesji w tle (MONOLYNX_SPRINT_RUN=1): "ask" nie ma adresata - sesja
    # bez czlowieka zawisla by na promptcie na zawsze (MON-173, pierwszy
    # przebieg dyspozytora). Deny z instrukcja: agent wybiera wariant
    # nieniszczacy albo konczy ture linia SPRINT-RUN STOP (work, regula 10).
    if decision == "ask" and os.environ.get("MONOLYNX_SPRINT_RUN", "").strip() == "1":
        decision = "deny"
        reason = (
            "Tryb sesji w tle (MONOLYNX_SPRINT_RUN=1): nikt nie potwierdzi tej "
            "operacji. Uzyj wariantu nieniszczacego (rm bez -f/-r na pliku, ktory "
            "sam utworzyles; docker stop + docker rm bez -f) albo, jesli operacja "
            "jest niezbedna, zakoncz ture linia 'SPRINT-RUN STOP: <powod>'. "
            "Pierwotny powod: " + reason
        )
    # Escape hatch dla klientow, ktore nie egzekwuja "ask" (np. Codex CLI
    # oznacza taki hook jako failed i mimo to wykonuje komende - fail-open,
    # patrz tests/fixtures/codex/README.md, Znalezisko 2). Domyslnie
    # wylaczone: opt-in, nie nowa polityka. Wzorowane na obsludze
    # MONOLYNX_SPRINT_RUN powyzej - skoro nikt nie odpowie na "ask", hook
    # zwraca "deny" zamiast fail-open "allow".
    if decision == "ask" and flag("MONOLYNX_HOOK_ASK_UNSUPPORTED"):
        decision = "deny"
        reason = (
            "MONOLYNX_HOOK_ASK_UNSUPPORTED=true: ten klient nie egzekwuje "
            "decyzji 'ask' (parsowane, ale wykonuje komende mimo to - fail-open). "
            "Operacja wymaga recznego uruchomienia przez czlowieka poza hookiem. "
            "Pierwotny powod: " + reason
        )
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

# Marker aktywnego hooka: dotyka KAZDE wywolanie Bash, ktore dotrze tu dalej
# (kazda galaz ponizej konczy sie explicit allow/ask/deny) - patrz docstring
# modulu i touch_hook_active_marker.
touch_hook_active_marker(str(data.get("session_id") or "").strip(), bool(data.get("agent_id")))

GIT_WRITE = re.compile(
    r"\bgit\b[^|;&]*?\b(push|commit|add|reset\s+--hard|rebase|clean\s+(?:-[a-zA-Z]*f[a-zA-Z]*|--force)\b)"
)
GLAB_MR_CREATE = re.compile(r"\bglab\b[^|;&]*?\bmr\b[^|;&]*?\bcreate\b(?!-)")
# `glab mr accept` to alias `glab mr merge` (glab mr merge --help, 1.100.0).
MR_MERGE = re.compile(r"\bglab\b[^|;&]*?\bmr\b[^|;&]*?\b(?:merge|accept)\b(?!-)|\bgh\b[^|;&]*?\bpr\b[^|;&]*?\bmerge\b(?!-)")
MR_APPROVE = re.compile(
    r"\bglab\b[^|;&]*?\bmr\b[^|;&]*?\bapprove\b(?!-)|\bgh\b[^|;&]*?\bpr\b[^|;&]*?\breview\b[^|;&]*?(?:--approve\b|\s-a\b)"
)
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


_RM_ALLOWED_TMP = ("/tmp", "/private/tmp")
_RM_UNSAFE_ARG = re.compile("[$~*?\\[\"']")


def _project_root(cwd: str) -> str:
    """Korzen repo dla cwd (worktree dyspozytora ma wlasny korzen); brak gita -> cwd."""
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return cwd
    root = out.stdout.strip()
    return root if out.returncode == 0 and root else cwd


def _under(path: str, root: str) -> bool:
    """path lezy POD root (sam root nie liczy sie - skasowanie korzenia to nie sprzatanie)."""
    root = root.rstrip("/")
    return path != root and path.startswith(root + "/")


def rm_target_policy(cmd: str, cwd: str, project_root: str) -> str:
    """Polityka celow `rm`: "allowed" | "forbidden" | "unknown" (MON-174).

    Sesja (takze w tle, bez czlowieka) ma prawo sprzatac WYLACZNIE pod /tmp,
    pod katalogiem projektu (korzen repo, wiec takze worktree
    .claude/worktrees/<KEY>) i pod dowolnym katalogiem `.claude`. Kazdy inny
    cel to "forbidden" -> twarde deny, bez pytania: sesja bez czlowieka nie
    powinna moc kasowac poza tymi katalogami, a czlowiek i tak nie powinien
    tego robic przez agenta. "unknown" (komenda zlozona, `sudo`, ekspansja
    `$`/`~`, glob, brak celu) zostawia dotychczasowy przebieg DESTRUCTIVE
    (fail-closed): hook widzi tekst PRZED ekspansja powloki, wiec nie ma
    jak zweryfikowac faktycznej sciezki. Sam korzen dozwolonego katalogu
    (`rm -rf /tmp`, `rm -rf <repo>`, `rm -rf ~/.claude`) nigdy nie jest
    dozwolony - tylko jego zawartosc.
    """
    stripped = cmd.strip()
    if not stripped or TMP_CLEANUP_COMPOUND.search(stripped):
        return "unknown"
    tokens = stripped.split()
    if tokens[0] != "rm":
        return "unknown"
    targets: list[str] = []
    flags_done = False
    for arg in tokens[1:]:
        if not flags_done and arg == "--":
            flags_done = True
            continue
        if not flags_done and arg.startswith("-"):
            continue
        if _RM_UNSAFE_ARG.search(arg):
            return "unknown"
        targets.append(arg)
    if not targets:
        return "unknown"
    for target in targets:
        path = os.path.normpath(target if os.path.isabs(target) else os.path.join(cwd, target))
        in_tmp = any(_under(path, tmp) for tmp in _RM_ALLOWED_TMP)
        in_project = _under(path, project_root)
        in_claude_dir = ".claude" in path.split("/")[:-1]
        if not (in_tmp or in_project or in_claude_dir):
            return "forbidden"
    return "allowed"


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

hook_cwd = (data.get("cwd") or "").strip() or os.getcwd()

for matcher, label in DESTRUCTIVE:
    if matcher(cmd):
        if matcher is _is_destructive_rm:
            policy = rm_target_policy(cmd, hook_cwd, _project_root(hook_cwd))
            if policy == "allowed":
                continue
            if policy == "forbidden":
                decide(
                    "deny",
                    "rm z flaga recursive/force/dir poza dozwolonymi katalogami. "
                    "Kasowac wolno tylko pod /tmp, pod katalogiem projektu (korzen "
                    "repo albo worktree) i pod katalogami .claude - nigdy sam korzen "
                    "tych katalogow. [MON-174]",
                )
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

if MR_APPROVE.search(cmd):
    if is_subagent:
        decide(
            "deny",
            "Subagent nie zatwierdza MR/PR - approve daje człowiek. Zgłoś gotowość w raporcie końcowym.",
        )
    decide(
        "ask",
        "Approve MR/PR to decyzja użytkownika - wymaga potwierdzenia (żadna flaga tego nie znosi).",
    )

# Merge przed GIT_WRITE z tego samego powodu co glab mr create: przy
# MONOLYNX_AUTOMERGE=true galaz nie konczy hooka, komenda zlozona idzie dalej.
if MR_MERGE.search(cmd):
    if is_subagent:
        decide(
            "deny",
            "Subagent nie merguje MR/PR - merge robi skill mr-queue w głównej sesji, wg MONOLYNX_AUTOMERGE.",
        )
    if not flag("MONOLYNX_AUTOMERGE"):
        decide(
            "ask",
            "Merge MR/PR wymaga potwierdzenia użytkownika (MONOLYNX_AUTOMERGE != true).",
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
