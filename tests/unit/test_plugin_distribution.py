"""Native Codex and standalone skill distribution contracts (MON-182)."""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugin"
pytestmark = pytest.mark.unit

RESOURCES = [
    ("next", "scripts/sprint_run_state.py"),
    ("next", "scripts/mr_queue_state.py"),
    ("sprint-run", "scripts/sprint_run_state.py"),
    ("sprint-run", "scripts/sprint_run.sh"),
    ("mr-queue", "scripts/mr_queue_state.py"),
    ("ticket-review", "scripts/ticket_overlap.py"),
    ("mutation-check", "scripts/mutation_report.py"),
    ("create-mutation-ci-script", "scripts/mutation_report.py"),
    ("create-mutation-ci-script", "scripts/mutation_baseline.py"),
    ("work", "references/resume-stages.md"),
    ("work-simple", "references/researcher.md"),
    ("graph-sync", "references/graph-mapper.md"),
]


def test_native_codex_manifest_uses_shared_skills_and_mcp():
    codex = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    claude = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert codex["name"] == claude["name"] == "monolynx"
    assert codex["version"] == claude["version"]
    assert re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", codex["version"])
    assert codex["skills"] == "./skills/"
    assert codex["mcpServers"] == "./.mcp.json"
    mcp = json.loads((PLUGIN / codex["mcpServers"]).read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["monolynx"]["url"] == "https://monolynx.com/mcp"


def test_skill_frontmatter_names_match_each_distribution():
    sources = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    assert sources
    for source in sources:
        raw = source.read_text(encoding="utf-8")
        assert raw.startswith("---\n"), source
        metadata = yaml.safe_load(raw.split("---", 2)[1])
        assert metadata["name"] == source.parent.name
        standalone = ROOT / "src/monolynx/static/skills" / f"monolynx-{source.parent.name}" / "SKILL.md"
        metadata = yaml.safe_load(standalone.read_text(encoding="utf-8").split("---", 2)[1])
        assert metadata["name"] == f"monolynx-{source.parent.name}"


@pytest.mark.parametrize(("skill", "relative_path"), RESOURCES)
def test_workflows_bundle_referenced_resources(skill, relative_path):
    resource = PLUGIN / "skills" / skill / relative_path
    content = resource.read_text(encoding="utf-8")
    assert content.strip()
    standalone = ROOT / "src/monolynx/static/skills" / f"monolynx-{skill}" / relative_path
    expected = content.replace("<PROJECT_SLUG>", "<PROJECT-SLUG>") if resource.suffix == ".md" else content
    assert standalone.read_text(encoding="utf-8") == expected
    workflow = (resource.parents[1] / "SKILL.md").read_text(encoding="utf-8")
    assert relative_path in workflow
    if resource.suffix == ".py":
        compile(content, str(resource), "exec")
