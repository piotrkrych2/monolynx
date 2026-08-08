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
