#!/usr/bin/env python3
"""PreToolUse guard (Bash) - egzekwuje reguly git/test workflow Monolynx.

Zasady:
- Subagent NIGDY nie robi git add/commit/push/reset --hard/rebase ani nie
  odpala lintu/testow - to nalezy do Team Managera (glowna sesja)
  i uzytkownika. Dotad byla to tylko prosba w prompcie; hook ja egzekwuje.
  Wyjatek (MON-176, zawezony w MON-207 po code review MR !98): `ruff check
  --no-fix` (goly `ruff check` bez tej flagi juz NIE wystarcza - `pyproject.
  toml` moze ustawic `fix = true` i cicho naprawiac pliki) i `ruff format
  --check` albo `--diff` sa odczytem, wiec TYLKO SUBAGENT moze je uruchomic
  na wlasnych plikach bez posrednictwa Team Managera - takze przez `docker
  compose exec [-T] app` i `docker compose ... run <flagi> app` (wariant
  worktree; `-v`/`--volume` dozwolone tylko gdy host wskazuje doslownie na
  korzen projektu, patrz `is_readonly_ruff`/`_docker_prefix_end`). Sesja
  glowna dla tych samych komend wraca do zwyklej galezi TEST_LINT (ask bez
  MONOLYNX_AUTOTEST, allow z nim) - nie dostaje juz automatycznego allow bez
  pytania. `docker run <obraz> ruff ...` zniknal calkowicie z wyjatku -
  dowolny obraz i dowolny bind mount to zbyt duza powierzchnia ataku. `ruff
  check --fix`, `ruff format` bez `--check/--diff`, przekierowanie (`<`/`>`,
  takze doklejone jak `>/etc/hosts` albo `1>plik`) i wszystko poza tym waskim
  zestawem nadal spada do zwyklej galezi TEST_LINT ponizej.
- Wrapper operacji, preflight transportu i CLI `monolynx` (MON-232): komenda
  prosta `python3 <katalog>/scripts/monolynx_op.py <op> ...`, `python3
  <katalog>/scripts/monolynx_transport.py ...` albo `monolynx|mnx ...` (bez
  `|;&`, backticka, nowej linii/CR, `$`, `~`, redirectow, globow i nawiasow ( ), nazwa pliku
  skryptu dokladnie jak wyzej) przechodzi bez decyzji hooka, przed regulami
  ponizej - slowa w tresci argumentow (`git commit`, `pytest`, `merge`) nie sa
  wtedy komenda. Odczyt (`MONOLYNX_READ_OPS`, preflight, komendy get/list/
  search/...) i zapis koncza sie tak samo: ten hook nie ma jawnego "allow",
  wiec zapis wraca do zwyklego systemu uprawnien klienta (jak zapisy MCP).
  `--yes`/`-y` i komendy login/logout/set/unset/delete/remove w pierwszych
  trzech slowach CLI nie dostaja wyjatku - stare reguly dzialaja jak dotad.
  Komenda zlozona, inny skrypt, `.bak` albo `notmonolynx_op.py` tez wracaja do
  starych regul.
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
nie jest zaladowany/zaufany. Marker niesie tez `subagent_identification`:
true = w tej sesji hook widzial co najmniej jeden payload z niepustym
`agent_id` (sticky w obrebie sesji, bo lider i agenci in-process dziela
`session_id`). Codex CLI nigdy nie wysyla `agent_id` w PreToolUse (patrz
tests/fixtures/codex/README.md, Znalezisko 1), wiec sesja z samym false
sygnalizuje degradacje ochrony agenta pomocniczego, nie tylko brak hooka.
Zapis markera jest fail-open: blad IO nigdy nie zmienia decyzji.

Agent pomocniczy (MON-224) = wywolanie z niepustym `agent_id`: subagent
i teammate Agent Teams in-process. Ograniczenie: teammate w trybie tmux nie ma
`agent_id` i jest nieodroznialny od sesji glownej uruchomionej z `--agent`,
wiec dostaje zachowanie sesji glownej (ask / flagi AUTO*), nie subagenta.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def hook_active_marker_path(session_id: str) -> Path:
    """Sciezka markera aktywnego hooka - ta sama konwencja sanitizacji
    identyfikatora sesji co marker brief_guard.py (znaki spoza
    [A-Za-z0-9_-] -> "_"), zeby przetestowac ja identycznie po obu stronach."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
    return Path.home() / ".cache" / "monolynx" / "hook-active" / safe


def touch_hook_active_marker(session_id: str, is_subagent: bool) -> None:
    """Best-effort marker potwierdzajacy, ze hook zyje i przetworzyl to
    wywolanie PreToolUse (AC6, MON-184). Fail-open: kazdy blad odczytu lub
    zapisu jest polykany i NIGDY nie zmienia decyzji hooka (jak
    brief_guard.py:69-75). Brak `session_id` -> marker sie nie zapisuje, hook
    dziala dalej normalnie.

    `subagent_identification` = true oznacza, ze w tej sesji hook widzial co
    najmniej jeden payload agenta pomocniczego (niepuste `agent_id`), czyli
    klient identyfikuje takich agentow w PreToolUse. Wartosc jest sticky-true
    w obrebie sesji: lider i agenci in-process dziela `session_id`, wiec
    komenda lidera (takze `find`, ktore czyta marker w skillu work 6d)
    nadpisywala true wartoscia false i stan "ochrona potwierdzona" byl
    nieosiagalny (objaw z MON-143). Poprzedni marker nieistniejacy,
    nieczytelny, niebedacy JSON-owym dictem albo bez true liczy sie jak brak
    wczesniejszego true. Teammate tmux nie ma `agent_id` (nieodroznialny od
    sesji glownej), wiec sam nigdy nie ustawi true."""
    session_id = (session_id or "").strip()
    if not session_id:
        return
    try:
        marker = hook_active_marker_path(session_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        identified = is_subagent
        if not identified:
            try:
                previous = json.loads(marker.read_text(encoding="utf-8"))
                identified = isinstance(previous, dict) and previous.get("subagent_identification") is True
            except (OSError, ValueError):
                pass
        marker.write_text(
            json.dumps(
                {
                    "subagent_identification": identified,
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

# Agentem pomocniczym (subagent albo teammate Agent Teams in-process) jest
# wywolanie z niepustym `agent_id`. Nie sprawdzamy `agent_type`: u teammate'a
# in-process to jego dowolna nazwa (fixture pretooluse-bash-teammate-inprocess.
# 2.1.284.json: agent_type="probe"), wiec teammate nazwany "main" omijalby
# regule, a literalu "main" nie ma w zadnym fixture. Zaden payload sesji
# glownej (pretooluse-bash-main / -lead-tmux / -main-agent-flag) nie ma
# `agent_id`. Teammate tmux (pretooluse-bash-teammate-tmux.2.1.284.json) nie ma
# `agent_id` i ksztaltem jest nieodroznialny od sesji glownej z `--agent`
# (pretooluse-bash-main-agent-flag.2.1.284.json), wiec traktujemy go jak sesje
# glowna - bez heurystyk (ograniczenie opisane w plugin/README.md). Brak pola =
# sesja glowna (bezpieczny default: nie blokowac uzytkownika).
is_subagent = bool(data.get("agent_id"))

# Marker aktywnego hooka: dotyka KAZDE wywolanie Bash, ktore dotrze tu dalej
# (kazda galaz ponizej konczy sie explicit allow/ask/deny) - patrz docstring
# modulu i touch_hook_active_marker.
touch_hook_active_marker(str(data.get("session_id") or "").strip(), is_subagent)

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

# Kopia `READ_OPS` z plugin/scripts/monolynx_op.py: hook jest samodzielnym skryptem
# bez importow z pluginu, a literal pozwala testowi wyciagnac stala przez ast
# (tests/unit/test_bash_guard.py pilnuje rownosci z wrapperem).
MONOLYNX_READ_OPS = frozenset(
    {
        "get_ticket",
        "list_tickets",
        "search_tickets",
        "list_comments",
        "get_board",
        "list_sprints",
        "get_burndown",
        "list_acceptance_criteria",
        "list_labels",
        "search_wiki",
        "get_wiki_page",
        "list_wiki_pages",
        "get_wiki_config",
    }
)
_MONOLYNX_SCRIPTS = ("monolynx_op.py", "monolynx_transport.py")
# Skladnia powloki zmieniajaca znaczenie komendy poza widocznymi tokenami, takze wewnatrz
# cudzyslowu (fail-closed): `|;&`, backtick, nowa linia/CR, `$`, `~`, redirecty, glob,
# nawiasy `(` `)` (subshell).
_MONOLYNX_UNSAFE = re.compile(r"[|;&`\n\r$~<>*?\[{()]")
_MONOLYNX_OP_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
# Flagi globalne CLI przed grupa (cli/src/monolynx_cli/main.py); te z wartoscia pochlaniaja nastepny token.
_MONOLYNX_VALUE_FLAGS = ("--output", "-o", "--profile", "--project", "--timeout")
# Slowa komendy, przy ktorych CLI loguje, zmienia konfiguracje albo kasuje: bez wyjatku, stare reguly zostaja.
_MONOLYNX_RISKY_WORDS = ("login", "logout", "set", "unset", "delete", "remove")


def _monolynx_script_call(tokens: list) -> "str | None":
    """`python3 <sciezka>/monolynx_op.py <op> ...` albo `.../monolynx_transport.py ...`:
    "read" | "write" | None. Sciezka bez `..`, z nazwa pliku DOKLADNIE jak w
    `_MONOLYNX_SCRIPTS` (nie `.bak`, nie `notmonolynx_op.py`) i w katalogu `scripts`."""
    if tokens[0] not in ("python3", "python") or len(tokens) < 2:
        return None
    script = PurePosixPath(tokens[1])
    if script.name not in _MONOLYNX_SCRIPTS or ".." in script.parts or script.parent.name != "scripts":
        return None
    if script.name == "monolynx_transport.py":
        return "read"
    if len(tokens) < 3 or not _MONOLYNX_OP_NAME.match(tokens[2]):
        return None
    return "read" if tokens[2] in MONOLYNX_READ_OPS else "write"


def _monolynx_cli_call(tokens: list) -> "str | None":
    """`monolynx ...` / `mnx ...`: "read" | "write" | None (None = `--yes`/`-y` albo komenda
    logowania, zmiany konfiguracji, kasowania w pierwszych trzech slowach)."""
    if tokens[0] not in ("monolynx", "mnx"):
        return None
    words: list = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in ("--yes", "-y"):
            return None
        if token.startswith("-"):
            index += 2 if token in _MONOLYNX_VALUE_FLAGS else 1
            continue
        words.append(token)
        index += 1
    if any(word in _MONOLYNX_RISKY_WORDS for word in words[:3]):
        return None
    if not words or any(word in ("get", "list", "search", "summary", "board", "burndown", "whoami", "version") for word in words[:3]):
        return "read"
    return "write"


def monolynx_call_kind(cmd: str) -> "str | None":
    """MON-232: wywolanie wrappera operacji, preflightu transportu albo CLI `monolynx`
    w formie prostej. "read" | "write" | None (None = nie rozpoznano, stare reguly).

    Forma prosta (fail-closed jak is_tmp_cleanup/is_readonly_ruff): bez `|;&`,
    backticka, nowej linii/CR, `$`, `~`, redirectow, globow i nawiasow ( ), takze wewnatrz
    cudzyslowu, a pierwszy token to dokladnie `python3 <...>/scripts/<skrypt>` albo
    `monolynx`/`mnx` - `echo monolynx_op.py ...` i obce skrypty nie pasuja. Slowa w
    tresci argumentow (`git commit`, `pytest`) nie sa wtedy komenda, wiec nie
    powinny wyzwalac GIT_WRITE/TEST_LINT/MR_MERGE/DESTRUCTIVE."""
    stripped = cmd.strip()
    if not stripped or _MONOLYNX_UNSAFE.search(stripped):
        return None
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return None
    if not tokens:
        return None
    return _monolynx_script_call(tokens) or _monolynx_cli_call(tokens)


# Odczyt i zapis w formie prostej koncza sie tak samo: ten hook nie ma jawnego "allow"
# (pusty stdout + exit 0 = przepusc, jak przy is_tmp_cleanup), wiec zapis dostaje zwykly
# system uprawnien klienta, a odczyt nie jest dodatkowo blokowany.
if monolynx_call_kind(cmd) is not None:
    sys.exit(0)

_DOCKER_COMPOSE_PRE_SUBCOMMAND_FLAGS = ("-p", "--profile")
_DOCKER_COMPOSE_RUN_FLAGS = ("--rm", "--no-deps", "-T")
# Sprawdzane raz, globalnie w is_readonly_ruff, na WSZYSTKICH tokenach komendy
# - niebezpieczny token moze stac w dowolnym miejscu (sciezka ruff, wartosc
# -p/--profile/-v, nazwa obrazu docker), a przekierowanie zapisuje plik mimo
# ze cala reszta komendy jest read-only.
_RUFF_UNSAFE_ARG = re.compile(r"[$~*?\[{]")
_RUFF_REDIRECT = re.compile(r"[<>]")


def _ruff_check_readonly(rest: list) -> bool:
    """MON-207: `ruff check` jest odczytem TYLKO z jawna flaga `--no-fix` -
    `pyproject.toml` moze ustawic `fix = true`, wiec goly `ruff check` bez
    zadnej flagi juz nie gwarantuje braku zapisu (code review MR !98). Poza
    `--no-fix` zadna inna flaga nie jest dozwolona (nie tylko --fix/
    --unsafe-fixes/--add-noqa, tez np. --output-file, --watch) - allow-lista
    zamiast wyliczania pisowni (fail-closed jak is_tmp_cleanup/rm_target_policy).
    $/~/glob juz odrzucone globalnie w is_readonly_ruff (kazdy token)."""
    if "--no-fix" not in rest:
        return False
    return not any(arg.startswith("-") and arg != "--no-fix" for arg in rest)


def _ruff_format_readonly(rest: list) -> bool:
    """`ruff format` domyslnie zapisuje pliki - odczyt tylko z --check albo
    --diff, bez zadnej innej flagi (ta sama allow-lista co check)."""
    has_check_or_diff = False
    for arg in rest:
        if arg in ("--check", "--diff"):
            has_check_or_diff = True
            continue
        if arg.startswith("-"):
            return False
    return has_check_or_diff


def _ruff_invocation_readonly(ruff_args: list) -> bool:
    if not ruff_args:
        return False
    subcommand, rest = ruff_args[0], ruff_args[1:]
    if subcommand == "check":
        return _ruff_check_readonly(rest)
    if subcommand == "format":
        return _ruff_format_readonly(rest)
    return False


def _docker_prefix_end(tokens: list, project_root: str) -> "int | None":
    """Zwraca indeks pierwszego tokenu PO prefiksie `docker compose exec [-T]
    app` / `docker compose ... run <flagi> app`, albo None gdy prefiks nie
    jest jedna z tych dwoch waskich, jawnie wspieranych form (nieznana flaga,
    inny subcommand, brak `app` na koncu). Fail-closed: nierozpoznany prefiks
    nigdy nie jest odczytem.

    MON-207 (code review MR !98): `docker run <flagi> <obraz>` zniknal z tej
    funkcji calkowicie (patrz TestDockerRunNeverAllowedForRuff) - dowolny obraz
    i dowolny bind mount to zbyt duza powierzchnia ataku, `docker compose run`
    zostaje jedyna dozwolona forma kontenerowa. W segmencie `run` `-v`/
    `--volume` jest dozwolone TYLKO gdy jego host-path (czesc przed pierwszym
    `:`) jest DOSLOWNIE rowny `project_root` (patrz `_project_root` w wywolaniu
    `is_readonly_ruff`) - inaczej subagent moglby zamontowac dowolny katalog
    hosta pod `/app`. Kazda inna flaga w tym segmencie (w tym `--entrypoint`,
    `--privileged`, `-u`, `--user`, w dowolnej formie `--flaga wartosc` albo
    `--flaga=wartosc`) nigdy nie byla w allow-liscie `_DOCKER_COMPOSE_RUN_FLAGS`,
    wiec konczy petle przez `break` i pada na niedopasowaniu do `app` ponizej -
    ten sam mechanizm co dotychczas, bez potrzeby wyliczania ich osobno.
    $/~/glob w wartosciach flag (-p/--profile) i w nazwie obrazu sprawdzone juz
    w is_readonly_ruff, globalnie po wszystkich tokenach - nie tutaj.
    """
    if tokens[0] != "docker" or len(tokens) < 2 or tokens[1] != "compose":
        return None
    i = 2
    while i < len(tokens) and tokens[i] in _DOCKER_COMPOSE_PRE_SUBCOMMAND_FLAGS:
        if i + 1 >= len(tokens):
            return None
        i += 2
    if i >= len(tokens):
        return None
    subcmd = tokens[i]
    i += 1
    if subcmd == "exec":
        while i < len(tokens) and tokens[i] == "-T":
            i += 1
    elif subcmd == "run":
        while i < len(tokens):
            tok = tokens[i]
            if tok in _DOCKER_COMPOSE_RUN_FLAGS:
                i += 1
                continue
            if tok in ("-v", "--volume"):
                if i + 1 >= len(tokens):
                    return None
                host = tokens[i + 1].split(":", 1)[0]
                if host != project_root:
                    return None
                i += 2
                continue
            break
    else:
        return None
    if i >= len(tokens) or tokens[i] != "app":
        return None
    return i + 1


def is_readonly_ruff(cmd: str, project_root: str) -> bool:
    """MON-176, zawezone w MON-207 (code review MR !98): `ruff check --no-fix`
    (jawna flaga - goly `ruff check` juz NIE wystarcza, patrz
    `_ruff_check_readonly`) i `ruff format --check|--diff` sa odczytem, wiec
    TYLKO SUBAGENT moze je uruchomic sam - takze przez `docker compose exec
    [-T] app` i `docker compose [-p X] [--profile X] run <flagi> app` (wariant
    worktree z toolchainu projektu, `exec` wchodzi do dzialajacego kontenera
    glownego checkoutu; `run` wymaga, gdy uzywa `-v`/`--volume`, ze host-path
    jest doslownie rowny `project_root`). `docker run <flagi> <obraz>` zniknal
    calkowicie z allow-listy (patrz `_docker_prefix_end`). Sesja glowna
    (wywolujaca ta funkcje jest odpowiedzialnoscia dzwoniacego - patrz uzycie
    `is_subagent and is_readonly_ruff(...)` na koncu pliku) dla tych samych
    komend wraca do zwyklej galezi TEST_LINT.

    Komenda musi byc prosta (fail-closed jak is_tmp_cleanup): zadnego
    `|`/`;`/`&`/backticka/nowej linii/`$(` (TMP_CLEANUP_COMPOUND) ani
    przekierowania `<`/`>` (`_RUFF_REDIRECT` - `TMP_CLEANUP_COMPOUND` tego nie
    lapie, `ruff check src/ > ~/.bashrc` nie ma zadnego z jej znakow), zeby
    nie dac powloce wykonac lub zapisac czegos poza widocznym `ruff ...`.
    Tokenizacja jak reszta pliku: `cmd.strip().split()`, bez shlex - token
    z cudzyslowem (np. `sh -c "ruff ..."`) nigdy nie zrowna sie z `ruff`
    i konczy sie False. $/~/glob sprawdzone RAZ, globalnie, w KAZDYM tokenie
    calej komendy (nie tylko w argumentach `ruff` - tez `-p`/`--profile`
    compose i wartosc `-v`), zeby nie trzeba bylo powtarzac tego sprawdzenia
    w kazdym miejscu, gdzie token moze wystapic.

    `project_root` (z `_project_root(hook_cwd)` w wywolaniu) jest jedynym
    dozwolonym host-path dla `-v`/`--volume` w `docker compose run`.
    """
    stripped = cmd.strip()
    if not stripped or TMP_CLEANUP_COMPOUND.search(stripped) or _RUFF_REDIRECT.search(stripped):
        return False

    tokens = stripped.split()
    if any(_RUFF_UNSAFE_ARG.search(tok) for tok in tokens):
        return False

    if tokens[0] == "ruff":
        return _ruff_invocation_readonly(tokens[1:])

    if tokens[0] == "docker":
        prefix_end = _docker_prefix_end(tokens, project_root)
        if prefix_end is None or prefix_end >= len(tokens) or tokens[prefix_end] != "ruff":
            return False
        return _ruff_invocation_readonly(tokens[prefix_end + 1 :])

    return False


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

if is_subagent and is_readonly_ruff(cmd, _project_root(hook_cwd)):
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
