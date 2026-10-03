"""Distribution of nested skill resources (MON-182)."""

import importlib.util
import re
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


def _write_test_lock_stub(skills_dir: Path) -> None:
    """`work` i `work-simple` pakuja `scripts/test_lock.py` (MON-251) - sztuczne drzewo musi go miec obok `references/`."""
    scripts = skills_dir.parent / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "test_lock.py").write_text("# test_lock stub\n", encoding="utf-8")


@pytest.mark.parametrize("check_only", [False, True])
def test_sync_rejects_missing_reference_source(tmp_path, monkeypatch, check_only):
    source = tmp_path / "source"
    skill = source / "work"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: work\ndescription: Work\n---\nWork\n", encoding="utf-8")
    _write_test_lock_stub(source)
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
    _write_test_lock_stub(skills)
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
    _write_test_lock_stub(source)
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


# ---------------------------------------------------------------------------
# MON-232: blok shared:transport wstrzykiwany z plugin/references/transport.md
# i automatyczne zasoby skryptow dla skilli ze znacznikiem
# ---------------------------------------------------------------------------

TRANSPORT_MARKER_RE = re.compile(r"<!-- shared:transport -->(.*?)<!-- /shared:transport -->", re.DOTALL)
CANONICAL_TRANSPORT = "## Transport\n\nKanoniczna tresc bloku transportu.\n\n- punkt 1\n- punkt 2\n"
TRANSPORT_SCRIPTS = ("monolynx_transport.py", "monolynx_op.py")


def _marked(body: str) -> str:
    return f"---\nname: sample\ndescription: Sample\n---\nIntro\n<!-- shared:transport -->\n{body}<!-- /shared:transport -->\nOutro\n"


@pytest.fixture
def transport_tree(tmp_path, monkeypatch):
    skills = tmp_path / "plugin/skills"
    skills.mkdir(parents=True)
    references = skills.parent / "references"
    references.mkdir()
    (references / "runtime.md").write_text("# Runtime\n", encoding="utf-8")
    (references / "transport.md").write_text(CANONICAL_TRANSPORT, encoding="utf-8")
    scripts = skills.parent / "scripts"
    scripts.mkdir()
    for name in TRANSPORT_SCRIPTS:
        (scripts / name).write_text(f"# {name}\nprint('{name}')\n", encoding="utf-8")
    (scripts / "mr_queue_state.py").write_text("# mr_queue_state\n", encoding="utf-8")
    marked = skills / "sample"
    marked.mkdir()
    (marked / "SKILL.md").write_text(_marked("STARA TRESC\n"), encoding="utf-8")
    (marked / "references").mkdir()
    nested = "Przed\n<!-- shared:transport -->\nSTARA W REFERENCJI\n<!-- /shared:transport -->\nPo\n"
    (marked / "references/extra.md").write_text(nested, encoding="utf-8")
    plain = skills / "plain"
    plain.mkdir()
    (plain / "SKILL.md").write_text("---\nname: plain\ndescription: Plain\n---\nBez znacznikow\n", encoding="utf-8")
    monkeypatch.setattr(sync_skills, "SOURCE_DIR", skills)
    monkeypatch.setattr(sync_skills, "TARGET_DIR", tmp_path / "target")
    return skills


def _run_sync(monkeypatch, *flags):
    monkeypatch.setattr(sys, "argv", ["sync_skills.py", *flags])
    return sync_skills.main()


def _between_markers(text: str) -> str:
    match = TRANSPORT_MARKER_RE.search(text)
    assert match is not None, text
    return match.group(1)


def test_sync_injects_canonical_transport_block_into_source_and_static_copy(transport_tree, tmp_path, monkeypatch):
    assert _run_sync(monkeypatch) == 0

    for path in (transport_tree / "sample/SKILL.md", tmp_path / "target/monolynx-sample/SKILL.md"):
        text = path.read_text(encoding="utf-8")
        assert _between_markers(text).strip() == CANONICAL_TRANSPORT.strip()
        assert "STARA TRESC" not in text
        assert text.startswith("---\nname: ")
        assert "Intro\n<!-- shared:transport -->" in text
        assert text.endswith("<!-- /shared:transport -->\nOutro\n")


def test_sync_injects_block_into_nested_markdown_with_markers(transport_tree, tmp_path, monkeypatch):
    assert _run_sync(monkeypatch) == 0

    for path in (transport_tree / "sample/references/extra.md", tmp_path / "target/monolynx-sample/references/extra.md"):
        text = path.read_text(encoding="utf-8")
        assert _between_markers(text).strip() == CANONICAL_TRANSPORT.strip()
        assert text.startswith("Przed\n<!-- shared:transport -->")
        assert text.endswith("<!-- /shared:transport -->\nPo\n")


def test_sync_is_idempotent_for_transport_block(transport_tree, monkeypatch):
    assert _run_sync(monkeypatch) == 0
    first = (transport_tree / "sample/SKILL.md").read_text(encoding="utf-8")

    assert _run_sync(monkeypatch) == 0
    assert (transport_tree / "sample/SKILL.md").read_text(encoding="utf-8") == first
    assert _run_sync(monkeypatch, "--check") == 0


def test_check_fails_when_block_differs_from_canonical_and_does_not_write(transport_tree, monkeypatch):
    assert _run_sync(monkeypatch) == 0
    skill = transport_tree / "sample/SKILL.md"
    drifted = skill.read_text(encoding="utf-8").replace("punkt 2", "punkt ZMIENIONY")
    skill.write_text(drifted, encoding="utf-8")

    assert _run_sync(monkeypatch, "--check") == 1
    assert skill.read_text(encoding="utf-8") == drifted

    assert _run_sync(monkeypatch) == 0
    assert "punkt 2" in skill.read_text(encoding="utf-8")
    assert "punkt ZMIENIONY" not in skill.read_text(encoding="utf-8")
    assert _run_sync(monkeypatch, "--check") == 0


def test_check_fails_when_canonical_transport_changes(transport_tree, monkeypatch):
    assert _run_sync(monkeypatch) == 0
    (transport_tree.parent / "references/transport.md").write_text("## Transport\n\nNOWA tresc kanoniczna.\n", encoding="utf-8")

    assert _run_sync(monkeypatch, "--check") == 1

    assert _run_sync(monkeypatch) == 0
    assert _between_markers((transport_tree / "sample/SKILL.md").read_text(encoding="utf-8")).strip() == "## Transport\n\nNOWA tresc kanoniczna."
    assert _run_sync(monkeypatch, "--check") == 0


def test_skill_without_markers_is_left_untouched_and_gets_no_transport_scripts(transport_tree, tmp_path, monkeypatch):
    original = (transport_tree / "plain/SKILL.md").read_text(encoding="utf-8")

    assert _run_sync(monkeypatch) == 0

    assert (transport_tree / "plain/SKILL.md").read_text(encoding="utf-8") == original
    for name in TRANSPORT_SCRIPTS:
        assert not (transport_tree / "plain/scripts" / name).exists()
        assert not (tmp_path / "target/monolynx-plain/scripts" / name).exists()


def test_skill_with_marker_gets_transport_scripts_byte_identical(transport_tree, tmp_path, monkeypatch):
    assert _run_sync(monkeypatch) == 0

    for name in TRANSPORT_SCRIPTS:
        expected = (transport_tree.parent / "scripts" / name).read_bytes()
        assert (transport_tree / "sample/scripts" / name).read_bytes() == expected
        assert (tmp_path / "target/monolynx-sample/scripts" / name).read_bytes() == expected


def test_check_detects_missing_transport_script_copy_and_sync_restores_it(transport_tree, tmp_path, monkeypatch):
    assert _run_sync(monkeypatch) == 0
    copy = tmp_path / "target/monolynx-sample/scripts/monolynx_op.py"
    copy.unlink()

    assert _run_sync(monkeypatch, "--check") == 1
    assert not copy.exists()

    assert _run_sync(monkeypatch) == 0
    assert copy.exists()


def test_bundled_resources_for_marked_skill_include_transport_scripts_and_runtime(transport_tree):
    resources = sync_skills.bundled_resources("sample")

    assert set(resources) == {"references/runtime.md", "scripts/monolynx_transport.py", "scripts/monolynx_op.py"}
    assert resources["scripts/monolynx_op.py"] == (transport_tree.parent / "scripts/monolynx_op.py").read_text(encoding="utf-8")


def test_bundled_resources_for_unmarked_skill_have_only_runtime(transport_tree):
    assert set(sync_skills.bundled_resources("plain")) == {"references/runtime.md"}


def test_marked_skill_with_existing_script_entry_gets_both_old_and_transport_scripts(transport_tree):
    skill = transport_tree / "mr-queue"
    skill.mkdir()
    (skill / "SKILL.md").write_text(_marked("x\n").replace("name: sample", "name: mr-queue"), encoding="utf-8")

    resources = sync_skills.bundled_resources("mr-queue")

    assert set(resources) == {"references/runtime.md", "scripts/mr_queue_state.py", "scripts/monolynx_transport.py", "scripts/monolynx_op.py"}


def test_marker_only_in_nested_markdown_does_not_add_transport_scripts(transport_tree):
    skill = transport_tree / "nested-only"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: nested-only\ndescription: N\n---\nBez znacznika\n", encoding="utf-8")
    (skill / "references/extra.md").write_text("<!-- shared:transport -->\nx\n<!-- /shared:transport -->\n", encoding="utf-8")

    assert set(sync_skills.bundled_resources("nested-only")) == {"references/runtime.md"}


UNBALANCED_TEXTS = {
    "only-opening": f"Przed\n{sync_skills.TRANSPORT_OPEN}\nx\nPo\n",
    "only-closing": f"Przed\nx\n{sync_skills.TRANSPORT_CLOSE}\nPo\n",
    "two-opening-one-closing": f"{sync_skills.TRANSPORT_OPEN}\na\n{sync_skills.TRANSPORT_CLOSE}\n{sync_skills.TRANSPORT_OPEN}\nb\n",
}


@pytest.mark.parametrize("text", list(UNBALANCED_TEXTS.values()), ids=list(UNBALANCED_TEXTS))
def test_inject_transport_rejects_unbalanced_markers(transport_tree, text):
    with pytest.raises(ValueError, match="Unbalanced"):
        sync_skills.inject_transport(text)


def test_inject_transport_rejects_reversed_markers(transport_tree):
    text = f"Przed\n{sync_skills.TRANSPORT_CLOSE}\nx\n{sync_skills.TRANSPORT_OPEN}\nPo\n"

    with pytest.raises(ValueError, match="Misordered"):
        sync_skills.inject_transport(text)


@pytest.mark.parametrize("check_only", [False, True])
def test_sync_with_unbalanced_markers_fails_and_writes_nothing_for_that_skill(transport_tree, tmp_path, monkeypatch, check_only):
    skill = transport_tree / "sample/SKILL.md"
    broken = f"---\nname: sample\ndescription: Sample\n---\n{sync_skills.TRANSPORT_OPEN}\nbez zamkniecia\n"
    skill.write_text(broken, encoding="utf-8")

    with pytest.raises(ValueError, match="Unbalanced"):
        _run_sync(monkeypatch, *(["--check"] if check_only else []))

    assert skill.read_text(encoding="utf-8") == broken
    assert not (tmp_path / "target/monolynx-sample").exists()


def test_inject_transport_replaces_every_pair_in_one_file_and_is_idempotent(transport_tree):
    block = f"{sync_skills.TRANSPORT_OPEN}\nSTARE\n{sync_skills.TRANSPORT_CLOSE}"
    text = f"Pierwszy\n{block}\nSrodek\n{block.replace('STARE', 'INNE STARE')}\nOstatni\n"

    injected = sync_skills.inject_transport(text)

    fresh = f"{sync_skills.TRANSPORT_OPEN}\n{CANONICAL_TRANSPORT.strip()}\n{sync_skills.TRANSPORT_CLOSE}"
    assert injected == f"Pierwszy\n{fresh}\nSrodek\n{fresh}\nOstatni\n"
    assert sync_skills.inject_transport(injected) == injected


def test_inject_transport_without_markers_does_not_need_canonical_file(transport_tree):
    (transport_tree.parent / "references/transport.md").unlink()

    assert sync_skills.inject_transport("Zwykly tekst\n") == "Zwykly tekst\n"


def test_sync_with_marker_but_missing_canonical_file_raises_file_not_found(transport_tree, monkeypatch):
    canonical = transport_tree.parent / "references/transport.md"
    canonical.unlink()

    with pytest.raises(FileNotFoundError) as error:
        _run_sync(monkeypatch)

    assert Path(error.value.filename) == canonical


def test_sync_without_any_marker_works_when_canonical_file_is_missing(transport_tree, tmp_path, monkeypatch):
    (transport_tree.parent / "references/transport.md").unlink()
    (transport_tree / "sample/references/extra.md").unlink()
    (transport_tree / "sample/SKILL.md").write_text("---\nname: sample\ndescription: Sample\n---\nBez znacznikow\n", encoding="utf-8")

    assert _run_sync(monkeypatch) == 0

    assert (tmp_path / "target/monolynx-sample/SKILL.md").exists()
    assert _run_sync(monkeypatch, "--check") == 0


def test_reference_excerpt_taken_from_another_skill_gets_canonical_transport_block(transport_tree, monkeypatch):
    other = transport_tree / "other"
    other.mkdir()
    stale = f"{sync_skills.TRANSPORT_OPEN}\nSTARA W WYCINKU\n{sync_skills.TRANSPORT_CLOSE}"
    (other / "SKILL.md").write_text(f"---\nname: other\ndescription: O\n---\n## A\n{stale}\n## B\nKoniec\n", encoding="utf-8")
    monkeypatch.setattr(sync_skills, "REFERENCE_RESOURCES", {"sample": ("other", "## A", "## B", "ref.md")})

    resources = sync_skills.bundled_resources("sample")

    excerpt = resources["references/ref.md"]
    assert _between_markers(excerpt).strip() == CANONICAL_TRANSPORT.strip()
    assert "STARA W WYCINKU" not in excerpt
    assert excerpt.startswith("## A\n")
    assert "## B" not in excerpt


def test_whole_file_resource_gets_canonical_transport_block(transport_tree, monkeypatch):
    other = transport_tree / "other"
    other.mkdir()
    stale = f"{sync_skills.TRANSPORT_OPEN}\nSTARA W PLIKU\n{sync_skills.TRANSPORT_CLOSE}"
    (other / "lint.md").write_text(f"Poczatek\n{stale}\nKoniec\n", encoding="utf-8")
    monkeypatch.setattr(sync_skills, "WHOLE_FILE_RESOURCES", {"sample": (("other/lint.md", "lint.md"),)})

    resources = sync_skills.bundled_resources("sample")

    assert _between_markers(resources["lint.md"]).strip() == CANONICAL_TRANSPORT.strip()
    assert "STARA W PLIKU" not in resources["lint.md"]
    assert resources["lint.md"].startswith("Poczatek\n")
    assert resources["lint.md"].endswith("Koniec\n")


def test_skill_md_with_only_closing_marker_gets_no_transport_scripts(transport_tree):
    skill = transport_tree / "closing-only"
    skill.mkdir()
    (skill / "SKILL.md").write_text(f"---\nname: closing-only\ndescription: C\n---\nTekst\n{sync_skills.TRANSPORT_CLOSE}\n", encoding="utf-8")

    assert set(sync_skills.bundled_resources("closing-only")) == {"references/runtime.md"}


# MON-251: `test_lock.py` jedzie tylko z `work` i `work-simple` (kolejka testow miedzy worktree).
TEST_LOCK_SKILLS = ("work", "work-simple")
OTHER_SKILLS = sorted(path.parent.name for path in (ROOT / "plugin" / "skills").glob("*/SKILL.md") if path.parent.name not in TEST_LOCK_SKILLS)
TEST_LOCK_SOURCE = ROOT / "plugin" / "scripts" / "test_lock.py"


@pytest.mark.parametrize("skill", TEST_LOCK_SKILLS)
def test_work_skills_bundle_test_lock_script(skill):
    resources = sync_skills.bundled_resources(skill)

    assert resources["scripts/test_lock.py"] == TEST_LOCK_SOURCE.read_text(encoding="utf-8")


@pytest.mark.parametrize("skill", OTHER_SKILLS)
def test_other_skills_do_not_bundle_test_lock_script(skill):
    assert "scripts/test_lock.py" not in sync_skills.bundled_resources(skill)


@pytest.mark.parametrize("skill", TEST_LOCK_SKILLS)
def test_skill_ships_byte_identical_test_lock_copies(skill):
    source = TEST_LOCK_SOURCE.read_bytes()

    assert (ROOT / "plugin" / "skills" / skill / "scripts" / "test_lock.py").read_bytes() == source
    assert (ROOT / "src" / "monolynx" / "static" / "skills" / f"monolynx-{skill}" / "scripts" / "test_lock.py").read_bytes() == source
