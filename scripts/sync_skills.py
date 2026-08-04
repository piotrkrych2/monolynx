#!/usr/bin/env python3
"""Sync skilli pluginowych do katalogu serwowanego przez install_monolynx_skills.

Zrodlo prawdy: `plugin/skills/<nazwa>/`.
Cel: `src/monolynx/static/skills/monolynx-<nazwa>/`.

Kopie roznia sie od zrodla dwoma rzeczami, dlatego nie da sie ich podlinkowac:
- SKILL.md w static ma dodatkowe pole `name: monolynx-<nazwa>` we frontmatterze
  (skille instalowane recznie nie maja namespace'u pluginu, wiec potrzebuja
  jawnej nazwy),
- placeholder `<PROJECT_SLUG>` zamieniany jest na `<PROJECT-SLUG>`, bo tylko taki
  wariant podmienia `_render_skill_content` w mcp_server.py.

Uruchamianie (lokalnie - katalog `plugin/` nie jest montowany w kontenerze):

    python3 scripts/sync_skills.py            # sync
    python3 scripts/sync_skills.py --check    # exit 1 gdy kopie sa nieaktualne
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "plugin" / "skills"
TARGET_DIR = ROOT / "src" / "monolynx" / "static" / "skills"
TARGET_PREFIX = "monolynx-"


def render(raw: str, skill_name: str, is_skill_md: bool) -> str:
    content = raw.replace("<PROJECT_SLUG>", "<PROJECT-SLUG>")
    if not is_skill_md:
        return content
    if not content.startswith("---\n"):
        raise ValueError(f"{skill_name}/SKILL.md nie ma frontmattera YAML")
    if "\nname:" in content[: content.find("\n---", 4)]:
        return content
    return "---\n" + f"name: {TARGET_PREFIX}{skill_name}\n" + content[4:]


def build(skill_dir: Path) -> dict[str, str]:
    """Zbuduj docelowa zawartosc katalogu skilla: {nazwa pliku: tresc}."""
    files = {}
    for path in sorted(skill_dir.glob("*.md")):
        is_skill_md = path.name == "SKILL.md"
        files[path.name] = render(path.read_text(encoding="utf-8"), skill_dir.name, is_skill_md)
    if "SKILL.md" not in files:
        raise ValueError(f"{skill_dir.name} nie ma pliku SKILL.md")
    return files


def main() -> int:
    check_only = "--check" in sys.argv

    if not SOURCE_DIR.is_dir():
        print(f"BLAD: brak katalogu {SOURCE_DIR}", file=sys.stderr)
        return 2

    stale: list[str] = []
    synced = 0

    source_names = sorted(d.name for d in SOURCE_DIR.iterdir() if d.is_dir())
    for name in source_names:
        files = build(SOURCE_DIR / name)
        target = TARGET_DIR / f"{TARGET_PREFIX}{name}"

        existing = {p.name: p.read_text(encoding="utf-8") for p in target.glob("*.md")} if target.is_dir() else {}
        if existing == files:
            continue

        if check_only:
            stale.append(name)
            continue

        target.mkdir(parents=True, exist_ok=True)
        for obsolete in set(existing) - set(files):
            (target / obsolete).unlink()
        for filename, content in files.items():
            (target / filename).write_text(content, encoding="utf-8")
        print(f"  zsynchronizowano {name} ({len(files)} plik(ow))")
        synced += 1

    expected = {f"{TARGET_PREFIX}{n}" for n in source_names}
    orphans = sorted(d.name for d in TARGET_DIR.iterdir() if d.is_dir() and d.name not in expected)
    for orphan in orphans:
        if check_only:
            stale.append(f"{orphan} (osierocony)")
        else:
            shutil.rmtree(TARGET_DIR / orphan)
            print(f"  usunieto osierocony {orphan}")
            synced += 1

    if check_only:
        if stale:
            print("Kopie w static/skills sa nieaktualne: " + ", ".join(stale), file=sys.stderr)
            print("Uruchom: python3 scripts/sync_skills.py", file=sys.stderr)
            return 1
        print("static/skills zgodne z plugin/skills")
        return 0

    print(f"Gotowe. Zmienionych skilli: {synced}/{len(source_names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
