#!/usr/bin/env python3
"""Synchronize Claude Code agent copies from the single-source role definitions.

plugin/agents/*.md is the single source of truth for role content (MON-184,
punkt 1 zakresu). .claude/agents/*.md is a flat, byte-for-byte copy consumed
directly by Claude Code (no per-agent subdirectories, unlike
scripts/sync_skills.py which builds a richer per-skill bundle) - it exists so
role content works without the plugin being installed, but must never drift
from the source.

Run ``python3 scripts/sync_agents.py --check`` to detect distribution drift
(exit 1 if the target is stale), or without flags to sync it (exit 0).

Optional ``--codex`` flag (MON-184, punkt 7): generates standalone Codex CLI
subagent files under ``.codex/agents/<name>.toml`` from the same
plugin/agents/*.md source. Shape confirmed against the vendor page
"Subagents | ChatGPT Learn"
(https://learn.chatgpt.com/docs/agent-configuration/subagents), fetched via
``curl`` (HTTP 200) on 2026-09-20. Literal quotes from that page (the same
ones are repeated as a comment header on every generated
``.codex/agents/*.toml`` file, see ``render_codex_toml`` below):

  "To define your own custom agents, add standalone TOML files under
  ~/.codex/agents/ for personal agents or .codex/agents/ for project-scoped
  agents."

  "Every standalone custom agent file must define: name description
  developer_instructions"

  "Codex identifies the custom agent by its name field. Matching the
  filename to the agent name is the simplest convention, but the name
  field is the source of truth."

Deliberately a SEPARATE
code path, never combined with the default/``--check`` run: the .claude/agents
sync above is exercised by tests/unit/test_sync_agents.py via monkeypatched
SOURCE_DIR/TARGET_DIR pointing at tmp_path fixtures with synthetic,
frontmatter-less content - if Codex generation ran unconditionally from the
same code path, every test invocation would also try to render/write real
``.codex/agents/`` files from that throwaway content. ``--codex`` is never in
the argv those tests use, so this path has no automated coverage - run it
manually after editing plugin/agents/*.md and check the diff.

Per MON-184 decision 6, the Anthropic model id in the ``model:`` frontmatter
key (e.g. ``sonnet``, ``opus``) is never carried into the generated TOML's
optional ``model`` field, which expects a Codex-native model id - the
generated file omits ``model``/``model_reasoning_effort`` entirely and lets
Codex resolve them from its own parent/``[agents]`` default.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "plugin" / "agents"
TARGET_DIR = ROOT / ".claude" / "agents"
CODEX_TARGET_DIR = ROOT / ".codex" / "agents"

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_NAME_RE = re.compile(r"^name:\s*(.+)$", re.M)
_DESCRIPTION_RE = re.compile(r'^description:\s*"((?:[^"\\]|\\.)*)"', re.M)


def read_markdown_files(directory: Path) -> dict[str, str]:
    """Only *.md at the top level - agents are single files, no nesting."""
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.md")) if path.is_file()}


def parse_agent_markdown(text: str) -> tuple[str, str, str] | None:
    """Split one plugin/agents/<role>.md into (name, description_toml_literal,
    body) for the Codex TOML rendering below. Returns None for anything
    without a matching ``name:``/``description: "..."`` frontmatter (granica
    systemu - input from the filesystem is validated, never indexed blindly)
    instead of raising, so a malformed or frontmatter-less file is skipped
    with a warning rather than crashing the whole --codex run over one bad
    file.

    ``description_toml_literal`` is the captured quoted-string body VERBATIM,
    not YAML-decoded. YAML double-quoted scalar escaping (``\\"``, ``\\\\``)
    and TOML basic-string escaping agree on exactly those two sequences, and
    this frontmatter never uses a real newline inside the description (the
    whole field is one physical source line, using literal two-character
    ``\\n`` the same way Claude Code's own subagent tool listing renders it
    verbatim) - so copying the already-escaped source text straight into a
    TOML basic string preserves the identical decoded value in both formats.
    Decoding it first (e.g. via json.loads) and then re-escaping would turn
    each already-literal ``\\n`` into ``\\\\n`` - double-escaped, wrong."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    frontmatter = match.group(1)
    name_match = _NAME_RE.search(frontmatter)
    description_match = _DESCRIPTION_RE.search(frontmatter)
    if not name_match or not description_match:
        return None
    name = name_match.group(1).strip()
    if not name:
        return None
    description_toml_literal = description_match.group(1)
    body = text[match.end() :].strip("\n")
    if not body:
        return None
    return name, description_toml_literal, body


def _toml_body_multiline_string(value: str) -> str:
    """TOML multi-line basic string (\"\"\"...\"\"\") for raw, un-escaped markdown
    body text. Backslashes must be escaped (a lone ``\\`` starts a TOML escape
    sequence); a run of 3+ quotes is neutralized so it can never be read as
    the closing delimiter. Single/double quotes are otherwise left alone -
    both are valid unescaped inside a TOML multi-line basic string."""
    escaped = value.replace("\\", "\\\\")
    escaped = re.sub(r'"""', '""\\"', escaped)
    return '"""\n' + escaped + '\n"""\n'


_CODEX_SOURCE_HEADER = """\
# Wygenerowane przez scripts/sync_agents.py --codex ze zrodla plugin/agents/*.md - NIE EDYTOWAC RECZNIE.
#
# Ksztalt pliku (name/description/developer_instructions) wg strony
# "Subagents | ChatGPT Learn"
# (https://learn.chatgpt.com/docs/agent-configuration/subagents), pobranej
# curl (HTTP 200) 2026-09-20. Cytaty doslowne z tej strony:
#
# "To define your own custom agents, add standalone TOML files under
# ~/.codex/agents/ for personal agents or .codex/agents/ for project-scoped
# agents."
#
# "Every standalone custom agent file must define: name description
# developer_instructions"
#
# "Codex identifies the custom agent by its name field. Matching the
# filename to the agent name is the simplest convention, but the name
# field is the source of truth."
"""


def render_codex_toml(name: str, description_toml_literal: str, body: str) -> str:
    return (
        _CODEX_SOURCE_HEADER
        + f'name = "{name}"\ndescription = "{description_toml_literal}"\ndeveloper_instructions = {_toml_body_multiline_string(body)}'
    )


def sync_codex_agents(check_only: bool) -> int:
    if not SOURCE_DIR.is_dir():
        print(f"BŁĄD: brak katalogu {SOURCE_DIR}", file=sys.stderr)
        return 2

    rendered: dict[str, str] = {}
    for path in sorted(SOURCE_DIR.glob("*.md")):
        parsed = parse_agent_markdown(path.read_text(encoding="utf-8"))
        if parsed is None:
            print(f"UWAGA: {path.name} bez poprawnego frontmatter (name/description) - pominięto w .codex/agents/", file=sys.stderr)
            continue
        name, description, body = parsed
        rendered[f"{name}.toml"] = render_codex_toml(name, description, body)

    existing = {p.name: p.read_text(encoding="utf-8") for p in CODEX_TARGET_DIR.glob("*.toml")} if CODEX_TARGET_DIR.is_dir() else {}

    stale = [name for name, content in rendered.items() if existing.get(name) != content]
    orphans = sorted(name for name in existing if name not in rendered)
    stale.extend(f"{name} (osierocony)" for name in orphans)

    if check_only:
        if stale:
            print("Pliki .codex/agents/ są nieaktualne: " + ", ".join(stale), file=sys.stderr)
            print("Uruchom: python3 scripts/sync_agents.py --codex", file=sys.stderr)
            return 1
        print(".codex/agents/ zgodne z plugin/agents/")
        return 0

    CODEX_TARGET_DIR.mkdir(parents=True, exist_ok=True)
    synced = 0
    for name, content in rendered.items():
        if existing.get(name) == content:
            continue
        (CODEX_TARGET_DIR / name).write_text(content, encoding="utf-8")
        print(f"  zsynchronizowano .codex/agents/{name}")
        synced += 1
    for name in orphans:
        (CODEX_TARGET_DIR / name).unlink()
        print(f"  usunięto osierocony .codex/agents/{name}")
        synced += 1

    print(f"Gotowe (.codex/agents). Zmienionych plików: {synced}/{len(rendered)}")
    return 0


def main() -> int:
    check_only = "--check" in sys.argv
    if "--codex" in sys.argv:
        return sync_codex_agents(check_only)
    if not SOURCE_DIR.is_dir():
        print(f"BŁĄD: brak katalogu {SOURCE_DIR}", file=sys.stderr)
        return 2

    source_files = read_markdown_files(SOURCE_DIR)
    target_files = read_markdown_files(TARGET_DIR) if TARGET_DIR.is_dir() else {}

    stale: list[str] = []
    for name, content in source_files.items():
        if target_files.get(name) != content:
            stale.append(name)

    orphans = sorted(name for name in target_files if name not in source_files)
    stale.extend(f"{name} (osierocony)" for name in orphans)

    if check_only:
        if stale:
            print("Kopie agentów .claude/agents/ są nieaktualne: " + ", ".join(stale), file=sys.stderr)
            print("Uruchom: python3 scripts/sync_agents.py", file=sys.stderr)
            return 1
        print("Kopie agentów .claude/agents/ zgodne z plugin/agents/")
        return 0

    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    synced = 0
    for name, content in source_files.items():
        if target_files.get(name) == content:
            continue
        (TARGET_DIR / name).write_text(content, encoding="utf-8")
        print(f"  zsynchronizowano {name}")
        synced += 1

    for name in orphans:
        (TARGET_DIR / name).unlink()
        print(f"  usunięto osierocony {name}")
        synced += 1

    print(f"Gotowe. Zmienionych plików: {synced}/{len(source_files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
