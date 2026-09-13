#!/usr/bin/env bash
# sprint_run.sh - zewnetrzna petla dyspozytora sprintu Monolynx.
#
# Uruchamia co INTERVAL jeden tick `/monolynx:sprint-run` w SWIEZYM procesie
# `claude -p`, az sprint sie wyczerpie. Sam nie robi zadnej pracy nad ticketami:
# tick dyspozytora startuje nazwane sesje w tle (`claude --bg --name <slug>-<KEY>`),
# zbiera zakonczone i raportuje. Dziala tak samo lokalnie, w tmux na VPS i z crona.
#
# Uzycie:
#   plugin/scripts/sprint_run.sh [--interval 15m] [--slots 2] [--max-ticks 96] [--close] [--once]
#
#   --interval  odstep miedzy tickami (15m, 900, 1h); domyslnie 15m
#   --slots     rownolegle sesje work; domyslnie $MONOLYNX_SPRINT_PARALLEL albo 2
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
# Tryb -p nie odpowie na zadne pytanie, dlatego skrypt wymusza flagi AUTO*
# i MONOLYNX_SPRINT_RUN=1 (MON-165). Kazda z nich mozna nadpisac w srodowisku.

set -u

INTERVAL="${MONOLYNX_SPRINT_RUN_INTERVAL:-15m}"
SLOTS="${MONOLYNX_SPRINT_PARALLEL:-2}"
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
    -h|--help) sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

command -v claude >/dev/null 2>&1 || { echo "sprint_run: brak 'claude' w PATH" >&2; exit 69; }
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

run_claude() {
  # $1 = nazwa sesji, $2 = prompt. Swiezy proces per wywolanie; wynik na stdout i do logu.
  claude -p "$2" \
    --name "$1" \
    --permission-mode acceptEdits \
    --max-turns "${MONOLYNX_SPRINT_RUN_MAX_TURNS:-80}" \
    --output-format text 2>&1 | tee -a "$LOG"
}

log "start: slug=$SLUG slots=$SLOTS interval=$INTERVAL close=$CLOSE once=$ONCE dir=$PROJECT_DIR"

tick=0
errors=0
while :; do
  tick=$((tick + 1))
  log "tick $tick"
  out="$(run_claude "$SLUG-sprint-run" "/monolynx:sprint-run $SLOTS")"
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

    if [ "$remaining" -eq 0 ] && [ "$running" -eq 0 ]; then
      if [ "$waiting" -gt 0 ]; then
        log "zostaly tylko sesje czekajace na czlowieka - koncze petle, dokoncz je recznie"
        exit 4
      fi
      log "sprint wykonany"
      if [ "$CLOSE" -eq 1 ]; then
        log "zamykanie: sprint-end"
        run_claude "$SLUG-sprint-end" "/monolynx:sprint-end" >/dev/null
        log "retro"
        run_claude "$SLUG-retro" "/monolynx:retro; zapisz wszystkie kandydatury" >/dev/null
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
