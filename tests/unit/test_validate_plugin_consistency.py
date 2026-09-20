"""Testy walidatora spojnosci pluginu: scripts/validate_plugin_consistency.py.

TDD: plik walidatora jeszcze NIE ISTNIEJE w chwili pisania tych testow.
Te testy SA specyfikacja jego kontraktu dla backend-developera:

    check_step_refs(files: dict[str, str]) -> list[str]
    check_em_dash(files: dict[str, str]) -> list[str]
    check_command_names(files: dict[str, str]) -> list[str]
    check_version_matches_changelog(plugin_json: str, readme: str) -> list[str]
    main(argv: list[str] | None = None) -> int   # 0 OK, 1 naruszenia, 2 blad konfiguracji

Dopoki modul nie istnieje, cala klasa testow jest skipowana (pytest.skip na
poziomie modulu) -- czerwone/skipniete do czasu implementacji, potem zielone.

Kontrakt CLI dla main() uzgodniony na potrzeby tych testow (deterministyczny,
bez dotykania prawdziwych plikow repo):
    --plugin-json PATH   sciezka do plugin.json
    --readme PATH        sciezka do README.md (sekcja '## Changelog')
    --skills-dir PATH    katalog z plikami .md skanowanymi przez pozostale checki
Brakujacy/niepoprawny plik pod ktoraz ze sciezek -> main() zwraca 2.
Sekcja '## Changelog' nieobecna w README -> main() zwraca 2 (blad konfiguracji,
nie tresciowe naruszenie -- to zaklada check_version_matches_changelog).
"""

import importlib.util
import json
from pathlib import Path

import pytest

VALIDATOR_PATH = Path(__file__).resolve().parents[2] / "scripts" / "validate_plugin_consistency.py"

if not VALIDATOR_PATH.exists():
    pytest.skip("walidator jeszcze nie zaimplementowany (TDD)", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("validate_plugin_consistency", VALIDATOR_PATH)
assert _spec is not None and _spec.loader is not None
validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validator)


# ---------------------------------------------------------------------------
# check_step_refs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckStepRefs:
    def test_dead_krok_reference_is_a_violation(self):
        files = {"skill.md": "Zobacz KROK 99 w dalszej czesci opisu."}
        violations = validator.check_step_refs(files)
        assert len(violations) == 1
        assert any("KROK 99" in v for v in violations)
        assert any("skill.md" in v for v in violations)

    def test_live_krok_reference_no_violation(self):
        files = {
            "pipeline.md": "## KROK 1: Research\nOpis kroku pierwszego.",
            "skill.md": "Patrz KROK 1 po szczegoly.",
        }
        assert validator.check_step_refs(files) == []

    def test_dead_faza_reference_is_a_violation(self):
        files = {"skill.md": "W FAZA 7 robimy X (ta faza nie istnieje)."}
        violations = validator.check_step_refs(files)
        assert len(violations) == 1
        assert any("FAZA 7" in v for v in violations)

    def test_live_faza_reference_no_violation(self):
        files = {
            "pipeline.md": "## FAZA 2: Coding\nSzczegoly.",
            "skill.md": "Patrz FAZA 2.",
        }
        assert validator.check_step_refs(files) == []

    def test_dead_sekcja_pipeline_reference_missing_file(self):
        files = {"doc.md": "Zobacz sekcja 7 pipeline.md po wiecej informacji."}
        violations = validator.check_step_refs(files)
        assert len(violations) == 1
        assert any("sekcja 7" in v.lower() for v in violations)

    def test_live_sekcja_pipeline_reference_no_violation(self):
        files = {
            "pipeline.md": "## 3. Coding\nSzczegoly kroku.",
            "doc.md": "Zobacz sekcja 3 pipeline.md.",
        }
        assert validator.check_step_refs(files) == []

    def test_clean_file_no_references_no_violation(self):
        files = {"readme.md": "Zwykly tekst bez zadnych referencji do krokow."}
        assert validator.check_step_refs(files) == []

    def test_empty_files_dict_no_violation(self):
        assert validator.check_step_refs({}) == []


# ---------------------------------------------------------------------------
# check_em_dash
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckEmDash:
    def test_em_dash_is_a_violation(self):
        files = {"doc.md": "To jest tekst — z em-dashem w srodku."}
        violations = validator.check_em_dash(files)
        assert len(violations) == 1
        assert any("doc.md" in v for v in violations)

    def test_en_dash_is_a_violation(self):
        files = {"doc.md": "Zakres stron 1\u20135 w dokumencie."}
        violations = validator.check_em_dash(files)
        assert len(violations) == 1
        assert any("doc.md" in v for v in violations)

    def test_clean_file_with_hyphen_no_violation(self):
        files = {"doc.md": "To jest tekst - z normalnym hyphenem, bez em-dash ani en-dash."}
        assert validator.check_em_dash(files) == []

    def test_multiple_files_reports_violation_per_file(self):
        files = {
            "a.md": "Tekst z — em-dashem.",
            "b.md": "Czysty tekst bez niczego.",
            "c.md": "Kolejny — em-dash tutaj.",
        }
        violations = validator.check_em_dash(files)
        assert len(violations) == 2
        assert any("a.md" in v for v in violations)
        assert any("c.md" in v for v in violations)
        assert not any("b.md" in v for v in violations)

    def test_empty_files_dict_no_violation(self):
        assert validator.check_em_dash({}) == []


# ---------------------------------------------------------------------------
# check_command_names
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckCommandNames:
    def test_hyphenated_slash_command_is_a_violation(self):
        files = {"doc.md": "Uzyj komendy /monolynx-work zeby zaczac prace."}
        violations = validator.check_command_names(files)
        assert len(violations) == 1
        assert any("/monolynx-work" in v for v in violations)

    def test_hyphenated_slash_command_work_simple_is_a_violation(self):
        files = {"doc.md": "Dla prostych ticketow uzyj /monolynx-work-simple."}
        violations = validator.check_command_names(files)
        assert len(violations) == 1
        assert any("/monolynx-work-simple" in v for v in violations)

    def test_correct_colon_format_no_violation(self):
        files = {"doc.md": "Uzyj /monolynx:work zeby rozpoczac prace nad ticketem."}
        assert validator.check_command_names(files) == []

    def test_monolynx_work_in_install_skills_context_whitelisted(self):
        files = {
            "doc.md": ("install_monolynx_skills instaluje kopie skilli, m.in. monolynx-work i monolynx-work-simple, do reczniejszej instalacji.")
        }
        assert validator.check_command_names(files) == []

    def test_clean_file_no_violation(self):
        files = {"doc.md": "Ten plik nie wspomina zadnych komend."}
        assert validator.check_command_names(files) == []

    def test_empty_files_dict_no_violation(self):
        assert validator.check_command_names({}) == []


# ---------------------------------------------------------------------------
# check_version_matches_changelog
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckVersionMatchesChangelog:
    def test_matching_version_no_violation(self):
        plugin_json = json.dumps({"name": "monolynx", "version": "1.5.0"})
        readme = "# README\n\n## Changelog\n\n### 1.5.0\n- opis zmian\n\n### 1.4.0\n- starsze zmiany\n"
        assert validator.check_version_matches_changelog(plugin_json, readme) == []

    def test_mismatched_version_is_a_violation(self):
        plugin_json = json.dumps({"name": "monolynx", "version": "1.6.0"})
        readme = "# README\n\n## Changelog\n\n### 1.5.0\n- opis zmian\n\n### 1.4.0\n- starsze zmiany\n"
        violations = validator.check_version_matches_changelog(plugin_json, readme)
        assert len(violations) == 1
        assert "1.6.0" in violations[0]
        assert "1.5.0" in violations[0]

    def test_ignores_changelog_entries_before_the_changelog_heading(self):
        """Wersja w naglowku np. '### 1.9.9' PRZED '## Changelog' nie liczy sie jako wpis."""
        plugin_json = json.dumps({"name": "monolynx", "version": "1.5.0"})
        readme = "# README\n\n### 1.9.9\n- to nie jest wpis changeloga\n\n## Changelog\n\n### 1.5.0\n- opis\n"
        assert validator.check_version_matches_changelog(plugin_json, readme) == []


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMainCli:
    @staticmethod
    def _write_ok_fixture(tmp_path):
        plugin_json = tmp_path / "plugin.json"
        plugin_json.write_text(json.dumps({"name": "monolynx", "version": "1.5.0"}), encoding="utf-8")

        readme = tmp_path / "README.md"
        readme.write_text(
            "# README\n\n## Changelog\n\n### 1.5.0\n- connector MCP\n\n### 1.4.0\n- starsze\n",
            encoding="utf-8",
        )

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "work.md").write_text(
            "Uzyj /monolynx:work zeby rozpoczac. Patrz KROK 1.\n\n## KROK 1: Start\nOpis.",
            encoding="utf-8",
        )
        return plugin_json, readme, skills_dir

    def test_all_checks_clean_returns_zero(self, tmp_path):
        plugin_json, readme, skills_dir = self._write_ok_fixture(tmp_path)
        argv = [
            "--plugin-json",
            str(plugin_json),
            "--readme",
            str(readme),
            "--skills-dir",
            str(skills_dir),
        ]
        assert validator.main(argv) == 0

    def test_content_violation_returns_one(self, tmp_path):
        plugin_json, readme, skills_dir = self._write_ok_fixture(tmp_path)
        (skills_dir / "broken.md").write_text("Tekst z — em-dashem, ktory jest zabroniony.", encoding="utf-8")
        argv = [
            "--plugin-json",
            str(plugin_json),
            "--readme",
            str(readme),
            "--skills-dir",
            str(skills_dir),
        ]
        assert validator.main(argv) == 1

    def test_missing_changelog_section_returns_two(self, tmp_path):
        plugin_json, _, skills_dir = self._write_ok_fixture(tmp_path)
        readme_no_changelog = tmp_path / "README_no_changelog.md"
        readme_no_changelog.write_text("# README\n\nBrak sekcji changeloga w tym pliku.\n", encoding="utf-8")
        argv = [
            "--plugin-json",
            str(plugin_json),
            "--readme",
            str(readme_no_changelog),
            "--skills-dir",
            str(skills_dir),
        ]
        assert validator.main(argv) == 2

    def test_missing_plugin_json_file_returns_two(self, tmp_path):
        _, readme, skills_dir = self._write_ok_fixture(tmp_path)
        argv = [
            "--plugin-json",
            str(tmp_path / "does-not-exist.json"),
            "--readme",
            str(readme),
            "--skills-dir",
            str(skills_dir),
        ]
        assert validator.main(argv) == 2


# ---------------------------------------------------------------------------
# check_skill_coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckSkillCoverage:
    def test_skill_missing_on_one_page_is_a_violation(self):
        pages = {
            "README.md": "| `/monolynx:work` | ... |\n| `/monolynx:retro` | ... |",
            "help.md": "### /monolynx:work\n",
        }
        violations = validator.check_skill_coverage(["work", "retro"], pages)
        assert violations == ["help.md: brak skilla '/monolynx:retro' - kazdy skill z plugin/skills/ ma tu byc opisany"]

    def test_all_skills_on_all_pages_no_violation(self):
        pages = {"a.md": "/monolynx:work /monolynx:work-simple", "b.html": "<code>/monolynx:work-simple</code> /monolynx:work"}
        assert validator.check_skill_coverage(["work", "work-simple"], pages) == []

    def test_prefix_of_longer_name_does_not_count(self):
        pages = {"a.md": "tylko /monolynx:work-simple tutaj"}
        violations = validator.check_skill_coverage(["work"], pages)
        assert len(violations) == 1
        assert "/monolynx:work'" in violations[0]

    def test_empty_skill_list_no_violation(self):
        assert validator.check_skill_coverage([], {"a.md": ""}) == []


@pytest.mark.unit
class TestMainCliSkillCoverage:
    @staticmethod
    def _write_fixture(tmp_path, page_text):
        plugin_json = tmp_path / "plugin.json"
        plugin_json.write_text(json.dumps({"name": "monolynx", "version": "1.5.0"}), encoding="utf-8")
        readme = tmp_path / "README.md"
        readme.write_text("# README\n\n## Changelog\n\n### 1.5.0\n- x\n", encoding="utf-8")
        skills_dir = tmp_path / "plugin"
        (skills_dir / "skills" / "work").mkdir(parents=True)
        (skills_dir / "skills" / "work" / "SKILL.md").write_text("---\ndescription: x\n---\n# work\n", encoding="utf-8")
        (skills_dir / "skills" / "retro").mkdir()
        (skills_dir / "skills" / "retro" / "SKILL.md").write_text("---\ndescription: y\n---\n# retro\n", encoding="utf-8")
        contract = (VALIDATOR_PATH.parent.parent / "plugin/references/runtime.md").read_text(encoding="utf-8")
        for name in ("work", "retro"):
            skill = skills_dir / "skills" / name
            entrypoint = skill / "SKILL.md"
            entrypoint.write_text(entrypoint.read_text(encoding="utf-8") + "\n[runtime](references/runtime.md)\n", encoding="utf-8")
            (skill / "references").mkdir()
            (skill / "references/runtime.md").write_text(contract, encoding="utf-8")
        page = tmp_path / "guide.html"
        page.write_text(page_text, encoding="utf-8")
        return plugin_json, readme, skills_dir, page

    def _argv(self, plugin_json, readme, skills_dir, page):
        return [
            "--plugin-json",
            str(plugin_json),
            "--readme",
            str(readme),
            "--skills-dir",
            str(skills_dir),
            "--skills-root",
            str(skills_dir / "skills"),
            "--page",
            str(page),
        ]

    def test_page_with_all_skills_returns_zero(self, tmp_path):
        fx = self._write_fixture(tmp_path, "<code>/monolynx:work</code> <code>/monolynx:retro</code>")
        assert validator.main(self._argv(*fx)) == 0

    def test_missing_runtime_fails_cli_even_when_coverage_is_complete(self, tmp_path, capsys):
        fx = self._write_fixture(tmp_path, "/monolynx:work /monolynx:retro")
        (fx[2] / "skills/work/references/runtime.md").unlink()
        assert validator.main(self._argv(*fx)) == 1
        assert "runtime.md" in capsys.readouterr().out

    def test_page_missing_a_skill_returns_one(self, tmp_path, capsys):
        fx = self._write_fixture(tmp_path, "<code>/monolynx:work</code>")
        assert validator.main(self._argv(*fx)) == 1
        assert "/monolynx:retro" in capsys.readouterr().out

    def test_custom_skills_dir_without_pages_skips_coverage(self, tmp_path):
        plugin_json, readme, skills_dir, _ = self._write_fixture(tmp_path, "")
        argv = ["--plugin-json", str(plugin_json), "--readme", str(readme), "--skills-dir", str(skills_dir)]
        assert validator.main(argv) == 0

    def test_missing_page_file_returns_two(self, tmp_path):
        plugin_json, readme, skills_dir, _ = self._write_fixture(tmp_path, "")
        argv = self._argv(plugin_json, readme, skills_dir, tmp_path / "nope.html")
        assert validator.main(argv) == 2


@pytest.mark.unit
class TestRuntimeContract:
    @staticmethod
    def files(body=""):
        contract = (VALIDATOR_PATH.parent.parent / "plugin/references/runtime.md").read_text(encoding="utf-8")
        return {
            "skills/sample/SKILL.md": "Przeczytaj [runtime](references/runtime.md).\n" + body,
            "skills/sample/references/runtime.md": contract,
            "references/runtime.md": contract,
        }

    def test_local_complete_contract_is_valid(self):
        assert validator.check_runtime_contract(self.files()) == []

    def test_missing_contract_is_reported(self):
        files = self.files()
        del files["skills/sample/references/runtime.md"]
        assert any("runtime.md" in item for item in validator.check_runtime_contract(files))

    def test_unlinked_contract_is_reported(self):
        files = self.files()
        files["skills/sample/SKILL.md"] = "Opis bez kontraktu."
        assert validator.check_runtime_contract(files)

    def test_truncated_contract_is_reported_without_canonical_source(self):
        files = self.files()
        del files["references/runtime.md"]
        files["skills/sample/references/runtime.md"] = "# Runtime\n## Projekt\n"
        assert validator.check_runtime_contract(files)

    def test_stale_contract_is_reported(self):
        files = self.files()
        files["skills/sample/references/runtime.md"] += "Stara instrukcja.\n"
        assert validator.check_runtime_contract(files)

    @pytest.mark.parametrize("reference", ["../other/references/context.md", "/tmp/context.md", "references/missing.md"])
    def test_external_or_missing_reference_is_reported(self, reference):
        assert validator.check_runtime_contract(self.files(f"Czytaj [kontekst]({reference})."))

    def test_local_reference_is_valid(self):
        files = self.files("Czytaj [kontekst](references/context.md).")
        files["skills/sample/references/context.md"] = "Kontekst."
        assert validator.check_runtime_contract(files) == []

    def test_project_paths_in_code_are_not_bundled_references(self):
        body = "Odczytaj `.claude/rules/db-commit.md` i `src/docs/guide.md`.\n"
        assert validator.check_runtime_contract(self.files(body)) == []

    def test_nested_reference_resolves_from_skill_root(self):
        files = self.files("Czytaj `references/stages.md`.\n")
        files["skills/sample/references/stages.md"] = "Czytaj `references/context.md`.\n"
        files["skills/sample/references/context.md"] = "Kontekst."
        assert validator.check_runtime_contract(files) == []
        del files["skills/sample/references/context.md"]
        assert validator.check_runtime_contract(files)

    @pytest.mark.parametrize(
        "instruction",
        ["ToolSearch(query='x')", "Skill(skill='work')", "AskUserQuestion(...)", "Weź $ARGUMENTS.", "Czytaj ${CLAUDE_PLUGIN_ROOT}/x."],
    )
    def test_unconditional_client_instruction_is_reported(self, instruction):
        assert validator.check_runtime_contract(self.files(instruction))

    def test_explicit_claude_variant_is_valid_but_does_not_leak(self):
        body = "<!-- runtime:claude-code -->\nToolSearch(query='x')\n<!-- /runtime:claude-code -->\n"
        assert validator.check_runtime_contract(self.files(body)) == []
        assert validator.check_runtime_contract(self.files(body + "AskUserQuestion(...)\n"))

    def test_explanations_of_fallback_and_negation_are_valid(self):
        body = "ToolSearch jest opcjonalne. Nie używaj literalnego `$ARGUMENTS`.\n"
        assert validator.check_runtime_contract(self.files(body)) == []

    def test_unclosed_variant_is_reported(self):
        assert validator.check_runtime_contract(self.files("<!-- runtime:claude-code -->\nToolSearch(query='x')"))


# ---------------------------------------------------------------------------
# check_shared_blocks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckSharedBlocks:
    """Kontrakt (ustalony przez Team Managera dla MON-189):

    check_shared_blocks(files: dict[str, str]) -> list[str]

    Dwie formy znacznika:
    - blokowa: `<!-- shared:<nazwa> -->` / `<!-- /shared:<nazwa> -->`, kazdy
      w osobnej linii; tresc to linie pomiedzy nimi.
    - liniowa: `<!-- shared:<nazwa> -->` na koncu linii z trescia; tresc to
      ta jedna linia z usunietym znacznikiem (potrzebne w wierszach tabel
      markdown, bo samodzielna linia komentarza konczy tabele GFM).

    Normalizacja przed porownaniem: zwiniecie ciagow bialych znakow do
    pojedynczej spacji + strip. Nic wiecej - zadnego fuzzy matchingu.
    """

    @staticmethod
    def pair(block_a: str, block_b: str, name_a: str = "work.md", name_b: str = "work-simple.md") -> dict[str, str]:
        """Buduje dwuplikowy slownik jak przy work.md / work-simple.md."""
        return {name_a: block_a, name_b: block_b}

    # -- zgodnosc ------------------------------------------------------

    def test_identical_shared_block_content_no_violation(self):
        files = self.pair(
            "przed\n<!-- shared:slug -->\nTresc wspolna.\n<!-- /shared:slug -->\npo\n",
            "co innego\n<!-- shared:slug -->\nTresc wspolna.\n<!-- /shared:slug -->\nkoniec\n",
        )
        assert validator.check_shared_blocks(files) == []

    def test_content_matches_despite_different_line_wrapping_and_indentation(self):
        """Cala uzgodniona normalizacja: zwiniecie bialych znakow, nic wiecej."""
        files = self.pair(
            "<!-- shared:slug -->\nKrok pierwszy.\nKrok drugi.\n<!-- /shared:slug -->\n",
            "<!-- shared:slug -->\n   Krok pierwszy. Krok   drugi.   \n<!-- /shared:slug -->\n",
        )
        assert validator.check_shared_blocks(files) == []

    def test_no_shared_markers_at_all_no_violation(self):
        """Check nie moze alarmowac na plikach bez zadnych znacznikow shared."""
        files = self.pair(
            "Zwykly tekst bez zadnych znacznikow shared.\n",
            "Inny zwykly tekst, tez bez znacznikow.\n",
        )
        assert validator.check_shared_blocks(files) == []

    def test_empty_files_dict_no_violation(self):
        assert validator.check_shared_blocks({}) == []

    # -- rozjazd semantyczny --------------------------------------------

    def test_semantic_drift_between_two_files_is_a_violation(self):
        files = self.pair(
            "<!-- shared:slug -->\nWykonaj krok pierwszy.\n<!-- /shared:slug -->\n",
            "<!-- shared:slug -->\nWykonaj krok drugi.\n<!-- /shared:slug -->\n",
        )
        violations = validator.check_shared_blocks(files)
        assert violations
        combined = "\n".join(violations)
        assert "slug" in combined
        assert "work.md" in combined
        assert "work-simple.md" in combined

    # -- brak pary --------------------------------------------------------

    def test_name_present_in_only_one_file_is_a_violation(self):
        files = self.pair(
            "<!-- shared:only-here -->\nTresc.\n<!-- /shared:only-here -->\n",
            "Nic tu nie ma.\n",
        )
        violations = validator.check_shared_blocks(files)
        assert len(violations) == 1
        assert "only-here" in violations[0]
        assert "work.md" in violations[0]

    # -- wadliwe znaczniki --------------------------------------------------

    def test_unclosed_opening_marker_is_a_violation(self):
        files = {"work.md": "<!-- shared:slug -->\nTresc bez zamkniecia az do konca pliku.\n"}
        violations = validator.check_shared_blocks(files)
        assert violations
        assert any("slug" in v and "work.md" in v for v in violations)

    def test_closing_marker_without_opening_is_a_violation(self):
        files = {"work.md": "Tresc przed.\n<!-- /shared:slug -->\n"}
        violations = validator.check_shared_blocks(files)
        assert violations
        assert any("slug" in v and "work.md" in v for v in violations)

    def test_nested_shared_block_is_a_violation(self):
        nested = (
            "<!-- shared:outer -->\nTresc zewnetrzna.\n<!-- shared:inner -->\nTresc wewnetrzna.\n<!-- /shared:inner -->\n<!-- /shared:outer -->\n"
        )
        # ta sama tresc w obu plikach - izoluje naruszenie od checku "brak pary"
        files = self.pair(nested, nested)
        violations = validator.check_shared_blocks(files)
        assert violations
        assert any("work.md" in v for v in violations)

    def test_duplicate_name_in_the_same_file_is_a_violation(self):
        files = self.pair(
            "<!-- shared:slug -->\nA.\n<!-- /shared:slug -->\nTekst posredni.\n<!-- shared:slug -->\nB.\n<!-- /shared:slug -->\n",
            "<!-- shared:slug -->\nA.\n<!-- /shared:slug -->\n",
        )
        violations = validator.check_shared_blocks(files)
        assert violations
        assert any("slug" in v and "work.md" in v for v in violations)

    # -- forma liniowa (wiersze tabel markdown) ------------------------------

    def test_line_form_identical_content_no_violation(self):
        files = self.pair(
            "| `/monolynx:work` | Startuje prace nad ticketem. | <!-- shared:row-work -->\n",
            "| `/monolynx:work` | Startuje prace nad ticketem. | <!-- shared:row-work -->\n",
        )
        assert validator.check_shared_blocks(files) == []

    def test_line_form_different_content_is_a_violation(self):
        files = self.pair(
            "| `/monolynx:work` | Startuje prace nad ticketem. | <!-- shared:row-work -->\n",
            "| `/monolynx:work` | Startuje prace, prosty tryb. | <!-- shared:row-work -->\n",
        )
        violations = validator.check_shared_blocks(files)
        assert len(violations) == 1
        assert "row-work" in violations[0]
        assert "work.md" in violations[0]
        assert "work-simple.md" in violations[0]

    # -- koegzystencja ze znacznikami runtime:claude-code --------------------

    def test_runtime_block_inside_shared_is_not_nesting(self):
        block = (
            "<!-- shared:slug -->\n"
            "Tresc wspolna.\n"
            "<!-- runtime:claude-code -->\n"
            "Instrukcja specyficzna dla Claude Code.\n"
            "<!-- /runtime:claude-code -->\n"
            "<!-- /shared:slug -->\n"
        )
        files = self.pair(block, block)
        assert validator.check_shared_blocks(files) == []


# ---------------------------------------------------------------------------
# check_shared_blocks - integracja z main()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMainCliSharedBlocks:
    def test_shared_block_mismatch_fails_cli_and_reports_on_stdout(self, tmp_path, capsys):
        plugin_json = tmp_path / "plugin.json"
        plugin_json.write_text(json.dumps({"name": "monolynx", "version": "1.5.0"}), encoding="utf-8")

        readme = tmp_path / "README.md"
        readme.write_text(
            "# README\n\n## Changelog\n\n### 1.5.0\n- opis zmian\n",
            encoding="utf-8",
        )

        # tresc celowo bez KROK/FAZA/sekcja pipeline.md, em-dash ani
        # /monolynx-<nazwa>, zeby jedynym naruszeniem byl rozjazd shared:slug
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "work.md").write_text(
            "<!-- shared:slug -->\nWspolny fragment mowi X.\n<!-- /shared:slug -->\n",
            encoding="utf-8",
        )
        (skills_dir / "work-simple.md").write_text(
            "<!-- shared:slug -->\nWspolny fragment mowi Y.\n<!-- /shared:slug -->\n",
            encoding="utf-8",
        )

        argv = [
            "--plugin-json",
            str(plugin_json),
            "--readme",
            str(readme),
            "--skills-dir",
            str(skills_dir),
        ]
        assert validator.main(argv) == 1
        out = capsys.readouterr().out
        assert "slug" in out
