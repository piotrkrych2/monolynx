#!/usr/bin/env python3
"""Nakladanie sie ticketow na tych samych plikach dla /monolynx:ticket-review.

Tickety, ktore dotykaja tych samych plikow i nie maja miedzy soba relacji
blokera, nie powinny isc rownolegle w sprint-run (konflikty merge). Skill nie
porownuje list "Pliki dotykane" z prozy - ten skrypt liczy przeciecia
deterministycznie i proponuje `blocked_by`. Niczego nie zapisuje.

Wywolanie:

    ticket_overlap.py --input plik.json
    ticket_overlap.py --input -            (JSON ze stdin)

Wejscie (JSON):

    {"tickets": [
      {"key": "MON-1", "priority": "high", "files": ["cli/src/a.py"], "blocked_by": ["MON-7"]}
    ]}

    key         wymagany, `^[A-Z][A-Z0-9]*-[0-9]+$`, unikalny
    priority    wymagany: critical | high | medium | low
    files       lista sciezek repo-relatywnych; brak pola, null albo lista pusta
                (takze po normalizacji) daje ticket w `no_files`
    blocked_by  klucze istniejacych blokerow (opcjonalne); moga wskazywac tickety
                spoza wejscia

Sciezki sa normalizowane: strip, zdjecie backtickow, zdjecie wiodacego `./`,
`\\` -> `/`. Sciezka absolutna albo z segmentem `..` to blad wejscia.

Kolejnosc ticketow (ranga): priorytet malejaco, potem numer klucza rosnaco
(MON-9 przed MON-10), potem klucz. Wczesniejszy w rangach jest blokerem.

Wyjscie (JSON na stdout):

    proposals   [{"blocker", "blocked", "files"}] - proponowane krawedzie
    related     [{"tickets": [A, B], "files"}]    - nakladaja sie, ale relacja
                                                    juz istnieje (bezposrednio,
                                                    posrednio albo z propozycji)
    shared      [{"file", "tickets"}]             - pliki zawsze wspolne, bez propozycji
    no_files    [klucz, ...]                      - tickety bez listy plikow

Kody wyjscia: 0 OK, 2 blad tresci wejscia (zly JSON, zly ticket, brak pliku),
64 blad uzycia (brak --input, nieznany argument). Blad na stderr z prefiksem
`ticket_overlap: `, nigdy traceback.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from typing import Any

KEY_PATTERN = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+")

# Kolejnosc waznosci: mniejszy indeks = wazniejszy = bloker.
PRIORITIES = ("critical", "high", "medium", "low")

# Pliki, ktore rutynowo edytuje kazdy ticket pluginu i ktorych konflikt rozwiazuje
# sie trywialnie przy merge (wpisy changelogu pod `### Unreleased`, manifesty
# bumpowane przez mergujacego, liczniki skilli). Blokowanie na nich zserializowaloby
# caly sprint bez zysku, wiec nie generuja propozycji - sa tylko raportowane.
SHARED_FILES = frozenset(
    {
        "plugin/README.md",
        "plugin/.claude-plugin/plugin.json",
        "plugin/.codex-plugin/plugin.json",
        "plugin/skills/help/SKILL.md",
        "tests/unit/test_skill_counters.py",
        "src/monolynx/static/how-to-use-monolynx.md",
        "src/monolynx/static/agent-bootstrap.md",
        "src/monolynx/templates/dashboard/profile/mcp_guide.html",
    }
)
# Kopie skilli generuje `scripts/sync_skills.py` z plugin/skills, wiec ich zmiana
# zawsze idzie w parze z ticketem edytujacym zrodlo, nie z osobna praca. Koncowy
# `/` wymusza dopasowanie katalogu, nie napisu (`skills_extra/` nie pasuje).
SHARED_PREFIXES = ("src/monolynx/static/skills/",)


class _UsageError(Exception):
    """Blad parsowania argumentow CLI (kod wyjscia 64)."""


class _InputError(Exception):
    """Blad tresci wejscia (kod wyjscia 2)."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        # argparse wola sys.exit(2), co zlewaloby sie z kodem 2 dla bledow wejscia.
        raise _UsageError(message)


def normalize_path(raw: str) -> str:
    path = raw.strip().strip("`").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _is_shared(path: str) -> bool:
    return path in SHARED_FILES or path.startswith(SHARED_PREFIXES)


def _parse_files(raw: Any, where: str) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise _InputError(f"{where}: 'files' nie jest lista")
    files: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, str):
            raise _InputError(f"{where}.files[{i}]: sciezka nie jest napisem")
        path = normalize_path(item)
        if not path:
            continue
        if path.startswith("/") or ".." in path.split("/"):
            raise _InputError(f"{where}.files[{i}]: sciezka musi byc repo-relatywna: {item!r}")
        files.add(path)
    return frozenset(files)


def _parse_blocked_by(raw: Any, where: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _InputError(f"{where}: 'blocked_by' nie jest lista")
    for i, item in enumerate(raw):
        if not isinstance(item, str):
            raise _InputError(f"{where}.blocked_by[{i}]: klucz nie jest napisem")
    return list(raw)


def parse_tickets(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("tickets"), list):
        raise _InputError("wejscie musi byc obiektem z lista 'tickets'")
    tickets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, item in enumerate(data["tickets"]):
        where = f"tickets[{i}]"
        if not isinstance(item, dict):
            raise _InputError(f"{where}: element nie jest obiektem")
        key = item.get("key")
        if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
            raise _InputError(f"{where}: brak poprawnego pola 'key' (np. MON-12)")
        if key in seen:
            raise _InputError(f"{where}: duplikat klucza {key}")
        seen.add(key)
        priority = item.get("priority")
        if priority not in PRIORITIES:
            raise _InputError(f"{where}: 'priority' musi byc jednym z {', '.join(PRIORITIES)}")
        tickets.append(
            {
                "key": key,
                "priority": priority,
                "files": _parse_files(item.get("files"), where),
                "blocked_by": _parse_blocked_by(item.get("blocked_by"), where),
            }
        )
    return tickets


def _rank(ticket: dict[str, Any]) -> tuple[int, int, str]:
    key: str = ticket["key"]
    return (PRIORITIES.index(ticket["priority"]), int(key.rsplit("-", 1)[1]), key)


def _reachable(graph: dict[str, set[str]], start: str, goal: str) -> bool:
    stack = [start]
    visited = {start}
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        for nxt in graph[node] - visited:
            visited.add(nxt)
            stack.append(nxt)
    return False


def analyze(tickets: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(tickets, key=_rank)
    index = {t["key"]: i for i, t in enumerate(ordered)}

    graph: dict[str, set[str]] = defaultdict(set)
    for t in ordered:
        for blocker in t["blocked_by"]:
            graph[blocker].add(t["key"])

    with_files = [t for t in ordered if t["files"]]
    no_files = [t["key"] for t in ordered if not t["files"]]

    shared_by_file: dict[str, list[str]] = defaultdict(list)
    for t in with_files:
        for path in t["files"]:
            if _is_shared(path):
                shared_by_file[path].append(t["key"])
    shared = [{"file": path, "tickets": keys} for path, keys in sorted(shared_by_file.items()) if len(keys) >= 2]

    overlaps: list[tuple[str, str, list[str]]] = []
    for i, a in enumerate(with_files):
        for b in with_files[i + 1 :]:
            common = sorted(p for p in a["files"] & b["files"] if not _is_shared(p))
            if common:
                overlaps.append((a["key"], b["key"], common))
    # Dla kazdego blokowanego najpierw najblizszy poprzednik: A->B->C zamiast kliki
    # A->B, A->C, B->C. Dalsza para jest wtedy juz polaczona sciezka i laduje w `related`.
    overlaps.sort(key=lambda o: (index[o[1]], -index[o[0]]))

    proposals: list[dict[str, Any]] = []
    related: list[dict[str, Any]] = []
    for a, b, common in overlaps:
        if _reachable(graph, a, b) or _reachable(graph, b, a):
            related.append({"tickets": [a, b], "files": common})
        else:
            proposals.append({"blocker": a, "blocked": b, "files": common})
            graph[a].add(b)
    related.sort(key=lambda r: (index[r["tickets"][0]], index[r["tickets"][1]]))

    return {"proposals": proposals, "related": related, "shared": shared, "no_files": no_files}


def _read_input(source: str) -> Any:
    try:
        if source == "-":
            raw = sys.stdin.read()
        else:
            with open(source, encoding="utf-8") as fh:
                raw = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise _InputError(f"nie mozna odczytac wejscia {source}: {exc}") from exc
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise _InputError(f"wejscie nie jest poprawnym JSON: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(prog="ticket_overlap.py", description="Nakladanie sie ticketow na plikach.")
    parser.add_argument("--input", required=True, help="plik JSON albo '-' dla stdin")
    try:
        args = parser.parse_args(argv)
        result = analyze(parse_tickets(_read_input(args.input)))
    except _UsageError as exc:
        print(f"ticket_overlap: {exc}", file=sys.stderr)
        return 64
    except _InputError as exc:
        print(f"ticket_overlap: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
