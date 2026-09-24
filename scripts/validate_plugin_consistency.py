#!/usr/bin/env python3
"""Walidator spojnosci pluginu Monolynx (skille, agenci, README, plugin.json).

Sprawdza nastepujace rzeczy niezaleznie od siebie:
- czy referencje "KROK X" / "FAZA X" / "sekcja N pipeline.md" w tresci skilli
  wskazuja istniejacy naglowek (martwe odnosniki po edycji skilli),
- czy w plikach nie ma em-dash (U+2014) ani en-dash (U+2013) - konwencja
  projektu wymaga zwyklego myslnika,
- czy nazwy komend uzywaja formatu `/monolynx:<nazwa>`, a nie starego
  `/monolynx-<nazwa>` (gole "monolynx-work" bez wiodacego slasha, w kontekscie
  install_monolynx_skills, jest poprawna nazwa skilla do recznej instalacji -
  nie jest to komenda i nie jest zglaszane),
- czy `version` w plugin.json zgadza sie z pierwszym wpisem `### X.Y.Z`
  w sekcji "## Changelog" pliku README.md pluginu; ticket dopisuje swoj wpis
  pod naglowkiem "### Unreleased" (MON-177) - na branchu roboczym to OK i
  wersja jest porownywana z pierwszym wpisem numerycznym PO Unreleased, na
  wydaniu (main albo `--release`) "### Unreleased" jako pierwszy wpis jest
  bledem, bo wydanie wymaga numeru nadanego przez mergujacego,
- czy wpisy pod "### Unreleased" maja klucz ticketu (`KLUCZ-123: opis`) -
  pozwala jednoznacznie scalic wpisy z wielu MR-ow przy nadawaniu numeru,
- czy kazdy skill z katalogu plugin/skills/<nazwa>/ jest wymieniony jako
  `/monolynx:<nazwa>` na kazdej stronie opisujacej skille (README pluginu,
  /monolynx:help, mcp-guide po zalogowaniu, agent-bootstrap.md,
  how-to-use-monolynx.md, teksty landingu w main.py) - nowy skill bez wpisu
  na ktorejs z nich to naruszenie,
- czy tracked `.claude/settings.json` (domyslnie ROOT/.claude/settings.json,
  jawna sciezka przez `--settings-json`) niesie w "env" flagi sesji sprintu
  roboczego (MONOLYNX_SPRINT_RUN, MONOLYNX_BASE_BRANCH, MONOLYNX_MR_TARGET) -
  w trybie wydania (`--release` / CI_COMMIT_BRANCH=main) to naruszenie, bo te
  flagi opisuja stan/branch sprintu i nie maja sensu na main (MON-207); brak
  pliku pod `--settings-json` nie jest bledem.

Uzycie (bez argumentow - domyslnie skanuje plugin/ w korzeniu repo):

    python3 scripts/validate_plugin_consistency.py

Uzycie z jawnymi sciezkami:

    python3 scripts/validate_plugin_consistency.py \\
        --plugin-json plugin/.claude-plugin/plugin.json \\
        --readme plugin/README.md \\
        --skills-dir plugin \\
        --skills-root plugin/skills \\
        --page plugin/README.md --page src/monolynx/templates/dashboard/profile/mcp_guide.html

Przy niestandardowym --skills-dir bez jawnych --page sprawdzenie pokrycia stron
jest pomijane (testy walidatora pracuja na katalogach tymczasowych, nie na repo).

Flaga `--release` (albo `CI_COMMIT_BRANCH=main` w srodowisku) wlacza tryb
wydania: "### Unreleased" jako pierwszy wpis changeloga jest wtedy bledem
zamiast dozwolonego stanu miedzy mergami.

Kod wyjscia: 0 (OK), 1 (znaleziono naruszenia), 2 (blad konfiguracji - brakujacy
plik/katalog albo README.md bez sekcji "## Changelog").
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLUGIN_JSON = ROOT / "plugin" / ".claude-plugin" / "plugin.json"
DEFAULT_README = ROOT / "plugin" / "README.md"
DEFAULT_SKILLS_DIR = ROOT / "plugin"
DEFAULT_SKILLS_ROOT = ROOT / "plugin" / "skills"
DEFAULT_SETTINGS_JSON = ROOT / ".claude" / "settings.json"
_SETTINGS_ENV_FORBIDDEN_KEYS = ("MONOLYNX_SPRINT_RUN", "MONOLYNX_BASE_BRANCH", "MONOLYNX_MR_TARGET")
DEFAULT_PAGES = (
    ROOT / "plugin" / "README.md",
    ROOT / "plugin" / "skills" / "help" / "SKILL.md",
    ROOT / "src" / "monolynx" / "templates" / "dashboard" / "profile" / "mcp_guide.html",
    ROOT / "src" / "monolynx" / "static" / "agent-bootstrap.md",
    ROOT / "src" / "monolynx" / "static" / "how-to-use-monolynx.md",
    ROOT / "src" / "monolynx" / "main.py",
)

_HEADING_KROK_FAZA_RE = re.compile(r"^#{1,6}\s*(KROK|FAZA)\s+(\d+)", re.IGNORECASE | re.MULTILINE)
_REF_KROK_FAZA_RE = re.compile(r"\b(KROK|FAZA)\s+(\d+)", re.IGNORECASE)
_HEADING_SEKCJA_RE = re.compile(r"^#{1,6}\s*(\d+)\.", re.MULTILINE)
_REF_SEKCJA_RE = re.compile(r"\bsekcja\s+(\d+)\s+`?pipeline\.md`?", re.IGNORECASE)
_COMMAND_NAME_RE = re.compile(r"/monolynx-[A-Za-z0-9-]+")
_CHANGELOG_HEADING_RE = re.compile(r"^##\s*Changelog\s*$", re.MULTILINE)
_CHANGELOG_ENTRY_RE = re.compile(r"^###\s*(\S+)", re.MULTILINE)
_CHANGELOG_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_UNRELEASED_HEADING_RE = re.compile(r"^###\s*Unreleased\s*$", re.MULTILINE)
_UNRELEASED_SECTION_END_RE = re.compile(r"^#{2,3}\s", re.MULTILINE)
_UNRELEASED_ENTRY_VALID_RE = re.compile(r"^- [A-Z][A-Z0-9]*-\d+: ")

_SHARED_NAME = r"[a-z0-9][a-z0-9-]*"
_SHARED_OPEN_ONLY_RE = re.compile(rf"^\s*<!--\s*shared:({_SHARED_NAME})\s*-->\s*$")
_SHARED_CLOSE_ONLY_RE = re.compile(rf"^\s*<!--\s*/shared:({_SHARED_NAME})\s*-->\s*$")
_SHARED_LINE_FORM_RE = re.compile(rf"^(.*\S.*)<!--\s*shared:({_SHARED_NAME})\s*-->\s*$")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_shared_content(text: str) -> str:
    """Collapse whitespace runs to one space - the only agreed normalization (MON-189)."""
    return _WHITESPACE_RE.sub(" ", text).strip()


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
                violations.append(f"{name}: martwa referencja 'sekcja {match.group(1)} pipeline.md' - brak takiego naglowka w pipeline.md")

    return violations


def check_em_dash(files: dict[str, str]) -> list[str]:
    """Report files containing U+2014 or U+2013 instead of a hyphen."""
    violations = []
    for name in sorted(files):
        content = files[name]
        if "\u2014" in content or "\u2013" in content:
            violations.append(f"{name}: zawiera em-dash (U+2014) lub en-dash (U+2013) - użyj zwykłego myślnika '-'")
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
            violations.append(f"{name}: nieprawidlowa nazwa komendy '{match.group(0)}' - uzyj formatu /monolynx:<nazwa>")
    return violations


def check_version_matches_changelog(plugin_json: str, readme: str, *, release: bool = False) -> list[str]:
    """Porownaj `version` z plugin.json z pierwszym wpisem sekcji Changelog w README.

    Zwykly przypadek: pierwszy wpis to numer wersji i musi byc rowny `version`.
    Ticket dopisuje swoj wpis pod naglowkiem "### Unreleased" (MON-177) -
    numer nadaje dopiero mergujacy do main:
    - release=False (branch roboczy): "### Unreleased" jako pierwszy wpis jest
      OK; `version` jest porownywany z PIERWSZYM wpisem numerycznym `### X.Y.Z`
      PO nim. Brak takiego wpisu po Unreleased to blad konfiguracji.
    - release=True (wydanie na main - patrz `--release` w main()): "### Unreleased"
      jako pierwszy wpis jest naruszeniem, bez porownania wersji - wydanie
      wymaga numeru.
    Niezaleznie od release: "### Unreleased" obecny, ale NIE jako pierwszy
    wpis, jest naruszeniem (dopisany pod zlym, juz istniejacym numerem).

    Podnosi ConfigError gdy plugin_json jest niepoprawnym JSON-em, brakuje pola
    `version`, README nie ma sekcji '## Changelog', ta sekcja jest pusta, albo
    "### Unreleased" (poza trybem release) nie ma po sobie zadnego wpisu
    numerycznego - to sa bledy konfiguracji, nie tresciowe naruszenia.
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

    entries = list(_CHANGELOG_ENTRY_RE.finditer(readme[changelog_match.end() :]))
    if not entries:
        raise ConfigError("sekcja '## Changelog' w README.md nie ma zadnego wpisu '### X.Y.Z'")

    first_entry = entries[0].group(1)
    violations: list[str] = []

    if first_entry == "Unreleased":
        if release:
            violations.append(
                "plugin.json: pierwszy wpis changeloga w README.md to 'Unreleased' - to wydanie bez numeru, "
                "zamien naglowek '### Unreleased' na numer wersji i ustaw go w plugin.json"
            )
        else:
            next_version: str | None = next(
                (entry.group(1) for entry in entries[1:] if _CHANGELOG_VERSION_RE.match(entry.group(1))),
                None,
            )
            if next_version is None:
                raise ConfigError("sekcja 'Unreleased' w README.md nie ma po sobie zadnego wpisu numerycznego '### X.Y.Z'")
            if next_version != version:
                violations.append(f"plugin.json: version '{version}' nie zgadza sie z pierwszym wpisem changeloga '{next_version}' w README.md")
    else:
        if first_entry != version:
            violations.append(f"plugin.json: version '{version}' nie zgadza sie z pierwszym wpisem changeloga '{first_entry}' w README.md")
        if any(entry.group(1) == "Unreleased" for entry in entries[1:]):
            violations.append("README.md: sekcja 'Unreleased' w changelogu nie jest pierwszym wpisem - musi byc na samej gorze")

    return violations


def check_unreleased_entries(readme: str) -> list[str]:
    """Sprawdz, czy kazdy wpis pod "### Unreleased" w README.md ma klucz ticketu.

    Brak naglowka "### Unreleased" to nie blad - sekcja jest opcjonalna (branch
    bez zadnej zmiany w changelogu). Sekcja to linie od naglowka do nastepnego
    naglowka `##`/`###` albo konca pliku. Kazdy punkt najwyzszego poziomu
    (linia zaczynajaca sie od "- " w kolumnie 0) musi pasowac do wzorca
    "KLUCZ-123: opis" (`^- [A-Z][A-Z0-9]*-\\d+: `); wciete linie (kontynuacje)
    i puste linie sa ignorowane. Sekcja bez zadnego punktu jest naruszeniem
    (pusta sekcja).

    WHY: klucz ticketu pozwala jednoznacznie scalic wpisy z wielu MR-ow, gdy
    numer wersji nadaje dopiero mergujacy do main (MON-177).
    """
    heading_match = _UNRELEASED_HEADING_RE.search(readme)
    if heading_match is None:
        return []

    lines = readme.splitlines()
    section_start = readme.count("\n", 0, heading_match.start()) + 1

    section_end = len(lines)
    for index in range(section_start, len(lines)):
        if _UNRELEASED_SECTION_END_RE.match(lines[index]):
            section_end = index
            break

    violations: list[str] = []
    has_entry = False
    for index in range(section_start, section_end):
        line = lines[index]
        if not line.strip() or line.startswith((" ", "\t")):
            continue
        if line.startswith("- "):
            has_entry = True
            if not _UNRELEASED_ENTRY_VALID_RE.match(line):
                violations.append(f"README.md:{index + 1}: wpis w sekcji Unreleased bez klucza ticketu (oczekiwano '- KLUCZ-123: opis'): {line}")

    if not has_entry:
        violations.append("README.md: sekcja 'Unreleased' jest pusta - dopisz co najmniej jeden wpis z kluczem ticketu")

    return violations


def check_settings_env(settings_json: str, *, release: bool = False) -> list[str]:
    """Sprawdz, czy `.claude/settings.json` niesie flagi sesji sprintu roboczego do main.

    MONOLYNX_SPRINT_RUN, MONOLYNX_BASE_BRANCH i MONOLYNX_MR_TARGET opisuja stan
    albo branch biezacego sprintu (dyspozytor sesji w tle) - nie maja sensu
    poza tym sprintem i nie moga trafic do main (MONOLYNX_SPRINT_RUN=1 na main
    zmienia zachowanie bash_guard.py poza sesja sprintu - "ask" staje sie
    "deny" wszedzie, patrz plugin/hooks/bash_guard.py).

    release=False (branch roboczy sprintu): te flagi sa legalne, zawsze [].
    release=True (wydanie/main): kazdy z trzech kluczy obecny w "env" to jedno
    naruszenie z nazwa klucza. Brak "env" albo brak zakazanych kluczy -> [].
    Niepoprawny JSON w trybie release -> jedno naruszenie (nie wyjatek, w
    odroznieniu od ConfigError w check_version_matches_changelog - brak env
    jest tresciowym naruszeniem, nie bledem konfiguracji walidatora).
    """
    if not release:
        return []

    try:
        data = json.loads(settings_json)
    except json.JSONDecodeError:
        return ["settings.json: niepoprawny JSON - nie mozna sprawdzic sekcji 'env'"]

    env = data.get("env")
    if not isinstance(env, dict):
        return []

    violations: list[str] = []
    for key in _SETTINGS_ENV_FORBIDDEN_KEYS:
        if key in env:
            violations.append(
                f"settings.json: klucz '{key}' w sekcji 'env' opisuje stan/branch sprintu roboczego "
                "i nie moze trafic do main - usun go przed wydaniem"
            )
    return violations


def check_skill_coverage(skill_names: list[str], pages: dict[str, str]) -> list[str]:
    """Kazdy skill musi wystapic jako `/monolynx:<nazwa>` na kazdej stronie ze skillami.

    Dopasowanie po pelnej nazwie: `/monolynx:work` nie zalicza `work-simple`
    i odwrotnie. Zapis bez slasha (`monolynx:work`, uzywany w dokumentacji
    dla klientow czatowych) tez sie liczy. Strony to dowolny tekst (markdown,
    HTML, Python z tekstami landingu) - liczy sie sama obecnosc komendy.
    """
    violations = []
    for name in sorted(skill_names):
        pattern = re.compile(r"/?monolynx:" + re.escape(name) + r"(?![A-Za-z0-9-])")
        for page in sorted(pages):
            if not pattern.search(pages[page]):
                violations.append(f"{page}: brak skilla '/monolynx:{name}' - kazdy skill z plugin/skills/ ma tu byc opisany")
    return violations


def check_runtime_contract(files: dict[str, str]) -> list[str]:
    """Check portable entrypoints and skill-local Markdown resources."""
    violations = []
    sections = ("Projekt", "Narzędzia", "Skille i argumenty", "Pytania i zgody", "Uprawnienia", "Procesy")
    start, end = "<!-- runtime:claude-code -->", "<!-- /runtime:claude-code -->"
    client_call = re.compile(r"\b(?:ToolSearch|Skill|AskUserQuestion)\s*\(")
    required_client = re.compile(
        r"(?:weź|użyj|wywołaj|uruchom|czytaj|odczytaj|wykonaj|sprawdź|wymagane|obowiązkowo).*"
        r"(?:\$ARGUMENTS|\$\{?CLAUDE_PLUGIN_ROOT|\bToolSearch\b|\bAskUserQuestion\b)",
        re.IGNORECASE,
    )
    link = re.compile(r"\[[^\]]*\]\(([^\s)]+\.md)(?:#[^)]*)?\)|`(references/[^`\s]+\.md)`")
    for name in sorted(files):
        if Path(name).name != "SKILL.md":
            continue
        directory = Path(name).parent
        runtime = (directory / "references/runtime.md").as_posix()
        content = files[name]
        if "references/runtime.md" not in content or runtime not in files:
            violations.append(f"{name}: brak lokalnego kontraktu references/runtime.md lub odnośnika")
        elif any(f"## {section}\n" not in files[runtime] for section in sections):
            violations.append(f"{runtime}: niekompletny kontrakt runtime")
        elif "references/runtime.md" in files and files[runtime] != files["references/runtime.md"]:
            violations.append(f"{runtime}: kontrakt różni się od źródła kanonicznego")
        for resource_name in sorted(files):
            if not Path(resource_name).is_relative_to(directory) or not resource_name.endswith(".md"):
                continue
            resource = files[resource_name]
            if resource_name != runtime:
                depth = 0
                fenced = False
                for line_number, line in enumerate(resource.splitlines(), 1):
                    if start in line:
                        depth += 1
                        if depth != 1:
                            violations.append(f"{resource_name}:{line_number}: zagnieżdżony wariant runtime")
                        continue
                    if end in line:
                        depth -= 1
                        if depth != 0:
                            violations.append(f"{resource_name}:{line_number}: błędny koniec wariantu runtime")
                        continue
                    if depth:
                        continue
                    if line.lstrip().startswith(("```", "~~~")):
                        fenced = not fenced
                    negated = re.search(r"\bnie\s+(?:używaj|wywołuj|uruchamiaj|wymagaj|zakładaj)", line, re.IGNORECASE)
                    if not negated and (
                        client_call.search(line)
                        or required_client.search(line)
                        or (fenced and re.search(r"\$ARGUMENTS|\$\{?CLAUDE_PLUGIN_ROOT", line))
                    ):
                        violations.append(f"{resource_name}:{line_number}: bezwarunkowa instrukcja Claude Code poza wariantem runtime")
                if depth:
                    violations.append(f"{resource_name}: niezamknięty wariant runtime")
            for match in link.finditer(resource):
                target = match.group(1) or match.group(2)
                if "://" in target or "/" not in target:
                    continue
                path = Path(target)
                if path.is_absolute() or ".." in path.parts:
                    violations.append(f"{resource_name}: referencja poza katalogiem skilla: {target}")
                elif (directory / path).as_posix() not in files:
                    violations.append(f"{resource_name}: brak lokalnej referencji {target}")
    return violations


def check_shared_blocks(files: dict[str, str]) -> list[str]:
    """Sprawdz spojnosc fragmentow oznaczonych `shared:<nazwa>` miedzy plikami (MON-189).

    Dwie formy znacznika: blokowa (`<!-- shared:x -->` / `<!-- /shared:x -->`,
    kazdy w osobnej linii - tresc to linie pomiedzy nimi) i liniowa
    (`<!-- shared:x -->` na koncu linii z trescia - potrzebna w wierszach
    tabel GFM, gdzie samodzielna linia komentarza HTML konczy tabele).
    Normalizacja przed porownaniem to wylacznie zwiniecie bialych znakow do
    pojedynczej spacji + strip - zadnego fuzzy matchingu, bo szersza
    normalizacja przepuscilaby rozjazd znaczeniowy.

    Znaczniki `runtime:claude-code` nie sa `shared:*` i nie licza sie do
    zagniezdzenia - przechodza jako zwykla tresc bloku.
    """
    violations: list[str] = []
    occurrences: dict[str, list[tuple[str, str]]] = {}

    for name in sorted(files):
        content = files[name]
        stack_names: list[str] = []
        stack_starts: list[int] = []
        stack_lines: list[list[str]] = []
        seen_in_file: set[str] = set()

        for line_number, line in enumerate(content.splitlines(), start=1):
            open_match = _SHARED_OPEN_ONLY_RE.match(line)
            if open_match:
                block_name = open_match.group(1)
                if stack_names:
                    violations.append(f"{name}:{line_number}: zagnieżdżony blok 'shared:{block_name}' wewnątrz otwartego 'shared:{stack_names[-1]}'")
                stack_names.append(block_name)
                stack_starts.append(line_number)
                stack_lines.append([])
                continue

            close_match = _SHARED_CLOSE_ONLY_RE.match(line)
            if close_match:
                if not stack_names:
                    violations.append(f"{name}:{line_number}: znacznik zamykający 'shared:{close_match.group(1)}' bez odpowiadającego otwierającego")
                    continue
                block_name = stack_names.pop()
                stack_starts.pop()
                block_content = "\n".join(stack_lines.pop())
                if block_name in seen_in_file:
                    violations.append(f"{name}: nazwa 'shared:{block_name}' występuje dwukrotnie w tym samym pliku")
                seen_in_file.add(block_name)
                occurrences.setdefault(block_name, []).append((name, _normalize_shared_content(block_content)))
                continue

            if not stack_names:
                line_match = _SHARED_LINE_FORM_RE.match(line)
                if line_match:
                    block_name = line_match.group(2)
                    if block_name in seen_in_file:
                        violations.append(f"{name}: nazwa 'shared:{block_name}' występuje dwukrotnie w tym samym pliku")
                    seen_in_file.add(block_name)
                    occurrences.setdefault(block_name, []).append((name, _normalize_shared_content(line_match.group(1))))
                    continue

            for buffer in stack_lines:
                buffer.append(line)

        for block_name, start_line in zip(stack_names, stack_starts):
            violations.append(f"{name}:{start_line}: niezamknięty znacznik 'shared:{block_name}' - brak pary zamykającej do końca pliku")

    for block_name in sorted(occurrences):
        occurrence_list = occurrences[block_name]
        present_in = {file_name for file_name, _ in occurrence_list}
        if len(present_in) < 2:
            only_file = next(iter(present_in))
            violations.append(f"{only_file}: fragment 'shared:{block_name}' występuje tylko w tym pliku - brak pary w drugim pliku")
            continue
        normalized_variants = {norm for _, norm in occurrence_list}
        if len(normalized_variants) > 1:
            violations.append(f"shared:{block_name}: rozjazd treści między plikami: {', '.join(sorted(present_in))}")

    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Walidator spojnosci pluginu Monolynx (referencje krokow, em-dash, nazwy komend, wersja/changelog, sekcja Unreleased)."
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
    parser.add_argument(
        "--skills-root",
        type=Path,
        default=None,
        help=f"katalog z podkatalogami skilli (domyslnie {DEFAULT_SKILLS_ROOT})",
    )
    parser.add_argument(
        "--page",
        type=Path,
        action="append",
        default=None,
        help="strona opisujaca skille, do sprawdzenia pokrycia (powtarzalne; domyslnie README, help, mcp_guide, agent-bootstrap, how-to, main.py)",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="wydanie na main - '### Unreleased' jako pierwszy wpis w changelogu to blad (domyslnie wlaczane tez przez CI_COMMIT_BRANCH=main)",
    )
    parser.add_argument(
        "--settings-json",
        type=Path,
        default=DEFAULT_SETTINGS_JSON,
        help=f"sciezka do .claude/settings.json (domyslnie {DEFAULT_SETTINGS_JSON}); brak pliku nie jest bledem",
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

    files = {str(path.relative_to(args.skills_dir)): path.read_text(encoding="utf-8") for path in sorted(args.skills_dir.rglob("*.md"))}

    violations: list[str] = []
    violations.extend(check_step_refs(files))
    violations.extend(check_em_dash(files))
    violations.extend(check_command_names(files))
    violations.extend(check_runtime_contract(files))
    violations.extend(check_shared_blocks(files))
    violations.extend(check_unreleased_entries(readme_text))

    release = args.release or os.environ.get("CI_COMMIT_BRANCH") == "main"
    try:
        violations.extend(check_version_matches_changelog(plugin_json_text, readme_text, release=release))
    except ConfigError as exc:
        print(f"BLAD KONFIGURACJI: {exc}", file=sys.stderr)
        return 2

    if args.settings_json.is_file():
        settings_json_text = args.settings_json.read_text(encoding="utf-8")
        violations.extend(check_settings_env(settings_json_text, release=release))

    # Pokrycie stron: domyslne strony maja sens tylko dla prawdziwego repo;
    # walidator odpalony na katalogu tymczasowym bez --page pomija ten check.
    pages_paths = args.page
    if pages_paths is None and args.skills_dir == DEFAULT_SKILLS_DIR:
        pages_paths = list(DEFAULT_PAGES)
    if pages_paths:
        skills_root = args.skills_root or (DEFAULT_SKILLS_ROOT if args.skills_dir == DEFAULT_SKILLS_DIR else args.skills_dir / "skills")
        if not skills_root.is_dir():
            print(f"BLAD: brak katalogu skilli {skills_root}", file=sys.stderr)
            return 2
        missing_pages = [p for p in pages_paths if not p.is_file()]
        if missing_pages:
            print(f"BLAD: brak strony ze skillami {missing_pages[0]}", file=sys.stderr)
            return 2
        skill_names = sorted(d.name for d in skills_root.iterdir() if (d / "SKILL.md").is_file())
        pages = {str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p): p.read_text(encoding="utf-8") for p in pages_paths}
        violations.extend(check_skill_coverage(skill_names, pages))

    if violations:
        for violation in violations:
            print(violation)
        return 1

    print("OK: plugin spojny.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
