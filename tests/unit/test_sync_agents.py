"""Testy skryptu scripts/sync_agents.py (MON-184, punkt 4 zakresu).

Skrypt jest zaimplementowany. Import na poziomie modulu (analogicznie do
tests/unit/test_sync_skills.py) rzuca FileNotFoundError, jesli
scripts/sync_agents.py kiedykolwiek zniknie z repo -- czytelny fail-fast
zamiast tracebacku w pierwszym tescie.

Kontrakt (ustalony przez Team Managera, patrz brief zadania):
- Zrodlo tresci rol: plugin/agents/*.md (jedyne zrodlo prawdy).
- Cel: .claude/agents/*.md (kopia dla Claude Code, plaska struktura -- w
  odroznieniu od scripts/sync_skills.py agenci to pojedyncze pliki .md,
  bez zagniezdzonych katalogow per-agent).
- Bez argumentow: kopiuje kazdy plik zrodlowy do celu (nadpisujac
  rozjezdzajace sie kopie, tworzac brakujace), wypisuje podsumowanie na
  stdout, exit 0.
- `--check`: NIE modyfikuje niczego. Exit 1 i wypisanie na stdout/stderr
  nazw rozjezdzajacych sie plikow, jesli kopia rozni sie od zrodla albo jej
  brakuje. Exit 0, gdy .claude/agents/ jest identyczne ze plugin/agents/.

Decyzja qa-testera (do potwierdzenia przez Team Managera/backend-developera,
patrz tez raport koncowy): plik w .claude/agents/ bez odpowiednika o tej
samej nazwie w plugin/agents/ ("osierocony") jest traktowany tak samo jak
rozjazd tresci -- `--check` zglasza go i konczy sie exit 1, zwykle
wywolanie go usuwa. Uzasadnienie: .claude/agents/ ma byc WYLACZNIE kopia
plugin/agents/ (kontrakt brief: "jedyne zrodlo tresci rol"), wiec plik bez
zrodla jest tak samo rozjazdem jak plik nieaktualny -- dokladna analogia do
sekcji "orphans" w scripts/sync_skills.py (ta sama konwencja w repo, patrz
tests/unit/test_sync_skills.py::test_sync_check_detects_nested_drift_and_sync_removes_stale_resource).
Jesli backend-developer uzna, ze .claude/agents/ moze legalnie zawierac
pliki spoza plugin/agents/ (np. agent lokalny dla projektu, nie z pluginu),
te testy trzeba bedzie swiadomie zmienic -- nie jest to zalozenie z briefu,
tylko wybor qa-testera zgodny z istniejaca konwencja repo.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "sync_agents.py"
SPEC = importlib.util.spec_from_file_location("sync_agents_under_test", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
sync_agents = importlib.util.module_from_spec(SPEC)
# Przy braku pliku -> FileNotFoundError (fail-fast, patrz docstring modulu)
SPEC.loader.exec_module(sync_agents)

pytestmark = pytest.mark.unit


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """Podmienia SOURCE_DIR/TARGET_DIR skryptu na katalogi tymczasowe --
    testy nigdy nie dotykaja prawdziwych plugin/agents/ ani .claude/agents/."""
    source = tmp_path / "plugin_agents"
    target = tmp_path / "claude_agents"
    source.mkdir()
    target.mkdir()
    monkeypatch.setattr(sync_agents, "SOURCE_DIR", source)
    monkeypatch.setattr(sync_agents, "TARGET_DIR", target)
    return source, target


def write(path: Path, name: str, content: str) -> Path:
    file_path = path / name
    file_path.write_text(content, encoding="utf-8")
    return file_path


def run_main(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", argv)
    return sync_agents.main()


@pytest.mark.unit
class TestSyncCreatesMissingCopy:
    def test_sync_creates_missing_target_file(self, dirs, monkeypatch):
        source, target = dirs
        write(source, "qa-tester.md", "---\nname: qa-tester\n---\nTresc roli QA\n")
        assert not (target / "qa-tester.md").exists()

        exit_code = run_main(["sync_agents.py"], monkeypatch)

        assert exit_code == 0
        assert (target / "qa-tester.md").read_text(encoding="utf-8") == "---\nname: qa-tester\n---\nTresc roli QA\n"

    def test_sync_creates_multiple_missing_files(self, dirs, monkeypatch):
        source, target = dirs
        write(source, "backend-developer.md", "Backend content\n")
        write(source, "code-reviewer.md", "Reviewer content\n")

        exit_code = run_main(["sync_agents.py"], monkeypatch)

        assert exit_code == 0
        assert (target / "backend-developer.md").read_text(encoding="utf-8") == "Backend content\n"
        assert (target / "code-reviewer.md").read_text(encoding="utf-8") == "Reviewer content\n"


@pytest.mark.unit
class TestSyncOverwritesStaleCopy:
    def test_sync_overwrites_content_that_diverged_from_source(self, dirs, monkeypatch):
        source, target = dirs
        write(source, "qa-tester.md", "Nowa wersja tresci\n")
        write(target, "qa-tester.md", "Stara, rozjezdzajaca sie tresc\n")

        exit_code = run_main(["sync_agents.py"], monkeypatch)

        assert exit_code == 0
        assert (target / "qa-tester.md").read_text(encoding="utf-8") == "Nowa wersja tresci\n"

    def test_sync_leaves_identical_file_untouched(self, dirs, monkeypatch):
        """Plik juz zgodny nie powinien byc niepotrzebnie przepisywany -- test
        posrednio: po synchronizacji zawartosc pozostaje identyczna (nie
        sprawdzamy mtime, bo backend-developer moze zaimplementowac kopiowanie
        bezwarunkowe; wazny jest wynik, nie mechanizm)."""
        source, target = dirs
        content = "Zgodna tresc\n"
        write(source, "qa-tester.md", content)
        write(target, "qa-tester.md", content)

        exit_code = run_main(["sync_agents.py"], monkeypatch)

        assert exit_code == 0
        assert (target / "qa-tester.md").read_text(encoding="utf-8") == content


@pytest.mark.unit
class TestCheckMode:
    def test_check_on_matching_state_exits_zero_and_writes_nothing(self, dirs, monkeypatch):
        source, target = dirs
        content = "Zgodna tresc\n"
        write(source, "qa-tester.md", content)
        write(target, "qa-tester.md", content)

        exit_code = run_main(["sync_agents.py", "--check"], monkeypatch)

        assert exit_code == 0

    def test_check_on_content_drift_exits_one_and_names_the_file(self, dirs, monkeypatch, capsys):
        source, target = dirs
        write(source, "qa-tester.md", "Nowa wersja\n")
        write(target, "qa-tester.md", "Stara wersja\n")

        exit_code = run_main(["sync_agents.py", "--check"], monkeypatch)
        output = capsys.readouterr()

        assert exit_code == 1
        assert "qa-tester" in (output.out + output.err)
        # --check nigdy nie modyfikuje plikow
        assert (target / "qa-tester.md").read_text(encoding="utf-8") == "Stara wersja\n"

    def test_check_on_missing_copy_exits_one_and_names_the_file(self, dirs, monkeypatch, capsys):
        source, target = dirs
        write(source, "devops-infra.md", "Tresc\n")

        exit_code = run_main(["sync_agents.py", "--check"], monkeypatch)
        output = capsys.readouterr()

        assert exit_code == 1
        assert "devops-infra" in (output.out + output.err)
        assert not (target / "devops-infra.md").exists()

    def test_check_reports_every_drifted_file_not_just_the_first(self, dirs, monkeypatch, capsys):
        source, target = dirs
        write(source, "qa-tester.md", "Nowa\n")
        write(target, "qa-tester.md", "Stara\n")
        write(source, "code-reviewer.md", "Tresc\n")  # brakuje w target

        exit_code = run_main(["sync_agents.py", "--check"], monkeypatch)
        output = capsys.readouterr()
        combined = output.out + output.err

        assert exit_code == 1
        assert "qa-tester" in combined
        assert "code-reviewer" in combined


@pytest.mark.unit
class TestOrphanFileInTarget:
    """Plik w .claude/agents/ bez odpowiednika w plugin/agents/ -- decyzja
    qa-testera udokumentowana w docstringu modulu: traktowany jak rozjazd."""

    def test_check_reports_orphan_file_as_drift(self, dirs, monkeypatch, capsys):
        source, target = dirs
        write(source, "qa-tester.md", "Tresc\n")
        write(target, "qa-tester.md", "Tresc\n")
        write(target, "usuniety-agent.md", "Nieaktualna rola, nie ma juz zrodla\n")

        exit_code = run_main(["sync_agents.py", "--check"], monkeypatch)
        output = capsys.readouterr()

        assert exit_code == 1
        assert "usuniety-agent" in (output.out + output.err)

    def test_sync_removes_orphan_file_from_target(self, dirs, monkeypatch):
        source, target = dirs
        write(source, "qa-tester.md", "Tresc\n")
        write(target, "qa-tester.md", "Tresc\n")
        orphan = write(target, "usuniety-agent.md", "Nieaktualna rola\n")
        assert orphan.exists()

        exit_code = run_main(["sync_agents.py"], monkeypatch)

        assert exit_code == 0
        assert not orphan.exists()

    def test_check_on_fully_matching_state_with_no_orphans_exits_zero(self, dirs, monkeypatch):
        source, target = dirs
        write(source, "qa-tester.md", "Tresc\n")
        write(target, "qa-tester.md", "Tresc\n")

        exit_code = run_main(["sync_agents.py", "--check"], monkeypatch)

        assert exit_code == 0


@pytest.mark.unit
class TestNonMarkdownFilesInSourceDirAreIgnoredOrCopiedConsistently:
    """plugin/agents/ moze w przyszlosci zawierac pliki pomocnicze (np. README).
    Ten test pilnuje tylko, ze obecnosc pliku spoza *.md w zrodle nie wywraca
    skryptu -- nie zaklada konkretnego zachowania kopiowania dla innych
    rozszerzen (poza zakresem kontraktu z briefu, ktory mowi wprost o *.md)."""

    def test_extra_non_markdown_file_in_source_does_not_crash_sync(self, dirs, monkeypatch):
        source, target = dirs
        write(source, "qa-tester.md", "Tresc\n")
        write(source, "README.txt", "Nie jest agentem\n")

        exit_code = run_main(["sync_agents.py"], monkeypatch)

        assert exit_code == 0
        assert (target / "qa-tester.md").read_text(encoding="utf-8") == "Tresc\n"

    def test_extra_non_markdown_file_in_source_does_not_crash_check(self, dirs, monkeypatch):
        source, target = dirs
        write(source, "qa-tester.md", "Tresc\n")
        write(target, "qa-tester.md", "Tresc\n")
        write(source, "README.txt", "Nie jest agentem\n")

        exit_code = run_main(["sync_agents.py", "--check"], monkeypatch)

        assert exit_code == 0


@pytest.mark.unit
class TestMissingSourceDir:
    def test_missing_source_dir_fails_loudly_instead_of_silently_wiping_target(self, tmp_path, monkeypatch):
        """Brak plugin/agents/ (np. bledna konfiguracja sciezki) nie moze
        skonczyc sie cichym usunieciem calej zawartosci .claude/agents/ --
        to bylaby utrata tresci rol bez ostrzezenia. Oczekiwane: blad wejscia
        (exit != 0), target pozostaje nietkniety."""
        source = tmp_path / "brak_takiego_katalogu"
        target = tmp_path / "claude_agents"
        target.mkdir()
        write(target, "qa-tester.md", "Istniejaca tresc\n")
        monkeypatch.setattr(sync_agents, "SOURCE_DIR", source)
        monkeypatch.setattr(sync_agents, "TARGET_DIR", target)

        exit_code = run_main(["sync_agents.py"], monkeypatch)

        assert exit_code != 0
        assert (target / "qa-tester.md").read_text(encoding="utf-8") == "Istniejaca tresc\n"


@pytest.mark.unit
def test_committed_target_matches_committed_source():
    """Integracyjna kontrola spojnosci: prawdziwe .claude/agents/ w repo ma
    byc bajt-w-bajt identyczne z plugin/agents/ dla kazdego pliku *.md, po
    uruchomieniu synchronizacji przez backend-developera. Strzeze przed
    ponownym rozjazdem (ktory ten test wykryl na starcie ticketu: `diff`
    pokazywal roznice dla wszystkich 7 plikow, zanim skrypt zostal
    uruchomiony) -- regresja, nie teoretyczne ryzyko.

    Obraz `:dev` kopiuje src/, tests/, sdk/, plugin/ i scripts/, ale NIE
    `.claude/` (Dockerfile:37-41), wiec w kontenerze CI katalog docelowy nie
    istnieje i porownanie nie ma czego sprawdzic. Egzekwowanie tej samej
    spojnosci w CI robi job `plugin-consistency`, ktory chodzi po pelnym
    checkoucie: `python3 scripts/sync_agents.py --check`."""
    source_dir = ROOT / "plugin" / "agents"
    target_dir = ROOT / ".claude" / "agents"
    if not target_dir.is_dir():
        pytest.skip(".claude/agents/ nie jest dostarczane do obrazu - spojnosci pilnuje job plugin-consistency")
    source_names = {p.name for p in source_dir.glob("*.md")}
    target_names = {p.name for p in target_dir.glob("*.md")}
    assert source_names == target_names, "zestaw plikow .md musi byc identyczny (brak sierot/brakow)"
    mismatched = [
        name for name in sorted(source_names) if (source_dir / name).read_text(encoding="utf-8") != (target_dir / name).read_text(encoding="utf-8")
    ]
    assert mismatched == [], f"pliki rozjezdzajace sie od zrodla: {mismatched}"
