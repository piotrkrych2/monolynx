"""Distribution of nested skill resources (MON-182)."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("sync_skills_under_test", ROOT / "scripts/sync_skills.py")
assert SPEC is not None and SPEC.loader is not None
sync_skills = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_skills)
pytestmark = pytest.mark.unit


@pytest.fixture
def source_skill(tmp_path):
    skill = tmp_path / "source" / "sample"
    (skill / "references").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "SKILL.md").write_text("---\nname: sample\ndescription: Sample\n---\n<PROJECT_SLUG>\n", encoding="utf-8")
    (skill / "references/context.md").write_text("Context <PROJECT_SLUG>\n", encoding="utf-8")
    (skill / "scripts/helper.py").write_text('print("<PROJECT_SLUG>")\n', encoding="utf-8")
    (skill / "scripts/run.sh").write_text('echo "<PROJECT_SLUG>"\n', encoding="utf-8")
    return skill


def test_build_preserves_nested_resources_and_only_renders_markdown(source_skill):
    files = sync_skills.build(source_skill)
    assert set(files) == {"SKILL.md", "references/context.md", "scripts/helper.py", "scripts/run.sh"}
    assert "name: monolynx-sample\n" in files["SKILL.md"]
    assert files["SKILL.md"].count("name:") == 1
    assert "<PROJECT-SLUG>" in files["SKILL.md"]
    assert files["references/context.md"] == "Context <PROJECT-SLUG>\n"
    for name in ("scripts/helper.py", "scripts/run.sh"):
        assert files[name] == (source_skill / name).read_text(encoding="utf-8")


def test_build_rejects_resource_symlink_outside_skill(source_skill, tmp_path):
    external = tmp_path / "outside.md"
    external.write_text("outside", encoding="utf-8")
    (source_skill / "references/outside.md").symlink_to(external)
    with pytest.raises(ValueError):
        sync_skills.build(source_skill)


def test_sync_check_detects_nested_drift_and_sync_removes_stale_resource(source_skill, tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(sync_skills, "SOURCE_DIR", source_skill.parent)
    monkeypatch.setattr(sync_skills, "TARGET_DIR", target)
    monkeypatch.setattr(sys, "argv", ["sync_skills.py"])
    assert sync_skills.main() == 0
    destination = target / "monolynx-sample"
    resource = destination / "references/context.md"
    assert resource.read_text(encoding="utf-8") == "Context <PROJECT-SLUG>\n"
    resource.write_text("stale content", encoding="utf-8")
    obsolete = destination / "scripts/obsolete.py"
    obsolete.write_text("obsolete", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["sync_skills.py", "--check"])
    assert sync_skills.main() == 1
    assert resource.read_text(encoding="utf-8") == "stale content"
    assert obsolete.exists()
    monkeypatch.setattr(sys, "argv", ["sync_skills.py"])
    assert sync_skills.main() == 0
    assert not obsolete.exists()
    assert resource.read_text(encoding="utf-8") == "Context <PROJECT-SLUG>\n"
    monkeypatch.setattr(sys, "argv", ["sync_skills.py", "--check"])
    assert sync_skills.main() == 0


@pytest.mark.parametrize("check_only", [False, True])
def test_sync_rejects_missing_reference_source(tmp_path, monkeypatch, check_only):
    source = tmp_path / "source"
    skill = source / "work"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: work\ndescription: Work\n---\nWork\n", encoding="utf-8")
    monkeypatch.setattr(sync_skills, "SOURCE_DIR", source)
    monkeypatch.setattr(sync_skills, "TARGET_DIR", tmp_path / "target")
    monkeypatch.setattr(sys, "argv", ["sync_skills.py", "--check"] if check_only else ["sync_skills.py"])
    with pytest.raises(FileNotFoundError) as error:
        sync_skills.main()
    assert Path(error.value.filename) == source / "resume" / "SKILL.md"


def test_committed_standalone_distribution_matches_source():
    expected_names = {f"monolynx-{path.name}" for path in sync_skills.SOURCE_DIR.iterdir() if path.is_dir()}
    assert {path.name for path in sync_skills.TARGET_DIR.iterdir() if path.is_dir()} == expected_names
    for source in sorted(sync_skills.SOURCE_DIR.iterdir()):
        if not source.is_dir():
            continue
        target = sync_skills.TARGET_DIR / f"monolynx-{source.name}"
        actual = {p.relative_to(target).as_posix(): p.read_text(encoding="utf-8") for p in target.rglob("*") if p.is_file()}
        assert actual == sync_skills.build(source), source.name
