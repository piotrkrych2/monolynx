"""Testy plugin/scripts/ticket_overlap.py - wykrywanie ticketow na tych samych plikach (MON-225).

TDD: skrypt jeszcze NIE ISTNIEJE w chwili pisania testow; testy sa specyfikacja
kontraktu dla developera. Skrypt jest testowany wylacznie przez CLI (subprocess),
bo kontrakt definiuje wejscie/wyjscie JSON i kody wyjscia 0 / 2 / 64, nie API modulu.
"""

import itertools
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "ticket_overlap.py"

assert SCRIPT.exists(), f"Brak skryptu: {SCRIPT}"


def run_cli(*args, stdin=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        input=stdin,
        timeout=30,
    )


def t(key, priority="medium", files=None, blocked_by=None):
    ticket = {"key": key, "priority": priority}
    if files is not None:
        ticket["files"] = files
    if blocked_by is not None:
        ticket["blocked_by"] = blocked_by
    return ticket


def analyze(tmp_path, tickets):
    path = tmp_path / "in.json"
    path.write_text(json.dumps({"tickets": tickets}))
    result = run_cli("--input", str(path))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def by_tickets(items):
    return sorted(items, key=lambda item: item["tickets"])


def pairs(proposals):
    return [(p["blocker"], p["blocked"]) for p in proposals]


class TestDirection:
    def test_shared_file_without_relation_yields_one_proposal(self, tmp_path):
        out = analyze(tmp_path, [t("MON-1", files=["a.py"]), t("MON-2", files=["a.py"])])

        assert out["proposals"] == [{"blocker": "MON-1", "blocked": "MON-2", "files": ["a.py"]}]
        assert out["related"] == []
        assert out["shared"] == []
        assert out["no_files"] == []

    @pytest.mark.parametrize(
        ("hi", "lo"),
        [("critical", "high"), ("high", "medium"), ("medium", "low")],
    )
    def test_higher_priority_blocks_lower_even_with_bigger_key_number(self, tmp_path, hi, lo):
        out = analyze(
            tmp_path,
            [t("MON-1", lo, files=["a.py"]), t("MON-2", hi, files=["a.py"])],
        )

        assert pairs(out["proposals"]) == [("MON-2", "MON-1")]

    def test_equal_priority_orders_by_numeric_key_not_string(self, tmp_path):
        out = analyze(
            tmp_path,
            [t("MON-10", files=["a.py"]), t("MON-9", files=["a.py"])],
        )

        assert pairs(out["proposals"]) == [("MON-9", "MON-10")]

    def test_equal_priority_and_number_falls_back_to_key_string(self, tmp_path):
        out = analyze(
            tmp_path,
            [t("MON-5", files=["a.py"]), t("ABC-5", files=["a.py"])],
        )

        assert pairs(out["proposals"]) == [("ABC-5", "MON-5")]

    def test_proposal_files_are_sorted_intersection_only(self, tmp_path):
        out = analyze(
            tmp_path,
            [
                t("MON-1", files=["z.py", "b.py", "only1.py", "a.py"]),
                t("MON-2", files=["a.py", "b.py", "z.py", "only2.py"]),
            ],
        )

        assert out["proposals"] == [{"blocker": "MON-1", "blocked": "MON-2", "files": ["a.py", "b.py", "z.py"]}]

    def test_disjoint_files_yield_nothing(self, tmp_path):
        out = analyze(tmp_path, [t("MON-1", files=["a.py"]), t("MON-2", files=["b.py"])])

        assert out == {"proposals": [], "related": [], "shared": [], "no_files": []}


class TestExistingRelations:
    @pytest.mark.parametrize(
        "tickets",
        [
            [t("MON-1", "high", ["a.py"]), t("MON-2", "medium", ["a.py"], blocked_by=["MON-1"])],
            [t("MON-1", "high", ["a.py"], blocked_by=["MON-2"]), t("MON-2", "medium", ["a.py"])],
        ],
        ids=["along-rank", "against-rank"],
    )
    def test_direct_relation_in_either_direction_is_related_not_proposed(self, tmp_path, tickets):
        out = analyze(tmp_path, tickets)

        assert out["proposals"] == []
        assert out["related"] == [{"tickets": ["MON-1", "MON-2"], "files": ["a.py"]}]

    def test_transitive_relation_through_third_ticket_is_related(self, tmp_path):
        out = analyze(
            tmp_path,
            [
                t("MON-1", files=["a.py"]),
                t("MON-2", files=["x.py"], blocked_by=["MON-1"]),
                t("MON-3", files=["a.py"], blocked_by=["MON-2"]),
            ],
        )

        assert out["proposals"] == []
        assert out["related"] == [{"tickets": ["MON-1", "MON-3"], "files": ["a.py"]}]

    def test_common_blocker_outside_input_does_not_relate_siblings(self, tmp_path):
        out = analyze(
            tmp_path,
            [
                t("MON-1", files=["a.py"], blocked_by=["MON-99"]),
                t("MON-2", files=["a.py"], blocked_by=["MON-99"]),
            ],
        )

        assert pairs(out["proposals"]) == [("MON-1", "MON-2")]
        assert out["related"] == []

    def test_blocker_outside_input_is_accepted(self, tmp_path):
        out = analyze(tmp_path, [t("MON-1", files=["a.py"], blocked_by=["MON-99"])])

        assert out == {"proposals": [], "related": [], "shared": [], "no_files": []}


class TestChain:
    def test_three_tickets_on_same_file_form_chain_not_clique(self, tmp_path):
        out = analyze(
            tmp_path,
            [t("MON-1", files=["a.py"]), t("MON-2", files=["a.py"]), t("MON-3", files=["a.py"])],
        )

        assert out["proposals"] == [
            {"blocker": "MON-1", "blocked": "MON-2", "files": ["a.py"]},
            {"blocker": "MON-2", "blocked": "MON-3", "files": ["a.py"]},
        ]
        assert out["related"] == [{"tickets": ["MON-1", "MON-3"], "files": ["a.py"]}]

    def test_pair_connected_via_proposal_from_same_run_is_related(self, tmp_path):
        out = analyze(
            tmp_path,
            [
                t("MON-1", files=["a.py"]),
                t("MON-2", files=["a.py"], blocked_by=["MON-1"]),
                t("MON-3", files=["a.py"]),
            ],
        )

        assert out["proposals"] == [{"blocker": "MON-2", "blocked": "MON-3", "files": ["a.py"]}]
        assert by_tickets(out["related"]) == [
            {"tickets": ["MON-1", "MON-2"], "files": ["a.py"]},
            {"tickets": ["MON-1", "MON-3"], "files": ["a.py"]},
        ]

    def test_chain_follows_rank_not_input_order(self, tmp_path):
        out = analyze(
            tmp_path,
            [
                t("MON-3", "low", ["a.py"]),
                t("MON-1", "medium", ["a.py"]),
                t("MON-2", "critical", ["a.py"]),
            ],
        )

        assert pairs(out["proposals"]) == [("MON-2", "MON-1"), ("MON-1", "MON-3")]
        assert out["related"] == [{"tickets": ["MON-2", "MON-3"], "files": ["a.py"]}]


class TestSharedFiles:
    def test_changelog_shared_by_two_tickets_yields_no_proposal(self, tmp_path):
        out = analyze(
            tmp_path,
            [t("MON-1", files=["plugin/README.md"]), t("MON-2", files=["plugin/README.md"])],
        )

        assert out["proposals"] == []
        assert out["related"] == []
        assert out["shared"] == [{"file": "plugin/README.md", "tickets": ["MON-1", "MON-2"]}]

    @pytest.mark.parametrize(
        "path",
        [
            "plugin/.claude-plugin/plugin.json",
            "plugin/.codex-plugin/plugin.json",
            "plugin/skills/help/SKILL.md",
            "tests/unit/test_skill_counters.py",
            "src/monolynx/static/how-to-use-monolynx.md",
            "src/monolynx/static/agent-bootstrap.md",
            "src/monolynx/templates/dashboard/profile/mcp_guide.html",
            "src/monolynx/static/skills/work/SKILL.md",
            "src/monolynx/static/skills/deep/nested/file.md",
        ],
    )
    def test_every_always_shared_path_is_reported_as_shared(self, tmp_path, path):
        out = analyze(tmp_path, [t("MON-1", files=[path]), t("MON-2", files=[path])])

        assert out["proposals"] == []
        assert out["shared"] == [{"file": path, "tickets": ["MON-1", "MON-2"]}]

    def test_shared_tickets_are_listed_in_rank_order(self, tmp_path):
        out = analyze(
            tmp_path,
            [
                t("MON-1", "low", ["plugin/README.md"]),
                t("MON-2", "high", ["plugin/README.md"]),
            ],
        )

        assert out["shared"] == [{"file": "plugin/README.md", "tickets": ["MON-2", "MON-1"]}]

    def test_shared_entries_sorted_by_file(self, tmp_path):
        out = analyze(
            tmp_path,
            [
                t("MON-1", files=["plugin/README.md", "plugin/.claude-plugin/plugin.json"]),
                t("MON-2", files=["plugin/.claude-plugin/plugin.json", "plugin/README.md"]),
            ],
        )

        assert [s["file"] for s in out["shared"]] == [
            "plugin/.claude-plugin/plugin.json",
            "plugin/README.md",
        ]

    def test_always_shared_file_touched_by_single_ticket_is_not_reported(self, tmp_path):
        out = analyze(
            tmp_path,
            [t("MON-1", files=["plugin/README.md"]), t("MON-2", files=["a.py"])],
        )

        assert out == {"proposals": [], "related": [], "shared": [], "no_files": []}

    def test_ticket_with_shared_and_real_file_proposes_only_real_file(self, tmp_path):
        out = analyze(
            tmp_path,
            [
                t("MON-1", files=["plugin/README.md", "a.py"]),
                t("MON-2", files=["a.py", "plugin/README.md"]),
            ],
        )

        assert out["proposals"] == [{"blocker": "MON-1", "blocked": "MON-2", "files": ["a.py"]}]
        assert out["shared"] == [{"file": "plugin/README.md", "tickets": ["MON-1", "MON-2"]}]

    def test_shared_only_overlap_does_not_relate_or_chain_tickets(self, tmp_path):
        out = analyze(
            tmp_path,
            [
                t("MON-1", files=["plugin/README.md"]),
                t("MON-2", files=["plugin/README.md", "a.py"]),
                t("MON-3", files=["a.py"]),
            ],
        )

        assert pairs(out["proposals"]) == [("MON-2", "MON-3")]
        assert out["related"] == []

    def test_normalized_shared_path_matches_plain_one(self, tmp_path):
        out = analyze(
            tmp_path,
            [t("MON-1", files=["./plugin/README.md"]), t("MON-2", files=["`plugin/README.md`"])],
        )

        assert out["proposals"] == []
        assert out["shared"] == [{"file": "plugin/README.md", "tickets": ["MON-1", "MON-2"]}]

    def test_prefix_is_directory_match_not_string_prefix(self, tmp_path):
        out = analyze(
            tmp_path,
            [
                t("MON-1", files=["src/monolynx/static/skills_extra/a.md"]),
                t("MON-2", files=["src/monolynx/static/skills_extra/a.md"]),
            ],
        )

        assert pairs(out["proposals"]) == [("MON-1", "MON-2")]
        assert out["shared"] == []


class TestNormalization:
    @pytest.mark.parametrize(
        ("left", "right", "expected"),
        [
            ("./a.py", "a.py", "a.py"),
            ("`a.py`", "a.py", "a.py"),
            (" a.py ", "a.py", "a.py"),
            ("a\\b.py", "a/b.py", "a/b.py"),
            ("`./a/b.py`", "a\\b.py", "a/b.py"),
        ],
    )
    def test_variants_of_same_path_overlap_and_are_reported_normalized(self, tmp_path, left, right, expected):
        out = analyze(tmp_path, [t("MON-1", files=[left]), t("MON-2", files=[right])])

        assert out["proposals"] == [{"blocker": "MON-1", "blocked": "MON-2", "files": [expected]}]

    def test_double_dot_inside_file_name_is_not_a_parent_segment(self, tmp_path):
        out = analyze(tmp_path, [t("MON-1", files=["a..b.py"]), t("MON-2", files=["a..b.py"])])

        assert out["proposals"] == [{"blocker": "MON-1", "blocked": "MON-2", "files": ["a..b.py"]}]

    def test_empty_entries_are_skipped_among_real_files(self, tmp_path):
        out = analyze(
            tmp_path,
            [t("MON-1", files=["", "  ", "a.py"]), t("MON-2", files=["a.py", "``"])],
        )

        assert out["proposals"] == [{"blocker": "MON-1", "blocked": "MON-2", "files": ["a.py"]}]
        assert out["no_files"] == []


class TestNoFiles:
    @pytest.mark.parametrize(
        "ticket",
        [
            {"key": "MON-1", "priority": "high"},
            {"key": "MON-1", "priority": "high", "files": None},
            {"key": "MON-1", "priority": "high", "files": []},
            {"key": "MON-1", "priority": "high", "files": ["", "   ", "``", "./"]},
        ],
        ids=["missing", "null", "empty-list", "only-blank"],
    )
    def test_ticket_without_usable_files_is_listed_in_no_files(self, tmp_path, ticket):
        out = analyze(tmp_path, [ticket, t("MON-2", files=["a.py"])])

        assert out == {"proposals": [], "related": [], "shared": [], "no_files": ["MON-1"]}

    def test_no_files_are_listed_in_rank_order(self, tmp_path):
        out = analyze(
            tmp_path,
            [t("MON-9", "low"), t("MON-10", "high"), t("MON-2", "high"), t("MON-1", "critical", files=[])],
        )

        assert out["no_files"] == ["MON-1", "MON-2", "MON-10", "MON-9"]

    def test_no_files_ticket_is_not_compared_with_others(self, tmp_path):
        out = analyze(
            tmp_path,
            [t("MON-1", files=[]), t("MON-2", files=["a.py"]), t("MON-3", files=["a.py"])],
        )

        assert pairs(out["proposals"]) == [("MON-2", "MON-3")]


class TestInputErrors:
    def run_raw(self, tmp_path, raw):
        path = tmp_path / "in.json"
        path.write_text(raw if isinstance(raw, str) else json.dumps(raw))
        return run_cli("--input", str(path))

    @pytest.mark.parametrize(
        "raw",
        [
            "{not json",
            "",
            {"foo": []},
            {"tickets": "x"},
            {"tickets": {"key": "MON-1"}},
            [],
            {"tickets": ["MON-1"]},
            {"tickets": [t("MON-1", files=["a.py"]), t("MON-1", files=["b.py"])]},
            {"tickets": [t("mon-1")]},
            {"tickets": [t("MON1")]},
            {"tickets": [t("MON-")]},
            {"tickets": [t("MON-1x")]},
            {"tickets": [{"priority": "high"}]},
            {"tickets": [t("MON-1", "urgent")]},
            {"tickets": [{"key": "MON-1"}]},
            {"tickets": [t("MON-1", files="a.py")]},
            {"tickets": [t("MON-1", files=[1])]},
            {"tickets": [t("MON-1", files=["a.py"], blocked_by="MON-2")]},
            {"tickets": [t("MON-1", files=["a.py"], blocked_by=[5])]},
            {"tickets": [t("MON-1", files=["/etc/passwd"])]},
            {"tickets": [t("MON-1", files=["`/abs/a.py`"])]},
            {"tickets": [t("MON-1", files=["../a.py"])]},
            {"tickets": [t("MON-1", files=["a/../b.py"])]},
            {"tickets": [t("MON-1", files=["./../a.py"])]},
            {"tickets": [t("MON-1", files=["a\\..\\b.py"])]},
        ],
        ids=lambda r: str(r)[:50],
    )
    def test_invalid_input_exits_2_with_prefixed_stderr_and_empty_stdout(self, tmp_path, raw):
        result = self.run_raw(tmp_path, raw)

        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr.startswith("ticket_overlap: ")

    def test_invalid_input_is_not_a_traceback(self, tmp_path):
        result = self.run_raw(tmp_path, "{not json")

        assert "Traceback" not in result.stderr

    def test_missing_input_file_exits_2(self, tmp_path):
        result = run_cli("--input", str(tmp_path / "nope.json"))

        assert result.returncode == 2
        assert result.stderr.startswith("ticket_overlap: ")


class TestUsage:
    def test_no_arguments_exits_64(self):
        result = run_cli()

        assert result.returncode == 64
        assert result.stdout == ""
        assert result.stderr.startswith("ticket_overlap: ")

    def test_unknown_flag_exits_64(self, tmp_path):
        path = tmp_path / "in.json"
        path.write_text('{"tickets": []}')

        result = run_cli("--input", str(path), "--bogus")

        assert result.returncode == 64
        assert result.stdout == ""
        assert result.stderr.startswith("ticket_overlap: ")


class TestStdinAndOutput:
    def test_dash_reads_stdin(self):
        payload = {"tickets": [t("MON-1", files=["a.py"]), t("MON-2", files=["a.py"])]}

        result = run_cli("--input", "-", stdin=json.dumps(payload))

        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["proposals"] == [{"blocker": "MON-1", "blocked": "MON-2", "files": ["a.py"]}]

    def test_invalid_stdin_exits_2(self):
        result = run_cli("--input", "-", stdin="nope")

        assert result.returncode == 2
        assert result.stderr.startswith("ticket_overlap: ")

    def test_empty_tickets_yield_all_empty_lists(self, tmp_path):
        out = analyze(tmp_path, [])

        assert out == {"proposals": [], "related": [], "shared": [], "no_files": []}

    def test_output_is_indented_json_with_unescaped_unicode(self, tmp_path):
        path = tmp_path / "in.json"
        path.write_text(json.dumps({"tickets": [t("MON-1", files=["zażółć.py"]), t("MON-2", files=["zażółć.py"])]}))

        result = run_cli("--input", str(path))

        assert result.returncode == 0, result.stderr
        assert "zażółć.py" in result.stdout
        assert '\n  "proposals": [' in result.stdout


class TestDeterminism:
    def test_input_order_does_not_change_result(self, tmp_path):
        tickets = [
            t("MON-4", "low", ["a.py", "plugin/README.md"]),
            t("MON-1", "high", ["a.py", "b.py", "plugin/README.md"]),
            t("MON-10", "high", ["b.py"], blocked_by=["MON-77"]),
            t("MON-2", "medium", ["b.py", "c.py"]),
            t("MON-3", "critical", ["c.py"], blocked_by=["MON-2"]),
            t("MON-9", "medium"),
        ]
        results = []
        for perm in itertools.islice(itertools.permutations(tickets), 0, None, 37):
            path = tmp_path / "perm.json"
            path.write_text(json.dumps({"tickets": list(perm)}))
            run = run_cli("--input", str(path))
            assert run.returncode == 0, run.stderr
            results.append(run.stdout)

        assert len(set(results)) == 1
        out = json.loads(results[0])
        assert out["proposals"] and out["shared"] and out["no_files"] == ["MON-9"]
