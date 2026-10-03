"""Testy plugin/scripts/monolynx_op.py - wrapper operacji MCP przez CLI `monolynx` (MON-232).

Kontrakt wrappera (stdlib, Python 3.10+):

- Wywolanie: `python3 monolynx_op.py <op> [--<param> <wartosc> ...]`; `<op>` to nazwa toola MCP,
  flagi to DOKLADNE nazwy parametrow toola MCP z podkresleniami (`--project_slug`, `--ticket_id`).
- Modul eksponuje: `OPS` (dict: nazwa op -> opis mapowania, struktura wewnetrzna dowolna),
  `READ_OPS` (set/frozenset nazw op tylko-odczyt, podzbior kluczy `OPS`, wylacznie nazwy
  zaczynajace sie od `get_`, `list_`, `search_`, `query_`, `find_`), `main(argv: list[str] | None = None) -> int`.
- CLI wolane DOKLADNIE jako `monolynx -o json --project <project_slug> <grupa> <komenda> ...`:
  pozycyjne argumenty zaraz po komendzie, potem flagi w kolejnosci wystepowania parametrow w
  wywolaniu wrappera. Wrapper przekazuje tylko podane parametry (zadnych wartosci domyslnych).
- Mapowanie parametrow (zob. tabela SCENARIOS nizej): `content` w `add_comment` -> `--body -`;
  `description` (create/update_ticket) -> `--description -`; `content` (create/update_wiki_page)
  -> `--content -`; w tych przypadkach tresc idzie na stdin CLI (nie w argv). `sprint_id` -> `--sprint`
  (w `complete_sprint`, `get_burndown`, `start_sprint`, `get_sprint` i `update_sprint` pozycyjnie;
  w `start_sprint`, `get_sprint`, `update_sprint` wymagany), `name` -> `--name`, `goal` -> `--goal`
  (zwykla opcja w argv, nie stdin: CLI nie przyjmuje `--goal -`; wartosc `-` albo `@plik` wrapper
  rozwija do tresci przed zbudowaniem argv), `start_date` -> `--start`, `end_date` -> `--end`
  (`create_sprint` wymaga `name` i `start_date`), `assignee_email` -> `--assignee`, `due_date` ->
  `--due`, `due_date_before|due_before` -> `--due-before`, `due_date_after|due_after` -> `--due-after`,
  `story_points` -> `--sp`, `label_id` -> `--label`, `spec_page_id` -> `--spec-page`, `parent_id` ->
  `--parent`, `acceptance_criteria` -> powtarzane `--ac`, `label_ids` -> powtarzane `--label`,
  `blocked_by_ids` -> powtarzane `--blocked-by` (pusta lista -> `--no-blockers`), `overdue=true` ->
  `--overdue` (false -> brak flagi), `compact=true` (`list_tickets`) -> `--compact` i `brief=true`
  (`create_ticket`, `update_ticket`) -> `--brief` (false -> brak flagi; to parametry wrappera, nie toola
  MCP), `is_completed` -> `--done` / `--not-done`, `is_public` -> `--public` / `--no-public`.
  Graf (MON-248, tylko odczyty): `list_graph_nodes` -> `graph nodes` z `type|search|limit` -> `--type|--search|--limit`,
  `get_graph_node` -> `graph node <node_id>` z `depth` -> `--depth`, `relation_types` -> powtarzane `--relation-type`,
  `node_types` -> powtarzane `--node-type`, `query_graph` -> `graph query` z `node_type` -> `--node-type`,
  `find_graph_path` -> `graph path` z `source_id|target_id` -> `--source-id|--target-id`, `get_graph_stats` -> `graph stats`;
  narzedzia zapisu grafu (w tym `replace_graph`) nie maja komendy CLI i zawsze daja fallback.
  Listy w jednej wartosci jako tablica JSON.
- Wartosc parametru `-` = czytaj ze stdin wrappera, `@<sciezka>` = czytaj z pliku (UTF-8).
- Kody: 0 (stdout = stdout CLI bez zmian); 3 + ostatnia linia stdout `OP-FALLBACK: mcp tool=<op>`
  (powod na stderr) gdy: op bez komendy CLI albo nieznana, `MONOLYNX_TRANSPORT=mcp`, brak `monolynx`
  w PATH, CLI zwrocilo 3 albo 2, parametr bez odpowiednika w CLI (wtedy CLI nie jest wolane);
  2 gdy CLI zwrocilo 1 albo 4 (stderr CLI przekazany, stdout pusty, bez OP-FALLBACK);
  64 przy bledzie uzycia (brak op, flaga bez wartosci, zly JSON listy, zla wartosc bool,
  stray argument pozycyjny, brak pliku `@sciezka`, brak wymaganego parametru, w tym `name` albo
  `start_date` w `create_sprint`) - CLI nie jest wolane.
- Dokladnie jedno wywolanie CLI na operacje (bez `auth whoami`).
- Zadna op (w tym `get_ticket`, bo API v2 niesie `spec_page_id`) nie wypisuje na stderr linii `OP-NOTE`.

Atrapa `monolynx` waliduje sie wobec fixture'ow przechwyconych z realnego `--help`
(`tests/fixtures/monolynx/`): komenda musi istniec w drzewie `--help`, a kazda flaga w argv w
`<grupa> <komenda> --help` (kryterium dryfu). Zrodlo fixture'a: `<nazwa>-help.source.txt`
(`--help` CLI zbudowanego z drzewa zrodel, gdy plik istnieje - flagi jeszcze niewydane), inaczej
`<nazwa>-help.0.1.0.txt` (realny `monolynx 0.1.0`).
"""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "plugin" / "scripts" / "monolynx_op.py"
FIXTURES = ROOT / "tests" / "fixtures" / "monolynx"

PREFIX = ["-o", "json", "--project", "monolynx"]
SLUG = ["--project_slug", "monolynx"]
CLI_JSON = '{"id": "t1", "key": "MON-1", "title": "tytuł"}\n'

FAKE_TEMPLATE = """#!{python}
import json, os, sys, time

spec = json.load(open(os.environ["FAKE_SPEC"]))
argv = sys.argv[1:]
stdin = sys.stdin.read()
with open(spec["log"], "a") as fh:
    fh.write(json.dumps({{"argv": argv, "stdin": stdin}}) + "\\n")
time.sleep(spec.get("sleep", 0))
sys.stdout.write(spec.get("stdout", ""))
sys.stderr.write(spec.get("stderr", ""))
sys.exit(spec.get("code", 0))
"""

# (op, argumenty wrappera po --project_slug, oczekiwany ogon argv CLI po prefiksie, oczekiwany stdin CLI).
# Napisy rozbijane przez shlex; tokeny z _TOKEN_VALUES (np. LONG_TEXT: wieloliniowy, z cudzyslowem i `git push`)
# sa podmieniane po rozbiciu, zeby cudzyslowy JSON-a nie mieszaly sie z cytowaniem powloki.
LONG = "Długa treść z cudzysłowem \" i apostrofem ' oraz\nnową linią i git push\n"
_RAW_SCENARIOS: list[tuple[str, str, str, str]] = [
    ("get_ticket", "--ticket_id MON-1", "ticket get MON-1", ""),
    (
        "list_tickets",
        "--status todo --sprint_id S1 --page 2 --overdue true --due_date_before 2026-10-01 --label_id L1"
        " --search 'foo bar' --priority high --due_date_after 2026-09-01",
        "ticket list --status todo --sprint S1 --page 2 --overdue --due-before 2026-10-01 --label L1"
        " --search 'foo bar' --priority high --due-after 2026-09-01",
        "",
    ),
    (
        "search_tickets",
        "--query 'x y' --status done --assignee_email a@b.c --sprint_id S2 --due_before 2026-10-05 --due_after 2026-10-01 --page 3 --priority low",
        "ticket search 'x y' --status done --assignee a@b.c --sprint S2 --due-before 2026-10-05 --due-after 2026-10-01 --page 3 --priority low",
        "",
    ),
    ("add_comment", "--ticket_id MON-1 --content LONG_TEXT", "ticket comment add MON-1 --body -", "LONG_TEXT"),
    ("list_comments", "--ticket_id MON-1", "ticket comment list MON-1", ""),
    (
        "update_ticket",
        "--ticket_id MON-1 --status in_progress --assignee_email me",
        "ticket update MON-1 --status in_progress --assignee me",
        "",
    ),
    (
        "update_ticket",
        "--ticket_id MON-1 --title Nowy --priority high --story_points 5 --sprint_id S1 --due_date 2026-10-09",
        "ticket update MON-1 --title Nowy --priority high --sp 5 --sprint S1 --due 2026-10-09",
        "",
    ),
    ("update_ticket", "--ticket_id MON-1 --description LONG_TEXT", "ticket update MON-1 --description -", "LONG_TEXT"),
    ("update_ticket", "--ticket_id MON-1 --label_ids LABELS_A1_B2", "ticket update MON-1 --label a1 --label b2", ""),
    ("update_ticket", "--ticket_id MON-1 --blocked_by_ids IDS_U1_U2", "ticket update MON-1 --blocked-by u1 --blocked-by u2", ""),
    ("update_ticket", "--ticket_id MON-1 --blocked_by_ids EMPTY_LIST", "ticket update MON-1 --no-blockers", ""),
    (
        "create_ticket",
        "--title Tytuł --description LONG_TEXT --priority high --story_points 3 --sprint_id S1 --assignee_email me"
        " --due_date 2026-10-09 --label_ids LABELS_L1 --acceptance_criteria CRITERIA_TWO"
        " --spec_page_id P1 --blocked_by_ids IDS_U1",
        "ticket create --title Tytuł --description - --priority high --sp 3 --sprint S1 --assignee me --due 2026-10-09"
        " --label l1 --ac 'kryterium 1' --ac 'kryterium 2' --spec-page P1 --blocked-by u1",
        "LONG_TEXT",
    ),
    ("list_tickets", "--compact true", "ticket list --compact", ""),
    ("list_tickets", "--status todo --compact true --page 2", "ticket list --status todo --compact --page 2", ""),
    ("create_ticket", "--title T --brief true", "ticket create --title T --brief", ""),
    ("update_ticket", "--ticket_id MON-1 --brief true", "ticket update MON-1 --brief", ""),
    ("update_ticket", "--ticket_id MON-1 --brief true --status done", "ticket update MON-1 --brief --status done", ""),
    ("get_board", "", "sprint board", ""),
    ("list_sprints", "--status active", "sprint list --status active", ""),
    ("complete_sprint", "--sprint_id S1", "sprint complete S1", ""),
    ("get_burndown", "--sprint_id S1", "sprint burndown S1", ""),
    ("get_burndown", "", "sprint burndown", ""),
    (
        "create_sprint",
        "--name 'Sprint 1' --start_date 2026-10-05 --end_date 2026-10-19 --goal LONG_TEXT",
        "sprint create --name 'Sprint 1' --start 2026-10-05 --end 2026-10-19 --goal LONG_TEXT",
        "",
    ),
    ("create_sprint", "--name 'Sprint 2' --start_date 2026-10-05", "sprint create --name 'Sprint 2' --start 2026-10-05", ""),
    (
        "create_sprint",
        "--goal 'cel' --end_date 2026-10-19 --start_date 2026-10-05 --name Odwrotnie",
        "sprint create --goal cel --end 2026-10-19 --start 2026-10-05 --name Odwrotnie",
        "",
    ),
    ("start_sprint", "--sprint_id S1", "sprint start S1", ""),
    ("get_sprint", "--sprint_id S1", "sprint get S1", ""),
    (
        "update_sprint",
        "--sprint_id S1 --name Nowy --goal LONG_TEXT --start_date 2026-10-06 --end_date 2026-10-20",
        "sprint update S1 --name Nowy --goal LONG_TEXT --start 2026-10-06 --end 2026-10-20",
        "",
    ),
    ("update_sprint", "--sprint_id S1 --goal 'nowy cel'", "sprint update S1 --goal 'nowy cel'", ""),
    ("update_sprint", "--sprint_id S1 --end_date 2026-10-20", "sprint update S1 --end 2026-10-20", ""),
    ("list_acceptance_criteria", "--ticket_id MON-1", "ticket ac list MON-1", ""),
    (
        "add_acceptance_criterion",
        "--ticket_id MON-1 --description QUOTED_TEXT",
        "ticket ac add MON-1 QUOTED_TEXT",
        "",
    ),
    (
        "update_acceptance_criterion",
        "--ticket_id MON-1 --criterion_id C1 --description 'nowy opis' --is_completed true",
        "ticket ac update MON-1 C1 --description 'nowy opis' --done",
        "",
    ),
    (
        "update_acceptance_criterion",
        "--ticket_id MON-1 --criterion_id C1 --is_completed false",
        "ticket ac update MON-1 C1 --not-done",
        "",
    ),
    ("list_labels", "", "ticket label list", ""),
    ("search_wiki", "--query 'jak działa RAG' --limit 5", "wiki search 'jak działa RAG' --limit 5", ""),
    ("get_wiki_page", "--page_id P1", "wiki get P1", ""),
    ("list_wiki_pages", "", "wiki list", ""),
    (
        "create_wiki_page",
        "--title Strona --content LONG_TEXT --parent_id P0 --position 3 --is_public true",
        "wiki create --title Strona --content - --parent P0 --position 3 --public",
        "LONG_TEXT",
    ),
    ("create_wiki_page", "--title Strona --content x --is_public false", "wiki create --title Strona --content - --no-public", "x"),
    (
        "update_wiki_page",
        "--page_id P1 --title Nowy --content LONG_TEXT --position 1 --is_public false",
        "wiki update P1 --title Nowy --content - --position 1 --no-public",
        "LONG_TEXT",
    ),
    ("get_wiki_config", "", "wiki config get", ""),
    ("list_graph_nodes", "--type File --search foo --limit 5", "graph nodes --type File --search foo --limit 5", ""),
    ("list_graph_nodes", "", "graph nodes", ""),
    (
        "get_graph_node",
        "--node_id abc --depth 2 --relation_types RELATIONS_CALLS_IMPORTS --node_types NODE_TYPES_CLASS",
        "graph node abc --depth 2 --relation-type CALLS --relation-type IMPORTS --node-type Class",
        "",
    ),
    ("get_graph_node", "--node_id abc", "graph node abc", ""),
    ("get_graph_node", "--node_id abc --relation_types EMPTY_LIST --node_types EMPTY_LIST", "graph node abc", ""),
    ("query_graph", "--node_type Class --search foo --limit 10", "graph query --node-type Class --search foo --limit 10", ""),
    ("query_graph", "", "graph query", ""),
    ("find_graph_path", "--source_id a --target_id b", "graph path --source-id a --target-id b", ""),
    ("get_graph_stats", "", "graph stats", ""),
]


_TOKEN_VALUES = {
    "RELATIONS_CALLS_IMPORTS": '["CALLS", "IMPORTS"]',
    "NODE_TYPES_CLASS": '["Class"]',
    "LONG_TEXT": LONG,
    "QUOTED_TEXT": 'Kryterium z "cudzysłowem"',
    "LABELS_A1_B2": '["a1", "b2"]',
    "LABELS_L1": '["l1"]',
    "IDS_U1_U2": '["u1", "u2"]',
    "IDS_U1": '["u1"]',
    "EMPTY_LIST": "[]",
    "CRITERIA_TWO": '["kryterium 1", "kryterium 2"]',
}


def _expand(tokens: list[str]) -> list[str]:
    return [_TOKEN_VALUES.get(token, token) for token in tokens]


SCENARIOS: list[tuple[str, list[str], list[str], str]] = [
    (op, _expand(shlex.split(params)), _expand(shlex.split(tail)), _TOKEN_VALUES.get(stdin, stdin)) for op, params, tail, stdin in _RAW_SCENARIOS
]
SCENARIO_IDS = [f"{op}-{index}" for index, (op, *_rest) in enumerate(SCENARIOS)]
SCENARIO_OPS = {op for op, *_rest in SCENARIOS}

# Op bez komendy CLI (zawsze fallback): pipelines, LLM Wiki, czas pracy oraz zapis grafu (robi go tylko synchronizacja przez MCP).
NO_CLI_OPS = shlex.split(
    "update_pipeline_job create_pipeline_job append_job_log log_time finish_pipeline append_wiki_log"
    " regenerate_wiki_index create_pipeline lint_wiki clean_pipeline_logs"
    " bootstrap_wiki_llm get_pipeline list_pipelines get_pipeline_job_log get_work_report get_wiki_backlinks replace_graph"
    " create_graph_node delete_graph_node create_graph_edge delete_graph_edge bulk_create_graph_nodes bulk_create_graph_edges"
    " totally_unknown_tool"
)

GRAPH_READ_OPS = frozenset({"list_graph_nodes", "get_graph_node", "query_graph", "find_graph_path", "get_graph_stats"})

# Prefiks nazwy op -> czy odczyt (kontrakt READ_OPS).
READ_PREFIXES = ("get_", "list_", "search_", "query_", "find_")


class Env:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "fake.log"
        self.spec_path = tmp_path / "spec.json"

    def install_cli(self, code: int = 0, stdout: str = CLI_JSON, stderr: str = "", sleep: float = 0) -> None:
        fake = self.bin / "monolynx"
        fake.write_text(FAKE_TEMPLATE.format(python=sys.executable), encoding="utf-8")
        fake.chmod(0o755)
        spec = {"log": str(self.log), "code": code, "stdout": stdout, "stderr": stderr, "sleep": sleep}
        self.spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def env(self, **extra: str) -> dict[str, str]:
        base = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp), "FAKE_SPEC": str(self.spec_path)}
        base.update(extra)
        return base

    def entries(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def run(self, *args: str, stdin: str = "", **env_extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=self.env(**env_extra),
            timeout=60,
        )


@pytest.fixture
def sandbox(tmp_path: Path) -> Env:
    return Env(tmp_path)


def load_module():
    spec = importlib.util.spec_from_file_location("monolynx_op_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fixture'y z realnego CLI: drzewo komend i flagi
# ---------------------------------------------------------------------------


def fixture_text(name: str) -> str:
    path = FIXTURES / f"{name}-help.source.txt"
    if not path.exists():
        path = FIXTURES / f"{name}-help.0.1.0.txt"
    assert path.exists(), f"brak fixture {name}-help.source.txt ani {name}-help.0.1.0.txt"
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("# "))


def listed_commands(path: list[str]) -> set[str]:
    """Nazwy podkomend z sekcji Commands pliku `<grupa>-help` (wiersze `│ nazwa  opis`); [] = root."""
    text = fixture_text("-".join(path) if path else "root")
    section = text.split("Commands", 1)[1]
    return set(re.findall(r"^│ ([a-z][a-z-]*)\s", section, flags=re.MULTILINE))


def has_subcommands(path: list[str]) -> bool:
    return "Commands" in fixture_text("-".join(path))


def assert_argv_matches_real_cli(tail: list[str]) -> None:
    """Komenda istnieje w drzewie `--help`, a kazda flaga z argv wystepuje w `--help` tej komendy."""
    assert tail[0] in listed_commands([]), f"grupa {tail[0]} nie istnieje w drzewie --help fixture'ow (source albo 0.1.0)"
    path = [tail[0]]
    while has_subcommands(path):
        child = tail[len(path)]
        assert child in listed_commands(path), f"komenda {' '.join([*path, child])} nie istnieje w drzewie --help fixture'ow (source albo 0.1.0)"
        path.append(child)
    help_text = fixture_text("-".join(path))
    for token in tail[len(path) :]:
        if token.startswith("--"):
            pattern = rf"(?<![\w-]){re.escape(token)}(?![\w-])"
            assert re.search(pattern, help_text), f"flaga {token} nie wystepuje w `monolynx {' '.join(path)} --help`"


# ---------------------------------------------------------------------------
# Kontrakt modulu
# ---------------------------------------------------------------------------


class TestModuleContract:
    def test_scenario_ops_are_all_in_ops_map(self):
        module = load_module()

        assert set(module.OPS) >= SCENARIO_OPS, sorted(SCENARIO_OPS - set(module.OPS))

    def test_no_cli_ops_are_not_in_ops_map(self):
        module = load_module()

        assert set(NO_CLI_OPS).isdisjoint(module.OPS), sorted(set(NO_CLI_OPS) & set(module.OPS))

    def test_read_ops_contains_only_read_prefixed_names(self):
        module = load_module()

        assert module.READ_OPS, "READ_OPS puste"
        offenders = sorted(op for op in module.READ_OPS if not op.startswith(READ_PREFIXES))
        assert offenders == []

    def test_graph_read_ops_are_mapped_and_marked_read_only(self):
        module = load_module()

        assert set(module.OPS) >= GRAPH_READ_OPS, sorted(GRAPH_READ_OPS - set(module.OPS))
        assert set(module.READ_OPS) >= GRAPH_READ_OPS, sorted(GRAPH_READ_OPS - set(module.READ_OPS))

    def test_graph_write_ops_are_not_mapped_nor_read_only(self):
        module = load_module()
        graph_writes = {op for op in NO_CLI_OPS if "graph" in op}

        assert graph_writes == {
            "replace_graph",
            "create_graph_node",
            "delete_graph_node",
            "create_graph_edge",
            "delete_graph_edge",
            "bulk_create_graph_nodes",
            "bulk_create_graph_edges",
        }
        assert graph_writes.isdisjoint(module.OPS)
        assert graph_writes.isdisjoint(module.READ_OPS)

    def test_read_ops_is_subset_of_ops(self):
        module = load_module()

        assert set(module.READ_OPS) <= set(module.OPS)

    def test_read_ops_match_read_prefixed_scenarios_exactly(self):
        module = load_module()
        expected = {op for op in SCENARIO_OPS if op.startswith(READ_PREFIXES)}

        assert expected <= set(module.READ_OPS), sorted(expected - set(module.READ_OPS))
        writes = SCENARIO_OPS - expected
        assert writes.isdisjoint(module.READ_OPS), sorted(writes & set(module.READ_OPS))

    def test_main_is_callable_in_process_and_returns_exit_code(self, sandbox, monkeypatch, capsys):
        sandbox.install_cli()
        monkeypatch.setenv("PATH", f"{sandbox.bin}:/usr/bin:/bin")
        monkeypatch.setenv("FAKE_SPEC", str(sandbox.spec_path))
        monkeypatch.delenv("MONOLYNX_TRANSPORT", raising=False)
        module = load_module()

        code = module.main(["get_ticket", *SLUG, "--ticket_id", "MON-1"])

        assert code == 0
        assert capsys.readouterr().out == CLI_JSON


# ---------------------------------------------------------------------------
# Mapowanie op -> argv CLI
# ---------------------------------------------------------------------------


class TestOpToArgvMapping:
    @pytest.mark.parametrize(("op", "params", "tail", "stdin"), SCENARIOS, ids=SCENARIO_IDS)
    def test_op_builds_exact_cli_argv_and_stdin(self, sandbox, op, params, tail, stdin):
        sandbox.install_cli()

        result = sandbox.run(op, *SLUG, *params)

        assert result.returncode == 0, result.stderr
        assert result.stdout == CLI_JSON
        entries = sandbox.entries()
        assert len(entries) == 1
        assert entries[0]["argv"] == [*PREFIX, *tail]
        assert entries[0]["stdin"] == stdin

    @pytest.mark.parametrize(("op", "params", "tail", "stdin"), SCENARIOS, ids=SCENARIO_IDS)
    def test_mapped_argv_exists_in_real_cli_help_tree(self, op, params, tail, stdin):
        assert_argv_matches_real_cli(tail)

    def test_overdue_false_omits_flag(self, sandbox):
        sandbox.install_cli()

        sandbox.run("list_tickets", *SLUG, "--overdue", "false", "--status", "todo")

        assert sandbox.entries()[0]["argv"] == [*PREFIX, "ticket", "list", "--status", "todo"]

    @pytest.mark.parametrize(
        ("op", "params", "flag", "expected_tail"),
        [
            ("list_tickets", ["--status", "todo"], "--compact", ["ticket", "list", "--status", "todo"]),
            ("create_ticket", ["--title", "T"], "--brief", ["ticket", "create", "--title", "T"]),
            ("update_ticket", ["--ticket_id", "MON-1", "--status", "done"], "--brief", ["ticket", "update", "MON-1", "--status", "done"]),
        ],
        ids=["list_tickets-compact", "create_ticket-brief", "update_ticket-brief"],
    )
    def test_compact_and_brief_false_omit_flag(self, sandbox, op, params, flag, expected_tail):
        sandbox.install_cli()

        sandbox.run(op, *SLUG, flag, "false", *params)

        assert sandbox.entries()[0]["argv"] == [*PREFIX, *expected_tail]

    @pytest.mark.parametrize(
        ("op", "params", "expected_tail"),
        [
            ("list_tickets", ["--status", "todo"], ["ticket", "list", "--status", "todo"]),
            ("create_ticket", ["--title", "T"], ["ticket", "create", "--title", "T"]),
            ("update_ticket", ["--ticket_id", "MON-1", "--status", "done"], ["ticket", "update", "MON-1", "--status", "done"]),
        ],
        ids=["list_tickets", "create_ticket", "update_ticket"],
    )
    def test_compact_and_brief_are_not_enabled_by_default(self, sandbox, op, params, expected_tail):
        sandbox.install_cli()

        sandbox.run(op, *SLUG, *params)

        assert sandbox.entries()[0]["argv"] == [*PREFIX, *expected_tail]

    @pytest.mark.parametrize(
        ("op", "params"),
        [
            ("get_ticket", ["--ticket_id", "MON-1", "--brief", "true"]),
            ("create_ticket", ["--title", "T", "--compact", "true"]),
            ("update_ticket", ["--ticket_id", "MON-1", "--compact", "true"]),
            ("list_tickets", ["--brief", "true"]),
            ("search_tickets", ["--query", "x", "--compact", "true"]),
        ],
        ids=["get_ticket-brief", "create_ticket-compact", "update_ticket-compact", "list_tickets-brief", "search_tickets-compact"],
    )
    def test_compact_or_brief_on_op_that_does_not_know_it_gives_fallback_without_calling_cli(self, sandbox, op, params):
        sandbox.install_cli()

        result = sandbox.run(op, *SLUG, *params)

        assert result.returncode == 3
        assert result.stdout.splitlines()[-1] == f"OP-FALLBACK: mcp tool={op}"
        assert sandbox.entries() == []

    def test_only_given_params_are_forwarded_without_defaults(self, sandbox):
        sandbox.install_cli()

        sandbox.run("create_wiki_page", *SLUG, "--title", "T", "--content", "c")

        assert sandbox.entries()[0]["argv"] == [*PREFIX, "wiki", "create", "--title", "T", "--content", "-"]

    def test_project_slug_is_passed_to_global_project_flag(self, sandbox):
        sandbox.install_cli()

        sandbox.run("get_ticket", "--project_slug", "inny-projekt", "--ticket_id", "MON-9")

        assert sandbox.entries()[0]["argv"][:4] == ["-o", "json", "--project", "inny-projekt"]

    def test_get_ticket_emits_no_op_note(self, sandbox):
        sandbox.install_cli()

        result = sandbox.run("get_ticket", *SLUG, "--ticket_id", "MON-1")

        assert result.returncode == 0
        assert result.stdout.strip()
        assert "OP-NOTE" not in result.stderr
        assert "OP-NOTE" not in result.stdout

    def test_other_read_op_has_no_op_note(self, sandbox):
        sandbox.install_cli()

        result = sandbox.run("list_comments", *SLUG, "--ticket_id", "MON-1")

        assert "OP-NOTE" not in result.stderr

    def test_value_dash_reads_param_from_wrapper_stdin(self, sandbox):
        sandbox.install_cli()

        sandbox.run("add_comment", *SLUG, "--ticket_id", "MON-1", "--content", "-", stdin="z stdin wrappera\nlinia 2\n")

        entry = sandbox.entries()[0]
        assert entry["argv"] == [*PREFIX, "ticket", "comment", "add", "MON-1", "--body", "-"]
        assert entry["stdin"] == "z stdin wrappera\nlinia 2\n"

    def test_value_at_path_reads_param_from_file(self, sandbox, tmp_path):
        sandbox.install_cli()
        body = tmp_path / "opis.md"
        body.write_text("Zażółć gęślą jaźń\n# git commit\n", encoding="utf-8")

        sandbox.run("update_ticket", *SLUG, "--ticket_id", "MON-1", "--description", f"@{body}")

        entry = sandbox.entries()[0]
        assert entry["argv"] == [*PREFIX, "ticket", "update", "MON-1", "--description", "-"]
        assert entry["stdin"] == "Zażółć gęślą jaźń\n# git commit\n"

    def test_at_path_works_for_wiki_content(self, sandbox, tmp_path):
        sandbox.install_cli()
        body = tmp_path / "strona.md"
        body.write_text("# Tytuł\ntreść\n", encoding="utf-8")

        sandbox.run("update_wiki_page", *SLUG, "--page_id", "P1", "--content", f"@{body}")

        entry = sandbox.entries()[0]
        assert entry["argv"] == [*PREFIX, "wiki", "update", "P1", "--content", "-"]
        assert entry["stdin"] == "# Tytuł\ntreść\n"


# ---------------------------------------------------------------------------
# Kody wyjscia
# ---------------------------------------------------------------------------


class TestExitCodes:
    @pytest.mark.parametrize("cli_code", [3, 2])
    def test_cli_auth_or_usage_error_gives_fallback_exit_3(self, sandbox, cli_code):
        sandbox.install_cli(code=cli_code, stdout="", stderr="Błąd z CLI\n")

        result = sandbox.run("get_ticket", *SLUG, "--ticket_id", "MON-1")

        assert result.returncode == 3
        assert result.stdout.splitlines()[-1] == "OP-FALLBACK: mcp tool=get_ticket"
        assert result.stderr.strip() != ""

    @pytest.mark.parametrize("cli_code", [1, 4])
    def test_cli_api_or_network_error_gives_exit_2_without_fallback(self, sandbox, cli_code):
        sandbox.install_cli(code=cli_code, stdout="", stderr="Błąd API: 500 serwer padł\n")

        result = sandbox.run("add_comment", *SLUG, "--ticket_id", "MON-1", "--content", "x")

        assert result.returncode == 2
        assert "Błąd API: 500 serwer padł" in result.stderr
        assert result.stdout == ""
        assert "OP-FALLBACK" not in result.stdout + result.stderr

    def test_missing_cli_in_path_gives_fallback_exit_3(self, sandbox):
        result = sandbox.run("get_ticket", *SLUG, "--ticket_id", "MON-1")

        assert result.returncode == 3
        assert result.stdout.splitlines()[-1] == "OP-FALLBACK: mcp tool=get_ticket"

    def test_transport_mcp_env_gives_fallback_without_calling_cli(self, sandbox):
        sandbox.install_cli()

        result = sandbox.run("get_ticket", *SLUG, "--ticket_id", "MON-1", MONOLYNX_TRANSPORT="mcp")

        assert result.returncode == 3
        assert result.stdout.splitlines()[-1] == "OP-FALLBACK: mcp tool=get_ticket"
        assert sandbox.entries() == []

    @pytest.mark.parametrize("op", NO_CLI_OPS)
    def test_op_without_cli_command_gives_fallback_without_calling_cli(self, sandbox, op):
        sandbox.install_cli()

        result = sandbox.run(op, *SLUG, "--job_id", "J1", "--status", "running")

        assert result.returncode == 3
        assert result.stdout.splitlines()[-1] == f"OP-FALLBACK: mcp tool={op}"
        assert result.stderr.strip() != ""
        assert sandbox.entries() == []

    def test_unknown_param_for_known_op_gives_fallback_without_calling_cli(self, sandbox):
        sandbox.install_cli()

        result = sandbox.run("get_ticket", *SLUG, "--ticket_id", "MON-1", "--bogus_param", "x")

        assert result.returncode == 3
        assert result.stdout.splitlines()[-1] == "OP-FALLBACK: mcp tool=get_ticket"
        assert sandbox.entries() == []

    def test_cli_failure_is_not_retried(self, sandbox):
        sandbox.install_cli(code=3, stdout="", stderr="x\n")

        sandbox.run("get_ticket", *SLUG, "--ticket_id", "MON-1")

        assert len(sandbox.entries()) == 1

    def test_stdout_of_cli_is_passed_through_byte_for_byte(self, sandbox):
        payload = '{\n  "a": [1, 2],\n  "b": "zażółć"\n}\n'
        sandbox.install_cli(stdout=payload)

        result = sandbox.run("list_labels", *SLUG)

        assert result.stdout == payload


# ---------------------------------------------------------------------------
# Bledy uzycia: kod 64, CLI nie wolane
# ---------------------------------------------------------------------------


USAGE_ERROR_CASES = {
    "no-op": "",
    "flag-without-value": "get_ticket --project_slug monolynx --ticket_id",
    "stray-positional": "get_ticket --project_slug monolynx --ticket_id MON-1 stray",
    "param-without-dashes": "get_ticket project_slug monolynx",
    "list-invalid-json": "update_ticket --project_slug monolynx --ticket_id MON-1 --label_ids 'not json'",
    "list-not-a-list": "update_ticket --project_slug monolynx --ticket_id MON-1 --label_ids '{\"a\": 1}'",
    "list-string": "update_ticket --project_slug monolynx --ticket_id MON-1 --label_ids '\"jeden\"'",
    "bool-invalid": "update_acceptance_criterion --project_slug monolynx --ticket_id MON-1 --criterion_id C1 --is_completed maybe",
    "bool-overdue-invalid": "list_tickets --project_slug monolynx --overdue maybe",
    "bool-compact-invalid": "list_tickets --project_slug monolynx --compact yes",
    "bool-brief-invalid-create": "create_ticket --project_slug monolynx --title T --brief yes",
    "bool-brief-invalid-update": "update_ticket --project_slug monolynx --ticket_id MON-1 --brief yes",
    "missing-at-file": "update_ticket --project_slug monolynx --ticket_id MON-1 --description @/nie/istnieje/plik.md",
    "op-uppercase-hyphen": "Get-Ticket --project_slug monolynx --ticket_id MON-1",
    "op-with-space": "'get ticket' --project_slug monolynx --ticket_id MON-1",
    "op-leading-digit": "1get --project_slug monolynx",
    "flag-uppercase": "get_ticket --project_slug monolynx --Ticket_Id MON-1",
    "flag-hyphenated": "get_ticket --project_slug monolynx --ticket-id MON-1",
    "graph-node-without-node-id": "get_graph_node --project_slug monolynx --depth 2",
    "graph-path-without-source": "find_graph_path --project_slug monolynx --target_id b",
    "graph-path-without-target": "find_graph_path --project_slug monolynx --source_id a",
    "graph-path-without-both": "find_graph_path --project_slug monolynx",
    "flag-single-dash": "get_ticket --project_slug monolynx -ticket_id MON-1",
    "list-with-number": "update_ticket --project_slug monolynx --ticket_id MON-1 --label_ids '[1]'",
    "list-with-null": "update_ticket --project_slug monolynx --ticket_id MON-1 --label_ids '[\"a\", null]'",
    "duplicate-param": "get_ticket --project_slug monolynx --ticket_id A --ticket_id B",
    "duplicate-project-slug": "get_ticket --project_slug monolynx --project_slug inny --ticket_id A",
    "slug-missing": "get_ticket --ticket_id MON-1",
    "slug-uppercase": "get_ticket --project_slug Mon --ticket_id MON-1",
    "slug-leading-hyphen": "get_ticket --project_slug -x --ticket_id MON-1",
    "slug-with-space": "get_ticket --project_slug 'a b' --ticket_id MON-1",
    "slug-empty": "get_ticket --project_slug '' --ticket_id MON-1",
    "slug-double-hyphen": "get_ticket --project_slug a--b --ticket_id MON-1",
    "missing-required-positional": "get_ticket --project_slug monolynx",
    "missing-second-required-positional": "add_acceptance_criterion --project_slug monolynx --ticket_id MON-1",
    "missing-required-query": "search_wiki --project_slug monolynx",
    "at-file-not-utf8": "update_ticket --project_slug monolynx --ticket_id MON-1 --description @NOT_UTF8_FILE",
    "start-sprint-missing-sprint-id": "start_sprint --project_slug monolynx",
    "get-sprint-missing-sprint-id": "get_sprint --project_slug monolynx",
    "update-sprint-missing-sprint-id": "update_sprint --project_slug monolynx --name Nowy",
    "create-sprint-missing-name": "create_sprint --project_slug monolynx --start_date 2026-10-05 --end_date 2026-10-19",
    "create-sprint-missing-start-date": "create_sprint --project_slug monolynx --name 'Sprint 1' --end_date 2026-10-19",
}


class TestUsageErrors:
    @pytest.mark.parametrize("command_line", list(USAGE_ERROR_CASES.values()), ids=list(USAGE_ERROR_CASES))
    def test_usage_error_exits_64_without_calling_cli(self, sandbox, command_line):
        sandbox.install_cli()
        bad_file = sandbox.tmp / "bad.md"
        bad_file.write_bytes(b"\xff\xfe\x00 nie utf-8")

        result = sandbox.run(*shlex.split(command_line.replace("NOT_UTF8_FILE", str(bad_file))))

        assert result.returncode == 64
        assert "OP-FALLBACK" not in result.stdout
        assert "Traceback" not in result.stderr
        assert sandbox.entries() == []

    def test_unknown_snake_case_op_is_fallback_not_usage_error(self, sandbox):
        sandbox.install_cli()

        result = sandbox.run("no_such_tool", *SLUG)

        assert result.returncode == 3
        assert result.stdout.splitlines()[-1] == "OP-FALLBACK: mcp tool=no_such_tool"
        assert not sandbox.log.exists()

    @pytest.mark.parametrize("op", ["Get-Ticket", "get ticket"])
    def test_invalid_op_name_is_named_in_stderr(self, sandbox, op):
        sandbox.install_cli()

        result = sandbox.run(op, *SLUG)

        assert result.returncode == 64
        assert "niepoprawna nazwa operacji" in result.stderr
        assert "OP-FALLBACK" not in result.stdout
        assert sandbox.entries() == []

    def test_slug_with_single_hyphen_is_valid(self, sandbox):
        sandbox.install_cli()

        result = sandbox.run("get_ticket", "--project_slug", "a-b", "--ticket_id", "MON-1")

        assert result.returncode == 0
        assert sandbox.entries()[0]["argv"][:4] == ["-o", "json", "--project", "a-b"]


class TestInputResolution:
    def test_bare_at_sign_is_a_literal_value(self, sandbox):
        sandbox.install_cli()

        result = sandbox.run("search_wiki", *SLUG, "--query", "@")

        assert result.returncode == 0, result.stderr
        assert sandbox.entries()[0]["argv"] == [*PREFIX, "wiki", "search", "@"]

    def test_stdin_that_is_not_utf8_gives_usage_error(self, sandbox, monkeypatch, capsys):
        import io

        sandbox.install_cli()
        monkeypatch.setenv("PATH", f"{sandbox.bin}:/usr/bin:/bin")
        monkeypatch.setenv("FAKE_SPEC", str(sandbox.spec_path))
        monkeypatch.delenv("MONOLYNX_TRANSPORT", raising=False)
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"\xff\xfe"), encoding="utf-8"))
        module = load_module()

        code = module.main(["add_comment", *SLUG, "--ticket_id", "MON-1", "--content", "-"])

        captured = capsys.readouterr()
        assert code == 64
        assert "nie mozna odczytac stdin" in captured.err
        assert "OP-FALLBACK" not in captured.out
        assert sandbox.entries() == []

    def test_goal_at_path_is_expanded_into_argv_and_cli_stdin_stays_empty(self, sandbox, tmp_path):
        sandbox.install_cli()
        goal = tmp_path / "cel.md"
        goal.write_text("Cel sprintu: zażółć\nlinia 2 git push\n", encoding="utf-8")

        result = sandbox.run("create_sprint", *SLUG, "--name", "S", "--start_date", "2026-10-05", "--goal", f"@{goal}")

        assert result.returncode == 0, result.stderr
        entry = sandbox.entries()[0]
        assert entry["argv"] == [
            *PREFIX,
            "sprint",
            "create",
            "--name",
            "S",
            "--start",
            "2026-10-05",
            "--goal",
            "Cel sprintu: zażółć\nlinia 2 git push\n",
        ]
        assert entry["stdin"] == ""

    def test_goal_dash_reads_wrapper_stdin_into_argv_and_cli_stdin_stays_empty(self, sandbox):
        sandbox.install_cli()

        result = sandbox.run("update_sprint", *SLUG, "--sprint_id", "S1", "--goal", "-", stdin="cel ze stdin\nlinia 2\n")

        assert result.returncode == 0, result.stderr
        entry = sandbox.entries()[0]
        assert entry["argv"] == [*PREFIX, "sprint", "update", "S1", "--goal", "cel ze stdin\nlinia 2\n"]
        assert entry["stdin"] == ""

    def test_two_dash_params_receive_the_same_stdin_text(self, sandbox):
        sandbox.install_cli()

        result = sandbox.run("update_wiki_page", *SLUG, "--page_id", "P1", "--title", "-", "--content", "-", stdin="wspolny tekst\n")

        assert result.returncode == 0, result.stderr
        entry = sandbox.entries()[0]
        assert entry["argv"] == [*PREFIX, "wiki", "update", "P1", "--title", "wspolny tekst\n", "--content", "-"]
        assert entry["stdin"] == "wspolny tekst\n"

    @pytest.mark.parametrize(
        ("param", "value", "expected_tail"),
        [
            ("--overdue", "TRUE", ["--overdue"]),
            ("--overdue", " true ", ["--overdue"]),
            ("--overdue", " False ", []),
            ("--compact", "TRUE", ["--compact"]),
            ("--compact", " true ", ["--compact"]),
            ("--compact", " False ", []),
        ],
    )
    def test_present_bool_ignores_case_and_surrounding_spaces(self, sandbox, param, value, expected_tail):
        sandbox.install_cli()

        sandbox.run("list_tickets", *SLUG, param, value)

        assert sandbox.entries()[0]["argv"] == [*PREFIX, "ticket", "list", *expected_tail]

    @pytest.mark.parametrize(
        ("op", "params", "base_tail"),
        [
            ("create_ticket", ["--title", "T"], ["ticket", "create", "--title", "T"]),
            ("update_ticket", ["--ticket_id", "MON-1"], ["ticket", "update", "MON-1"]),
        ],
        ids=["create_ticket", "update_ticket"],
    )
    @pytest.mark.parametrize(
        ("value", "brief_tail"),
        [("TRUE", ["--brief"]), (" true ", ["--brief"]), (" False ", [])],
    )
    def test_brief_bool_ignores_case_and_surrounding_spaces(self, sandbox, op, params, base_tail, value, brief_tail):
        sandbox.install_cli()

        sandbox.run(op, *SLUG, *params, "--brief", value)

        assert sandbox.entries()[0]["argv"] == [*PREFIX, *base_tail, *brief_tail]

    @pytest.mark.parametrize(("value", "flag"), [(" false ", "--not-done"), ("FALSE", "--not-done"), (" True", "--done")])
    def test_pair_bool_ignores_case_and_surrounding_spaces(self, sandbox, value, flag):
        sandbox.install_cli()

        sandbox.run("update_acceptance_criterion", *SLUG, "--ticket_id", "MON-1", "--criterion_id", "C1", "--is_completed", value)

        assert sandbox.entries()[0]["argv"] == [*PREFIX, "ticket", "ac", "update", "MON-1", "C1", flag]

    def test_absent_optional_positional_is_skipped(self, sandbox):
        sandbox.install_cli()

        sandbox.run("search_tickets", *SLUG, "--status", "todo")

        assert sandbox.entries()[0]["argv"] == [*PREFIX, "ticket", "search", "--status", "todo"]

    @pytest.mark.parametrize(
        ("op", "params"),
        [
            ("search_wiki", ["--query", "-foo"]),
            ("get_ticket", ["--ticket_id", "-1"]),
            ("get_graph_node", ["--node_id", "-1"]),
        ],
    )
    def test_positional_value_starting_with_dash_falls_back_without_calling_cli(self, sandbox, op, params):
        sandbox.install_cli()

        result = sandbox.run(op, *SLUG, *params)

        assert result.returncode == 3
        assert result.stdout.splitlines()[-1] == f"OP-FALLBACK: mcp tool={op}"
        assert sandbox.entries() == []

    def test_positional_value_from_stdin_starting_with_dash_falls_back(self, sandbox):
        sandbox.install_cli()

        result = sandbox.run("search_wiki", *SLUG, "--query", "-", stdin="-x")

        assert result.returncode == 3
        assert result.stdout.splitlines()[-1] == "OP-FALLBACK: mcp tool=search_wiki"
        assert sandbox.entries() == []

    def test_empty_label_list_in_update_ticket_falls_back_without_calling_cli(self, sandbox):
        sandbox.install_cli()

        result = sandbox.run("update_ticket", *SLUG, "--ticket_id", "MON-1", "--label_ids", "[]")

        assert result.returncode == 3
        assert result.stdout.splitlines()[-1] == "OP-FALLBACK: mcp tool=update_ticket"
        assert sandbox.entries() == []

    def test_empty_lists_in_create_ticket_add_no_flags(self, sandbox):
        sandbox.install_cli()

        result = sandbox.run("create_ticket", *SLUG, "--title", "T", "--label_ids", "[]", "--acceptance_criteria", "[]", "--blocked_by_ids", "[]")

        assert result.returncode == 0, result.stderr
        assert sandbox.entries()[0]["argv"] == [*PREFIX, "ticket", "create", "--title", "T"]


class TestCliProcessFailures:
    @pytest.fixture
    def inproc(self, sandbox, monkeypatch):
        monkeypatch.setenv("PATH", f"{sandbox.bin}:/usr/bin:/bin")
        monkeypatch.setenv("FAKE_SPEC", str(sandbox.spec_path))
        monkeypatch.delenv("MONOLYNX_TRANSPORT", raising=False)
        return load_module()

    def test_cli_timeout_gives_exit_2_without_fallback(self, sandbox, inproc, capsys):
        sandbox.install_cli(sleep=5)
        inproc._CLI_TIMEOUT = 0.3

        code = inproc.main(["add_comment", *SLUG, "--ticket_id", "MON-1", "--content", "x"])

        captured = capsys.readouterr()
        assert code == 2
        assert captured.out == ""
        assert "sprawdz stan" in captured.err
        assert "OP-FALLBACK" not in captured.out + captured.err

    @pytest.mark.parametrize("error", [PermissionError("brak praw"), FileNotFoundError("zniknelo")], ids=["permission", "not-found"])
    def test_cli_that_cannot_start_gives_fallback(self, sandbox, inproc, monkeypatch, capsys, error):
        sandbox.install_cli()

        def broken_run(*args, **kwargs):
            raise error

        monkeypatch.setattr(inproc.subprocess, "run", broken_run)

        code = inproc.main(["get_ticket", *SLUG, "--ticket_id", "MON-1"])

        captured = capsys.readouterr()
        assert code == 3
        assert captured.out.splitlines()[-1] == "OP-FALLBACK: mcp tool=get_ticket"
        assert "nie mozna uruchomic CLI" in captured.err

    def test_success_forwards_cli_stderr_and_stdout_unchanged(self, sandbox):
        sandbox.install_cli(stdout=CLI_JSON, stderr="ostrzezenie z CLI\n")

        result = sandbox.run("list_labels", *SLUG)

        assert result.returncode == 0
        assert result.stdout == CLI_JSON
        assert "ostrzezenie z CLI" in result.stderr

    @pytest.mark.parametrize("cli_code", [5, 97])
    def test_unknown_cli_exit_code_gives_exit_2_with_code_and_cli_stderr(self, sandbox, cli_code):
        sandbox.install_cli(code=cli_code, stdout="", stderr="dziwny blad\n")

        result = sandbox.run("add_comment", *SLUG, "--ticket_id", "MON-1", "--content", "x")

        assert result.returncode == 2
        assert result.stdout == ""
        assert f"kodem {cli_code}" in result.stderr
        assert "dziwny blad" in result.stderr
        assert "OP-FALLBACK" not in result.stdout + result.stderr

    def test_unknown_cli_exit_code_with_empty_stderr_still_reports_code(self, sandbox):
        sandbox.install_cli(code=5, stdout="", stderr="")

        result = sandbox.run("add_comment", *SLUG, "--ticket_id", "MON-1", "--content", "x")

        assert result.returncode == 2
        assert "kodem 5" in result.stderr
        assert "OP-FALLBACK" not in result.stdout + result.stderr

    def test_cli_killed_by_signal_gives_exit_2(self, sandbox, inproc, monkeypatch, capsys):
        sandbox.install_cli()
        monkeypatch.setattr(inproc.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], -9, b"", b"zabity\n"))

        code = inproc.main(["add_comment", *SLUG, "--ticket_id", "MON-1", "--content", "x"])

        captured = capsys.readouterr()
        assert code == 2
        assert "kodem -9" in captured.err
        assert "zabity" in captured.err
        assert "OP-FALLBACK" not in captured.out + captured.err

    @pytest.mark.parametrize("cli_code", [2, 3])
    def test_fallback_codes_with_empty_cli_stderr_still_explain_reason(self, sandbox, cli_code):
        sandbox.install_cli(code=cli_code, stdout="", stderr="")

        result = sandbox.run("get_ticket", *SLUG, "--ticket_id", "MON-1")

        assert result.returncode == 3
        assert result.stdout.splitlines()[-1] == "OP-FALLBACK: mcp tool=get_ticket"
        assert f"kodem {cli_code}" in result.stderr

    def test_cli_stderr_that_is_not_utf8_does_not_crash(self, sandbox, inproc, monkeypatch, capsys):
        sandbox.install_cli()
        monkeypatch.setattr(inproc.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, b"", b"blad \xff\xfe koniec\n"))

        code = inproc.main(["add_comment", *SLUG, "--ticket_id", "MON-1", "--content", "x"])

        captured = capsys.readouterr()
        assert code == 2
        assert "blad" in captured.err
        assert "koniec" in captured.err
