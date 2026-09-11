#!/usr/bin/env python3
"""UserPromptSubmit guard - pierwszy goly imperatyw sesji dostaje kontrakt z /monolynx:brief.

Raport MEGA (2026-09-09): wiekszosc epizodow otwiera goly czasownik rozkazujacy
bez stanu koncowego ("napraw build"), a sterowanie idzie przez przerywanie.
Skill /monolynx:brief ustala kontrakt (gotowe / nie ruszac / stop przed), ale
trzeba o nim pamietac. Ten hook zamienia pamietanie w maszynerie.

Tryby (MONOLYNX_BRIEF_GUARD):
- auto (domyslny): prompt przechodzi, a do kontekstu trafia tresc skilla brief
  z podstawionym promptem - agent zaczyna od kontraktu, tak jakby uzytkownik
  wpisal /monolynx:brief <prompt>. Zero przepisywania.
- block: prompt odrzucony (exit 2) z komunikatem "uruchom /monolynx:brief".
  Uzytkownik przepisuje sam - jedyny tryb, ktory zostawia kontrakt w JEGO
  tekscie (to mierzy skan MEGA).
- off / false / 0: hook wylaczony.

Wspolne:
- Ocenia TYLKO pierwszy prompt sesji (marker per session_id; /clear daje nowa
  sesje). Drugi prompt przechodzi - jedno przypomnienie, nie mur.
- Przepuszcza bez reakcji: slash-komendy, prompty z kluczem ticketu (ticket
  niesie kryteria), prompty > 200 znakow, pytania (zakonczone "?"), prompty bez
  czasownika rozkazujacego w pierwszych 60 znakach, prompty ze slowami kontraktu.
"""

import json
import os
import re
import sys
from pathlib import Path

VERBS = (
    "napraw|zrob|zrób|zrobmy|zróbmy|dodaj|dopisz|sprawdz|sprawdź|popraw|zaktualizuj"
    "|usun|usuń|wywal|zmien|zmień|podmien|podmień|wdroz|wdróż|uruchom|odpal"
    "|postaw|przenies|przenieś|zrefaktoruj|przepisz|wyslij|wyślij|przetestuj|zainstaluj"
    "|skonfiguruj|podlacz|podłącz|napisz|stworz|stwórz|utworz|utwórz|wygeneruj|zbadaj"
    "|znajdz|znajdź|przejrzyj|ogarnij|przygotuj|wrzuc|wrzuć|wyczysc|wyczyść|wylacz|wyłącz"
    "|wlacz|włącz|zaimplementuj|zoptymalizuj|zabezpiecz|zmigruj|zsynchronizuj|opublikuj"
    "|fix|add|check|update|remove|delete|change|deploy|run|refactor|write|create|implement"
    "|investigate|install|configure|build|make|test|debug|migrate|optimize|clean|rewrite|setup"
)
# Czasownik w pierwszych 60 znakach, jako osobne slowo: "W pliku X dodaj endpoint"
# ma byc zlapane tak samo jak "dodaj endpoint".
IMPERATIVE_HEAD = re.compile(r"^.{0,60}?(?<![\w-])(" + VERBS + r")(?![\w-])", re.I | re.S)
CONTRACT_WORDS = re.compile(
    r"gotowe|kryteri|nie ruszaj|nie ruszac|nie ruszać|stop przed|pytaj przed|zatrzymaj sie przed",
    re.I,
)
TICKET_KEY = re.compile(r"\b[A-Z]{2,}-\d+\b")
MAX_LEN = 200

MODE = os.environ.get("MONOLYNX_BRIEF_GUARD", "auto").strip().lower() or "auto"
if MODE in ("off", "false", "0"):
    sys.exit(0)

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

session_id = str(data.get("session_id") or "").strip()
prompt = str(data.get("prompt") or "").strip()
if not session_id or not prompt:
    sys.exit(0)

# Marker tworzymy PRZED ocena: drugi prompt w sesji (takze powtorka tego samego
# tekstu po odrzuceniu w trybie block) ma przejsc bez reakcji.
marker = Path.home() / ".cache" / "monolynx" / "brief-guard" / re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
try:
    if marker.exists():
        sys.exit(0)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
except OSError:
    sys.exit(0)

if prompt.startswith("/") or len(prompt) > MAX_LEN or prompt.endswith("?"):
    sys.exit(0)
if TICKET_KEY.search(prompt) or CONTRACT_WORDS.search(prompt):
    sys.exit(0)
if not IMPERATIVE_HEAD.match(prompt):
    sys.exit(0)

if MODE == "block":
    print(
        "Goły imperatyw bez kontraktu (raport MEGA: T01/T18). Uruchom:\n"
        f"  /monolynx:brief {prompt}\n"
        "Skill ustali w jednym pytaniu: gotowe = [stan], nie ruszać = [co], "
        "pytaj przed = [co], i od razu zacznie pracę. Albo dopisz te trzy rzeczy "
        "sam i wyślij ponownie - drugi prompt w tej sesji przechodzi bez pytania. "
        "Wyłączenie: MONOLYNX_BRIEF_GUARD=off.",
        file=sys.stderr,
    )
    sys.exit(2)

# Tryb auto: wstrzykujemy tresc skilla brief, nie tylko polecenie "uzyj Skill" -
# nie zalezy wtedy od tego, czy model wybierze narzedzie Skill.
skill_path = Path(__file__).resolve().parent.parent / "skills" / "brief" / "SKILL.md"
try:
    skill_text = skill_path.read_text(encoding="utf-8")
    skill_text = re.sub(r"^---\n.*?\n---\n", "", skill_text, count=1, flags=re.S)
except OSError:
    skill_text = "Uruchom Skill(skill=\"monolynx:brief\") z tym promptem jako argumentem i postępuj wg niego."

context = (
    "[hook brief_guard] Pierwszy prompt sesji to goły imperatyw bez kontraktu "
    "(raport MEGA: T01/T18). ZANIM cokolwiek zrobisz, wykonaj poniższy skill "
    "/monolynx:brief dokładnie tak, jakby użytkownik wpisał "
    f"`/monolynx:brief {prompt}`. Treść $ARGUMENTS = „{prompt}”. "
    "Po akceptacji kontraktu (ENTER) pracuj dalej w tej samej turze.\n\n"
    + skill_text
)
print(
    json.dumps(
        {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}},
        ensure_ascii=False,
    )
)
sys.exit(0)
