#!/usr/bin/env python3
"""Synchronize bundled plugin resources and standalone skills from shared sources.

Run ``python3 scripts/sync_skills.py --check`` to detect distribution drift.

Besides copying, the script injects the shared transport block: in every ``.md`` file of a skill
the text between ``<!-- shared:transport -->`` and ``<!-- /shared:transport -->`` is replaced with
``plugin/references/transport.md``. A skill whose SKILL.md carries the marker also gets
``monolynx_transport.py`` and ``monolynx_op.py`` in ``scripts/`` (MON-232).
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "plugin" / "skills"
TARGET_DIR = ROOT / "src" / "monolynx" / "static" / "skills"
TARGET_PREFIX = "monolynx-"
SCRIPT_RESOURCES = {
    "work": ("test_lock.py", "toolchain_fields.py"),
    "work-simple": ("test_lock.py", "toolchain_fields.py"),
    "setup": ("toolchain_fields.py",),
    "next": ("sprint_run_state.py", "mr_queue_state.py"),
    "sprint-run": ("sprint_run_state.py", "sprint_run.sh", "sprint_run_tick.py", "sprint_run_backend.py"),
    "mr-queue": ("mr_queue_state.py",),
    "ticket-review": ("ticket_overlap.py",),
    "mutation-check": ("mutation_report.py",),
    "create-mutation-ci-script": ("mutation_report.py", "mutation_baseline.py"),
}
REFERENCE_RESOURCES = {
    "work": ("resume", "## KROK 3:", "## KROK 4:", "resume-stages.md"),
    "work-simple": ("work", "### 3b.", "### 3c.", "researcher.md"),
    "graph-sync": ("create-graph-ci-script", "## KROK 3:", "## KROK 4:", "graph-mapper.md"),
}
WHOLE_FILE_RESOURCES = {
    "work-simple": (("work/review-rubric.md", "review-rubric.md"), ("work/lint-test.md", "lint-test.md"), ("work/commit-mr.md", "commit-mr.md")),
}


TRANSPORT_OPEN = "<!-- shared:transport -->"
TRANSPORT_CLOSE = "<!-- /shared:transport -->"
TRANSPORT_BLOCK = re.compile(re.escape(TRANSPORT_OPEN) + r".*?" + re.escape(TRANSPORT_CLOSE), re.DOTALL)
# Skille ze znacznikiem shared:transport w SKILL.md dostaja oba skrypty automatycznie (MON-232).
TRANSPORT_SCRIPTS = ("monolynx_transport.py", "monolynx_op.py")


def inject_transport(text: str) -> str:
    """Podmien tresc kazdej pary znacznikow shared:transport na kanoniczna z plugin/references/transport.md.

    Plik kanoniczny jest czytany tylko wtedy, gdy tekst ma znacznik, a para musi byc kompletna: samotny
    albo odwrocony znacznik to blad zrodla, nie cicha podmiana.
    """
    opens = text.count(TRANSPORT_OPEN)
    if opens != text.count(TRANSPORT_CLOSE):
        raise ValueError(f"Unbalanced {TRANSPORT_OPEN} markers")
    if not opens:
        return text
    canonical = (SOURCE_DIR.parent / "references" / "transport.md").read_text(encoding="utf-8").strip()
    injected, replaced = TRANSPORT_BLOCK.subn(lambda _match: f"{TRANSPORT_OPEN}\n{canonical}\n{TRANSPORT_CLOSE}", text)
    if replaced != opens:
        raise ValueError(f"Misordered {TRANSPORT_OPEN} markers")
    return injected


def render(raw: str, skill_name: str, is_skill_md: bool, *, prefix: str = TARGET_PREFIX) -> str:
    content = raw.replace("<PROJECT_SLUG>", "<PROJECT-SLUG>") if prefix else raw
    if not is_skill_md:
        return content
    end = content.find("\n---", 4)
    if not content.startswith("---\n") or end == -1:
        raise ValueError(f"{skill_name}/SKILL.md has no YAML frontmatter")
    metadata = content[4:end]
    name = f"name: {prefix}{skill_name}"
    if re.search(r"^name:", metadata, flags=re.MULTILINE):
        metadata = re.sub(r"^name:.*$", name, metadata, flags=re.MULTILINE)
    else:
        metadata = name + "\n" + metadata
    return "---\n" + metadata + content[end:]


def read_files(skill_dir: Path) -> dict[str, str]:
    files = {}
    root = skill_dir.resolve()
    for path in sorted(skill_dir.rglob("*")):
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Resource escapes skill directory: {path}")
        if path.is_file():
            files[path.relative_to(skill_dir).as_posix()] = path.read_text(encoding="utf-8")
    return files


def build(skill_dir: Path) -> dict[str, str]:
    """Build standalone files while preserving scripts verbatim."""
    files = read_files(skill_dir)
    if "SKILL.md" not in files:
        raise ValueError(f"{skill_dir.name} has no SKILL.md")
    return {name: render(content, skill_dir.name, name == "SKILL.md") if name.endswith(".md") else content for name, content in files.items()}


def bundled_resources(name: str) -> dict[str, str]:
    resources = {}
    skill_md = SOURCE_DIR / name / "SKILL.md"
    marked = skill_md.is_file() and TRANSPORT_OPEN in skill_md.read_text(encoding="utf-8")
    for script in (*SCRIPT_RESOURCES.get(name, ()), *(TRANSPORT_SCRIPTS if marked else ())):
        resources[f"scripts/{script}"] = (SOURCE_DIR.parent / "scripts" / script).read_text(encoding="utf-8")
    if name in REFERENCE_RESOURCES:
        source, start, end, filename = REFERENCE_RESOURCES[name]
        source_path = SOURCE_DIR / source / "SKILL.md"
        raw = source_path.read_text(encoding="utf-8")
        match = re.search(rf"^{re.escape(start)}.*?(?=^{re.escape(end)})", raw, flags=re.MULTILINE | re.DOTALL)
        if match is None:
            raise ValueError(f"Missing section markers {start!r}, {end!r} in {source_path}")
        # Wycinek cudzego SKILL.md moze zawierac blok transportu: ta sama podmiana co w zrodle, inaczej --check rozjezdza sie z zapisem.
        resources[f"references/{filename}"] = inject_transport(match.group().rstrip() + "\n")
    for source_rel, target_name in WHOLE_FILE_RESOURCES.get(name, ()):
        resources[target_name] = inject_transport((SOURCE_DIR / source_rel).read_text(encoding="utf-8"))
    resources["references/runtime.md"] = (SOURCE_DIR.parent / "references" / "runtime.md").read_text(encoding="utf-8")
    return resources


def write_files(directory: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def main() -> int:
    check_only = "--check" in sys.argv
    if not SOURCE_DIR.is_dir():
        print(f"BŁĄD: brak katalogu {SOURCE_DIR}", file=sys.stderr)
        return 2

    stale: list[str] = []
    synced = 0
    source_names = sorted(d.name for d in SOURCE_DIR.iterdir() if d.is_dir())
    for name in source_names:
        source = SOURCE_DIR / name
        existing_source = read_files(source)
        source_files = {path: inject_transport(content) if path.endswith(".md") else content for path, content in existing_source.items()}
        source_files["SKILL.md"] = render(source_files["SKILL.md"], name, True, prefix="")
        source_files.update(bundled_resources(name))
        files = {path: render(content, name, path == "SKILL.md") if path.endswith(".md") else content for path, content in source_files.items()}
        target = TARGET_DIR / f"{TARGET_PREFIX}{name}"
        existing = read_files(target) if target.is_dir() else {}
        if existing_source == source_files and existing == files:
            continue
        if check_only:
            stale.append(name)
            continue
        write_files(source, source_files)
        for obsolete in set(existing) - set(files):
            (target / obsolete).unlink()
        write_files(target, files)
        print(f"  zsynchronizowano {name} ({len(files)} plików)")
        synced += 1

    expected = {f"{TARGET_PREFIX}{n}" for n in source_names}
    orphans = sorted(d.name for d in TARGET_DIR.iterdir() if d.is_dir() and d.name not in expected) if TARGET_DIR.is_dir() else []
    for orphan in orphans:
        if check_only:
            stale.append(f"{orphan} (osierocony)")
        else:
            shutil.rmtree(TARGET_DIR / orphan)
            print(f"  usunięto osierocony {orphan}")
            synced += 1

    if check_only:
        if stale:
            print("Dystrybucje skilli są nieaktualne: " + ", ".join(stale), file=sys.stderr)
            print("Uruchom: python3 scripts/sync_skills.py", file=sys.stderr)
            return 1
        print("Dystrybucje skilli zgodne ze źródłami")
        return 0
    print(f"Gotowe. Zmienionych skilli: {synced}/{len(source_names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
