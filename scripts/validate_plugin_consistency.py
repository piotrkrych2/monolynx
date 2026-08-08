#!/usr/bin/env python3
"""Walidator spojnosci pluginu Monolynx (skille, agenci, README, plugin.json).

Sprawdza cztery rzeczy niezalezne od siebie:
- czy referencje "KROK X" / "FAZA X" / "sekcja N pipeline.md" w tresci skilli
  wskazuja istniejacy naglowek (martwe odnosniki po edycji skilli),
- czy w plikach nie ma em-dash (U+2014) ani en-dash (U+2013) - konwencja
  projektu wymaga zwyklego myslnika,
- czy nazwy komend uzywaja formatu `/monolynx:<nazwa>`, a nie starego
  `/monolynx-<nazwa>` (gole "monolynx-work" bez wiodacego slasha, w kontekscie
  install_monolynx_skills, jest poprawna nazwa skilla do recznej instalacji -
  nie jest to komenda i nie jest zglaszane),
- czy `version` w plugin.json zgadza sie z pierwszym wpisem `### X.Y.Z`
  w sekcji "## Changelog" pliku README.md pluginu.

Uzycie (bez argumentow - domyslnie skanuje plugin/ w korzeniu repo):

    python3 scripts/validate_plugin_consistency.py

Uzycie z jawnymi sciezkami:

    python3 scripts/validate_plugin_consistency.py \\
        --plugin-json plugin/.claude-plugin/plugin.json \\
        --readme plugin/README.md \\
        --skills-dir plugin

Kod wyjscia: 0 (OK), 1 (znaleziono naruszenia), 2 (blad konfiguracji - brakujacy
plik/katalog albo README.md bez sekcji "## Changelog").
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLUGIN_JSON = ROOT / "plugin" / ".claude-plugin" / "plugin.json"
DEFAULT_README = ROOT / "plugin" / "README.md"
DEFAULT_SKILLS_DIR = ROOT / "plugin"

_HEADING_KROK_FAZA_RE = re.compile(r"^#{1,6}\s*(KROK|FAZA)\s+(\d+)", re.IGNORECASE | re.MULTILINE)
_REF_KROK_FAZA_RE = re.compile(r"\b(KROK|FAZA)\s+(\d+)", re.IGNORECASE)
_HEADING_SEKCJA_RE = re.compile(r"^#{1,6}\s*(\d+)\.", re.MULTILINE)
_REF_SEKCJA_RE = re.compile(r"\bsekcja\s+(\d+)\s+`?pipeline\.md`?", re.IGNORECASE)
_COMMAND_NAME_RE = re.compile(r"/monolynx-[A-Za-z0-9-]+")
_CHANGELOG_HEADING_RE = re.compile(r"^##\s*Changelog\s*$", re.MULTILINE)
_CHANGELOG_ENTRY_RE = re.compile(r"^###\s*(\S+)", re.MULTILINE)


class ConfigError(Exception):
    """Blad konfiguracji: brakujaca sekcja Changelog albo pole version."""


def check_step_refs(files: dict[str, str]) -> list[str]:
    """Znajdz martwe referencje KROK X / FAZA X / sekcja N pipeline.md.

    Naglowek moze byc w dowolnym pliku ze zbioru `files` (referencje krzyzowe
    miedzy skillami sa normalne), za wyjatkiem "sekcja N pipeline.md" - tu
    naglowek musi byc w pliku, ktorego nazwa konczy sie na "pipeline.md".
    """
    existing_krok_faza: set[tuple[str, str]] = set()
    for content in files.values():
        for match in _HEADING_KROK_FAZA_RE.finditer(content):
            existing_krok_faza.add((match.group(1).upper(), match.group(2)))

    existing_sekcja: set[str] = set()
    for name, content in files.items():
        if name.endswith("pipeline.md"):
            for match in _HEADING_SEKCJA_RE.finditer(content):
                existing_sekcja.add(match.group(1))

    violations: list[str] = []
    for name in sorted(files):
        content = files[name]

        for match in _REF_KROK_FAZA_RE.finditer(content):
            key = (match.group(1).upper(), match.group(2))
            if key not in existing_krok_faza:
                violations.append(f"{name}: martwa referencja '{match.group(0)}' - brak takiego naglowka w skillach")

        for match in _REF_SEKCJA_RE.finditer(content):
            if match.group(1) not in existing_sekcja:
                violations.append(
                    f"{name}: martwa referencja 'sekcja {match.group(1)} pipeline.md' "
                    "- brak takiego naglowka w pipeline.md"
                )

    return violations


def check_em_dash(files: dict[str, str]) -> list[str]:
    """Zgloś kazdy plik zawierajacy em-dash (—) lub en-dash (–)."""
    violations = []
    for name in sorted(files):
        content = files[name]
        if "—" in content or "–" in content:
            violations.append(f"{name}: zawiera em-dash (—) lub en-dash (–) - uzyj zwyklego myslnika '-'")
    return violations


def check_command_names(files: dict[str, str]) -> list[str]:
    """Zgloś komendy w formacie `/monolynx-<nazwa>` (poprawnie: `/monolynx:<nazwa>`).

    Gole nazwy skilli bez wiodacego slasha (np. "monolynx-work" w opisie
    install_monolynx_skills) nie sa komendami i nie pasuja do wzorca.
    """
    violations = []
    for name in sorted(files):
        content = files[name]
        for match in _COMMAND_NAME_RE.finditer(content):
            violations.append(
                f"{name}: nieprawidlowa nazwa komendy '{match.group(0)}' - uzyj formatu /monolynx:<nazwa>"
            )
    return violations


def check_version_matches_changelog(plugin_json: str, readme: str) -> list[str]:
    """Porownaj `version` z plugin.json z pierwszym wpisem sekcji Changelog w README.

    Podnosi ConfigError gdy plugin_json jest niepoprawnym JSON-em, brakuje pola
    `version`, README nie ma sekcji '## Changelog' albo ta sekcja jest pusta -
    to sa bledy konfiguracji, nie tresciowe naruszenia.
    """
    try:
        data = json.loads(plugin_json)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"niepoprawny JSON w plugin.json: {exc}") from exc

    version = data.get("version")
    if not version:
        raise ConfigError("plugin.json nie ma pola 'version'")

    changelog_match = _CHANGELOG_HEADING_RE.search(readme)
    if changelog_match is None:
        raise ConfigError("README.md nie ma sekcji '## Changelog'")

    entry_match = _CHANGELOG_ENTRY_RE.search(readme[changelog_match.end() :])
    if entry_match is None:
        raise ConfigError("sekcja '## Changelog' w README.md nie ma zadnego wpisu '### X.Y.Z'")

    changelog_version = entry_match.group(1)
    if changelog_version != version:
        return [
            f"plugin.json: version '{version}' nie zgadza sie z pierwszym wpisem changeloga "
            f"'{changelog_version}' w README.md"
        ]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Walidator spojnosci pluginu Monolynx (referencje krokow, em-dash, "
        "nazwy komend, wersja/changelog)."
    )
    parser.add_argument(
        "--plugin-json",
        type=Path,
        default=DEFAULT_PLUGIN_JSON,
        help=f"sciezka do plugin.json (domyslnie {DEFAULT_PLUGIN_JSON})",
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=DEFAULT_README,
        help=f"sciezka do README.md, sekcja '## Changelog' (domyslnie {DEFAULT_README})",
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=DEFAULT_SKILLS_DIR,
        help=f"katalog z plikami .md do przeskanowania (domyslnie {DEFAULT_SKILLS_DIR})",
    )
    args = parser.parse_args(argv)

    if not args.plugin_json.is_file():
        print(f"BLAD: brak pliku {args.plugin_json}", file=sys.stderr)
        return 2
    if not args.readme.is_file():
        print(f"BLAD: brak pliku {args.readme}", file=sys.stderr)
        return 2
    if not args.skills_dir.is_dir():
        print(f"BLAD: brak katalogu {args.skills_dir}", file=sys.stderr)
        return 2

    plugin_json_text = args.plugin_json.read_text(encoding="utf-8")
    readme_text = args.readme.read_text(encoding="utf-8")

    files = {
        str(path.relative_to(args.skills_dir)): path.read_text(encoding="utf-8")
        for path in sorted(args.skills_dir.rglob("*.md"))
    }

    violations: list[str] = []
    violations.extend(check_step_refs(files))
    violations.extend(check_em_dash(files))
    violations.extend(check_command_names(files))

    try:
        violations.extend(check_version_matches_changelog(plugin_json_text, readme_text))
    except ConfigError as exc:
        print(f"BLAD KONFIGURACJI: {exc}", file=sys.stderr)
        return 2

    if violations:
        for violation in violations:
            print(violation)
        return 1

    print("OK: plugin spojny.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
