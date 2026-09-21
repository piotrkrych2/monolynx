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
    canonical = tmp_path / "references"
    canonical.mkdir()
    (canonical / "runtime.md").write_text("# Runtime\n", encoding="utf-8")
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


def test_bundled_resources_includes_canonical_runtime(tmp_path, monkeypatch):
    source = tmp_path / "plugin/skills"
    source.mkdir(parents=True)
    reference = source.parent / "references/runtime.md"
    reference.parent.mkdir()
    reference.write_text("# Canonical runtime\n", encoding="utf-8")
    monkeypatch.setattr(sync_skills, "SOURCE_DIR", source)
    assert sync_skills.bundled_resources("sample") == {"references/runtime.md": "# Canonical runtime\n"}


def test_bundled_resources_requires_runtime_source(tmp_path, monkeypatch):
    source = tmp_path / "plugin/skills"
    source.mkdir(parents=True)
    monkeypatch.setattr(sync_skills, "SOURCE_DIR", source)
    with pytest.raises(FileNotFoundError):
        sync_skills.bundled_resources("sample")


# WHOLE_FILE_RESOURCES["work-simple"] jest zrodlem prawdy dla wszystkich testow
# nizej - kazdy nowy wpis (source_rel, target_name) dostaje pokrycie automatycznie,
# bez dopisywania kolejnej kopii-wklejki per plik.
WHOLE_FILE_ENTRIES = sync_skills.WHOLE_FILE_RESOURCES["work-simple"]


def _whole_file_content(target_name: str) -> str:
    return f"# Fixture dla {target_name}\n\nTresc unikalna dla tego pliku: {target_name}.\n"


@pytest.fixture
def whole_file_source_tree(tmp_path, monkeypatch):
    skills = tmp_path / "plugin/skills"
    work = skills / "work"
    work.mkdir(parents=True)
    (work / "SKILL.md").write_text("---\nname: work\ndescription: Work\n---\nWork\n", encoding="utf-8")
    work_simple = skills / "work-simple"
    work_simple.mkdir()
    (work_simple / "SKILL.md").write_text("---\nname: work-simple\ndescription: Work simple\n---\nWork simple\n", encoding="utf-8")
    references = skills.parent / "references"
    references.mkdir()
    (references / "runtime.md").write_text("# Runtime\n", encoding="utf-8")
    for source_rel, target_name in WHOLE_FILE_ENTRIES:
        source_path = skills / source_rel
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(_whole_file_content(target_name), encoding="utf-8")
    monkeypatch.setattr(sync_skills, "SOURCE_DIR", skills)
    monkeypatch.setattr(sync_skills, "REFERENCE_RESOURCES", {})
    return skills


@pytest.mark.parametrize("source_rel,target_name", WHOLE_FILE_ENTRIES)
def test_bundled_resources_returns_whole_file_copy_for_work_simple(whole_file_source_tree, source_rel, target_name):
    resources = sync_skills.bundled_resources("work-simple")
    assert resources[target_name] == _whole_file_content(target_name)


@pytest.mark.parametrize("source_rel,target_name", WHOLE_FILE_ENTRIES)
def test_check_detects_missing_generated_whole_file_copy_and_sync_restores_it(whole_file_source_tree, tmp_path, monkeypatch, source_rel, target_name):
    skills = whole_file_source_tree
    target = tmp_path / "target"
    monkeypatch.setattr(sync_skills, "TARGET_DIR", target)
    monkeypatch.setattr(sys, "argv", ["sync_skills.py"])
    assert sync_skills.main() == 0
    generated = skills / "work-simple" / target_name
    static_copy = target / "monolynx-work-simple" / target_name
    expected = _whole_file_content(target_name)
    assert generated.read_text(encoding="utf-8") == expected
    assert static_copy.read_text(encoding="utf-8") == expected
    generated.unlink()
    monkeypatch.setattr(sys, "argv", ["sync_skills.py", "--check"])
    assert sync_skills.main() == 1
    assert not generated.exists()
    monkeypatch.setattr(sys, "argv", ["sync_skills.py"])
    assert sync_skills.main() == 0
    assert generated.read_text(encoding="utf-8") == expected
    monkeypatch.setattr(sys, "argv", ["sync_skills.py", "--check"])
    assert sync_skills.main() == 0


@pytest.mark.parametrize("source_rel,target_name", WHOLE_FILE_ENTRIES)
def test_check_detects_whole_file_content_drift_and_sync_restores_it(whole_file_source_tree, tmp_path, monkeypatch, source_rel, target_name):
    target = tmp_path / "target"
    monkeypatch.setattr(sync_skills, "TARGET_DIR", target)
    monkeypatch.setattr(sys, "argv", ["sync_skills.py"])
    assert sync_skills.main() == 0
    static_copy = target / "monolynx-work-simple" / target_name
    expected = _whole_file_content(target_name)
    assert static_copy.read_text(encoding="utf-8") == expected
    static_copy.write_text("recznie zmieniona tresc", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["sync_skills.py", "--check"])
    assert sync_skills.main() == 1
    assert static_copy.read_text(encoding="utf-8") == "recznie zmieniona tresc"
    monkeypatch.setattr(sys, "argv", ["sync_skills.py"])
    assert sync_skills.main() == 0
    assert static_copy.read_text(encoding="utf-8") == expected
    monkeypatch.setattr(sys, "argv", ["sync_skills.py", "--check"])
    assert sync_skills.main() == 0


@pytest.mark.parametrize("source_rel,target_name", WHOLE_FILE_ENTRIES)
@pytest.mark.parametrize("check_only", [False, True])
def test_bundled_resources_rejects_missing_whole_file_source(tmp_path, monkeypatch, check_only, source_rel, target_name):
    source = tmp_path / "source"
    work = source / "work"
    work.mkdir(parents=True)
    (work / "SKILL.md").write_text("---\nname: work\ndescription: Work\n---\nWork\n", encoding="utf-8")
    work_simple = source / "work-simple"
    work_simple.mkdir()
    (work_simple / "SKILL.md").write_text("---\nname: work-simple\ndescription: Work simple\n---\nWork simple\n", encoding="utf-8")
    references = source.parent / "references"
    references.mkdir()
    (references / "runtime.md").write_text("# Runtime\n", encoding="utf-8")
    for other_source_rel, other_target_name in WHOLE_FILE_ENTRIES:
        if other_source_rel == source_rel:
            continue
        other_path = source / other_source_rel
        other_path.parent.mkdir(parents=True, exist_ok=True)
        other_path.write_text(_whole_file_content(other_target_name), encoding="utf-8")
    monkeypatch.setattr(sync_skills, "SOURCE_DIR", source)
    monkeypatch.setattr(sync_skills, "TARGET_DIR", tmp_path / "target")
    monkeypatch.setattr(sync_skills, "REFERENCE_RESOURCES", {})
    monkeypatch.setattr(sys, "argv", ["sync_skills.py", "--check"] if check_only else ["sync_skills.py"])
    with pytest.raises(FileNotFoundError) as error:
        sync_skills.main()
    assert Path(error.value.filename) == source / source_rel
