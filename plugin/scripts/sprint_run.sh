#!/usr/bin/env bash
# sprint_run.sh - zewnetrzna petla dyspozytora sprintu Monolynx.
#
# Uruchamia co INTERVAL jeden tick `/monolynx:sprint-run` w SWIEZYM procesie
# `claude -p`, az sprint sie wyczerpie. Sam nie robi zadnej pracy nad ticketami:
# tick dyspozytora startuje nazwane sesje w tle (`claude --bg --name <slug>-<KEY>-<model>`),
# zbiera zakonczone i raportuje. Dziala tak samo lokalnie, w tmux na VPS i z crona.
#
# Uzycie:
#   plugin/scripts/sprint_run.sh [--interval 15m] [--slots 2] [--max-ticks 96] [--close] [--once]
#
#   --interval  odstep miedzy tickami (15m, 900, 1h); domyslnie 15m
#   --slots     rownolegle sesje work; domyslnie $MONOLYNX_SPRINT_PARALLEL albo 2
#   MONOLYNX_SPRINT_MAX_WAITING  limit zywych sesji waiting (domyslnie 2); po jego
#               osiagnieciu tick nie startuje nowych sesji; waiting nie zajmuja slotow
#   --max-ticks bezpiecznik: po tylu tickach petla konczy sie kodem 3
#   --close     po wyczerpaniu sprintu odpal /monolynx:sprint-end i /monolynx:retro
#               (domyslnie petla ZATRZYMUJE sie przed zamknieciem - MR-y przeglada czlowiek)
#   --once      jeden tick i koniec (do crona: cron sam jest petla)
#
# Kontrakt z tickiem: OSTATNIA linia odpowiedzi skilla sprint-run to
#   SPRINT-RUN: remaining=<n> running=<m> waiting=<k> done=<d>
# remaining=0 running=0 -> sprint wykonany. waiting>0 -> sesje czekaja na czlowieka
# (`claude agents`, `claude attach <id>`); petla biegnie dalej, bo reszta pracuje.
#
# Wymagania: uruchom z katalogu projektu (repo git z .claude/settings.json ustawiajacym
# MONOLYNX_PROJECT_SLUG), zalogowany `claude`, plugin monolynx >= 1.8.0.
# Uprawnienia ticku (MON-190): ten skrypt przekazuje `--allowedTools` i
# `--permission-prompts none` do kazdego wywolania `claude -p`, wiec swiezy
# projekt bez recznych regul w `.claude/settings.json` (`permissions.allow`)
# rowniez odpala tick poprawnie - patrz TICK_ALLOWED_TOOLS nizej. Od MON-207
# lista zawiera tez `Bash(MONOLYNX_SPRINT_RUN=1 claude:*)` obok `Bash(claude:*)` -
# `sprint-run/SKILL.md` KROK 4 startuje sesje robocze z inline `MONOLYNX_
# SPRINT_RUN=1 claude --bg ...` (zamiast polegac na tracked settings.json),
# a `Bash(claude:*)` pasuje tylko do polecen zaczynajacych sie doslownie od
# `claude`, wiec bez osobnej reguly to konkretne wywolanie nie byloby objete.
# Tryb -p nie odpowie na zadne pytanie, dlatego skrypt wymusza flagi AUTO*
# i MONOLYNX_SPRINT_RUN=1 (MON-165). Kazda z nich mozna nadpisac w srodowisku.
# Skrypt eksportuje tez MONOLYNX_SPRINT_RUN_MODE=batch (stala kontraktu, NIE do
# nadpisania - skille po niej odrozniaja tryb -p od sesji interaktywnej, bo
# `[ -t 0 ]` w narzedziu Bash Claude Code jest zawsze falszywe) oraz
# CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 domyslnie (worktree nie widzi
# settings.local.json, bez tej flagi sesje w tle traca Agent Teams) - MON-172.
#
# Model: bez `--model` proces `claude -p` bierze domyslny model konta, ktory
# bywa najdrozszy. Tick dyspozytora idzie na MONOLYNX_SPRINT_TICK_MODEL
# (domyslnie opus), a sesje work startowane przez tick na MONOLYNX_SPRINT_MODEL
# (domyslnie opus; skrypt eksportuje ja, skill sprint-run wpisuje ja w `claude --bg`).
#
# Runtime (MON-185): MONOLYNX_SPRINT_RUNTIME (domyslnie "claude") wybiera
# binarke, ktora prowadzi TICK dyspozytora (i - poprzez ten sam prompt skilla -
# executor sprint-end/retro). Jedna zmienna dla calosci (nie osobne pokretlo per
# etap) - MON-182 dostarczyl skille do Codex jako `.agents/skills`, wiec Codex
# zna `$monolynx:work`/`$monolynx:sprint-run` tak samo jak Claude zna
# `/monolynx:...`. `codex exec -m` NIE przyjmuje aliasow opus/sonnet
# (potwierdzone, plugin/scripts/sprint_run_backend.py) - dla runtime=codex
# TICK_MODEL/MONOLYNX_SPRINT_MODEL zostaja tylko do logowania/nazewnictwa,
# nigdy nie trafiaja do argumentow `codex exec`. Sandbox Codex nadpisywalny
# przez MONOLYNX_SPRINT_CODEX_SANDBOX (domyslnie "workspace-write" - NIGDY
# domyslnie --dangerously-bypass-approvals-and-sandbox/--approve-for-me, zakaz
# projektu).

set -u

INTERVAL="${MONOLYNX_SPRINT_RUN_INTERVAL:-15m}"
SLOTS="${MONOLYNX_SPRINT_PARALLEL:-2}"
MAX_WAITING="${MONOLYNX_SPRINT_MAX_WAITING:-2}"
MAX_TICKS=96
CLOSE=0
ONCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --interval) [ $# -ge 2 ] || { echo "sprint_run: --interval wymaga wartosci" >&2; exit 64; }; INTERVAL="$2"; shift 2 ;;
    --slots) [ $# -ge 2 ] || { echo "sprint_run: --slots wymaga wartosci" >&2; exit 64; }; SLOTS="$2"; shift 2 ;;
    --max-ticks) [ $# -ge 2 ] || { echo "sprint_run: --max-ticks wymaga wartosci" >&2; exit 64; }; MAX_TICKS="$2"; shift 2 ;;
    --close) CLOSE=1; shift ;;
    --once) ONCE=1; shift ;;
    -h|--help) sed -n '2,55p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "sprint_run: nieznany argument: $1" >&2; exit 64 ;;
  esac
done

to_seconds() {
  case "$1" in
    *h) echo $(( ${1%h} * 3600 )) ;;
    *m) echo $(( ${1%m} * 60 )) ;;
    *s) echo "${1%s}" ;;
    *) echo "$1" ;;
  esac
}
SLEEP_SECONDS="$(to_seconds "$INTERVAL")"

# MON-185: wybor runtime. Jedna zmienna obejmuje tick dyspozytora, sesje
# robocze ticketow (poprzez prompt skilla) i executor sprint-end/retro.
RUNTIME="${MONOLYNX_SPRINT_RUNTIME:-claude}"
case "$RUNTIME" in
  claude|codex) ;;
  *) echo "sprint_run: MONOLYNX_SPRINT_RUNTIME musi byc 'claude' albo 'codex' (byla: '$RUNTIME')" >&2; exit 64 ;;
esac

command -v "$RUNTIME" >/dev/null 2>&1 || { echo "sprint_run: brak '$RUNTIME' w PATH" >&2; exit 69; }
GIT_DIR="$(git rev-parse --git-dir 2>/dev/null)" || { echo "sprint_run: uruchom z katalogu repozytorium git" >&2; exit 65; }
PROJECT_DIR="$(git rev-parse --show-toplevel)"
cd "$PROJECT_DIR" || exit 65

# Slug: env ma pierwszenstwo; fallback z .claude/settings.json (to samo zrodlo, ktore czyta skill).
SLUG="${MONOLYNX_PROJECT_SLUG:-}"
if [ -z "$SLUG" ] && [ -f .claude/settings.json ]; then
  SLUG="$(python3 -c 'import json,sys;print(json.load(open(".claude/settings.json")).get("env",{}).get("MONOLYNX_PROJECT_SLUG",""))' 2>/dev/null || true)"
fi
[ -n "$SLUG" ] || { echo "sprint_run: brak MONOLYNX_PROJECT_SLUG (env albo .claude/settings.json)" >&2; exit 78; }

# Flagi trybu w tle - bez nich pierwszy push albo lint zatrzyma sesje na hooku "ask".
export MONOLYNX_PROJECT_SLUG="$SLUG"
export MONOLYNX_SPRINT_RUN=1
export MONOLYNX_CONTRACT="${MONOLYNX_CONTRACT:-auto}"
export MONOLYNX_AUTOTEST="${MONOLYNX_AUTOTEST:-true}"
export MONOLYNX_AUTOCOMMIT="${MONOLYNX_AUTOCOMMIT:-true}"
export MONOLYNX_AUTOPUSH="${MONOLYNX_AUTOPUSH:-true}"
export MONOLYNX_AUTOMR="${MONOLYNX_AUTOMR:-true}"
export MONOLYNX_SPRINT_PARALLEL="$SLOTS"
export MONOLYNX_SPRINT_MAX_WAITING="$MAX_WAITING"
# Stala kontraktu (nie ${VAR:-default}) - skille po niej odrozniaja tryb -p
# dyspozytora od sesji interaktywnej, bo `[ -t 0 ]` w narzedziu Bash Claude
# Code jest zawsze falszywe niezaleznie od terminala wywolujacego (MON-172).
export MONOLYNX_SPRINT_RUN_MODE=batch
# Worktree (uzywany przez sesje --bg startowane per ticket) nie widzi
# .claude/settings.local.json, wiec bez tej flagi `work` w sesji w tle traci
# Agent Teams; domyslnie wlaczona, ale nadpisywalna z zewnatrz (MON-172).
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS="${CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS:-1}"
# Bez jawnego modelu tick i sesje work ida na domyslnym modelu konta (bywa to
# najdrozszy model); koordynacje na drozszym modelu wlacza sie tylko swiadomie.
TICK_MODEL="${MONOLYNX_SPRINT_TICK_MODEL:-opus}"
export MONOLYNX_SPRINT_MODEL="${MONOLYNX_SPRINT_MODEL:-opus}"

# Uprawnienia ticku - jeden serwer MCP w formie mcp__<serwer> pokrywa wszystkie
# jego narzedzia; trzy prefiksy bo zaleza od sposobu instalacji (plugin, MCP
# bezposrednio, .mcp.json). Nadpisywalne calosciowo (nie doklejane), zeby
# projekt z innymi narzedziami mogl podac wlasna liste. Nadpisanie NIE trafia
# do logu startu w calosci (patrz ponizej) - tylko incydent 18.09: printenv
# wypisal sekrety do transkryptu, wiec zadna lista narzedzi/flag nie jest tu
# drukowana w calosci.
if [ -n "${MONOLYNX_SPRINT_TICK_ALLOWED_TOOLS:-}" ]; then
  TICK_ALLOWED_TOOLS="$MONOLYNX_SPRINT_TICK_ALLOWED_TOOLS"
  ALLOWED_TOOLS_LABEL="custom"
else
  TICK_ALLOWED_TOOLS="Bash(claude:*),Bash(MONOLYNX_SPRINT_RUN=1 claude:*),Bash(python3:*),Read(~/.claude/plugins/**),mcp__monolynx,mcp__claude_ai_Monolynx,mcp__plugin_monolynx_monolynx"
  ALLOWED_TOOLS_LABEL="default"
fi

STATE_DIR="$GIT_DIR/monolynx"
mkdir -p "$STATE_DIR"
LOG="$STATE_DIR/sprint-run.log"
PID_FILE="$STATE_DIR/sprint-run.pid"

# Jedna petla per repo: druga instancja zdublowalaby sesje na tych samych ticketach.
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "sprint_run: petla juz dziala (pid $(cat "$PID_FILE")) - $LOG" >&2
  exit 75
fi
echo $$ > "$PID_FILE"
cleanup() { rm -f "$PID_FILE"; }
trap 'cleanup; echo "sprint_run: przerwano; sesje w tle zyja dalej - sprawdz: claude agents" >&2; exit 130' INT TERM
trap cleanup EXIT

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

run_agent() {
  # $1 = nazwa sesji bez modelu, $2 = prompt w postaci "/monolynx:<skill> ...".
  # Swiezy proces per wywolanie; wynik na stdout i do logu. Ktora binarka jest
  # wolana zalezy od RUNTIME (MON-185) - jeden algorytm dyspozytora, dwa
  # backendy sesji.
  if [ "$RUNTIME" = "codex" ]; then
    # `codex exec -m` nie przyjmuje aliasow opus/sonnet (potwierdzone,
    # sprint_run_backend.py) - TICK_MODEL NIE trafia do argumentow tego
    # wywolania, zostaje tylko w logu startu. `codex exec` nie ma flagi
    # rownowaznej --name, wiec $1 tez nie idzie do argumentow - identyfikacja
    # ticku w logu petli wystarcza (nazwa procesu Codex nie jest kontraktem
    # dyspozytora tak jak dla Claude --bg).
    #
    # Skille sa dostarczone do Codex jako `.agents/skills` (MON-182), ale pod
    # postacia `$monolynx:<skill>` (nie `/monolynx:<skill>` jak w Claude Code) -
    # zamiana prefiksu w miejscu wywolania, zeby kazdy wolajacy run_agent mogl
    # nadal pisac prompt w jednej, znanej postaci.
    codex_prompt="\$monolynx:${2#/monolynx:}"
    # `codex exec` czyta stdin, gdy nie jest terminalem ("Reading additional
    # input from stdin..." - potwierdzone przez team-lead, wystepuje nawet przy
    # `< /dev/null`, fixture tests/fixtures/codex/exec-plain.0.155.1.stderr.txt).
    # Bez przekierowania stdin tick odpalony z otwartym pipe zawiesza sie bez
    # zadnego wyjscia - `_spawn_codex` w sprint_run_backend.py juz to robi
    # (stdin=subprocess.DEVNULL), tej petli brakowalo tego samego.
    codex exec \
      --sandbox "${MONOLYNX_SPRINT_CODEX_SANDBOX:-workspace-write}" \
      -C "$PROJECT_DIR" \
      --skip-git-repo-check \
      "$codex_prompt" < /dev/null 2>&1 | tee -a "$LOG"
  else
    # Model dopisywany tu, w jednym miejscu: kazda nazwa sesji konczy sie modelem, na ktorym dziala.
    # --allowedTools daje tickowi uprawnienia bez recznych regul w settings projektu (MON-190);
    # --permission-prompts none odrzuca natychmiast kazde pytanie o zgode, ktore i tak nie ma kto odebrac w -p.
    claude -p "$2" \
      --name "$1-$TICK_MODEL" \
      --model "$TICK_MODEL" \
      --permission-mode acceptEdits \
      --allowedTools "$TICK_ALLOWED_TOOLS" \
      --permission-prompts none \
      --max-turns "${MONOLYNX_SPRINT_RUN_MAX_TURNS:-80}" \
      --output-format text 2>&1 | tee -a "$LOG"
  fi
}

log "start: slug=$SLUG slots=$SLOTS max_waiting=$MAX_WAITING interval=$INTERVAL close=$CLOSE once=$ONCE runtime=$RUNTIME tick_model=$TICK_MODEL work_model=$MONOLYNX_SPRINT_MODEL dir=$PROJECT_DIR allowed_tools=$ALLOWED_TOOLS_LABEL"

tick=0
errors=0
while :; do
  tick=$((tick + 1))
  log "tick $tick"
  out="$(run_agent "$SLUG-sprint-run" "/monolynx:sprint-run $SLOTS")"
  status_line="$(printf '%s\n' "$out" | grep -E '^SPRINT-RUN: remaining=[0-9]+ running=[0-9]+ waiting=[0-9]+' | tail -1)"

  if [ -z "$status_line" ]; then
    errors=$((errors + 1))
    log "brak linii statusu SPRINT-RUN (blad $errors/3)"
    [ "$errors" -ge 3 ] && { log "3 ticki bez statusu - koncze. Log: $LOG"; exit 2; }
  else
    errors=0
    remaining="$(sed -E 's/.*remaining=([0-9]+).*/\1/' <<<"$status_line")"
    running="$(sed -E 's/.*running=([0-9]+).*/\1/' <<<"$status_line")"
    waiting="$(sed -E 's/.*waiting=([0-9]+).*/\1/' <<<"$status_line")"
    log "stan: $status_line"
    [ "$waiting" -gt 0 ] && log "UWAGA: $waiting sesji czeka na czlowieka - claude agents / claude attach <id>"

    [ "$remaining" -gt 0 ] && [ "$running" -eq 0 ] && log "UWAGA: nic nie biegnie, zostalo $remaining ticketow czekajacych na czlowieka (needs-local, niezamkniete blokery, limit waiting) - petla tyka dalej"

    if [ "$remaining" -eq 0 ] && [ "$running" -eq 0 ]; then
      if [ "$waiting" -gt 0 ]; then
        log "zostaly tylko sesje czekajace na czlowieka - koncze petle, dokoncz je recznie"
        exit 4
      fi
      log "sprint wykonany"
      if [ "$CLOSE" -eq 1 ]; then
        log "zamykanie: sprint-end"
        run_agent "$SLUG-sprint-end" "/monolynx:sprint-end" >/dev/null
        log "retro"
        run_agent "$SLUG-retro" "/monolynx:retro; zapisz wszystkie kandydatury" >/dev/null
      else
        log "nastepny krok: przejrzyj MR-y, zmerguj, potem /monolynx:sprint-end i /monolynx:retro (albo uruchom ponownie z --close)"
      fi
      exit 0
    fi
  fi

  [ "$ONCE" -eq 1 ] && exit 0
  [ "$tick" -ge "$MAX_TICKS" ] && { log "osiagnieto max-ticks=$MAX_TICKS - koncze"; exit 3; }
  sleep "$SLEEP_SECONDS"
done
