"""Testy plugin/scripts/mr_queue_state.py - stan kolejki merge /monolynx:mr-queue.

Wejscia to realne wyjscia `glab 1.100.0` i `gh 2.100.0` z tests/fixtures/glab/
i tests/fixtures/gh/ (pole "output"; "_meta" opisuje komende i date zrzutu),
zgodnie z .claude/rules/external-tool-output.md. Warianty, ktorych nie da sie
zlapac bez prawdziwego merge (np. zielony pipeline na otwartym MR), powstaja jako
kopia fixture ze zmienionym JEDNYM polem - reszta ksztaltu zostaje realna.
"""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "mr_queue_state.py"
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

assert SCRIPT.exists(), f"Brak skryptu: {SCRIPT}"

_spec = importlib.util.spec_from_file_location("mr_queue_state", SCRIPT)
assert _spec is not None and _spec.loader is not None
state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state)


def glab(name):
    return json.loads((FIXTURES / "glab" / f"{name}.1.100.0.json").read_text())["output"]


def gh(name):
    return json.loads((FIXTURES / "gh" / f"{name}.2.100.0.json").read_text())["output"]


def run_cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=30)


def write_json(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


@pytest.fixture
def mr_green():
    """MR !70 z fixture, z head_pipeline.status zmienionym na success."""
    mr = copy.deepcopy(glab("mr-view-opened-pipeline-running"))
    mr["head_pipeline"]["status"] = "success"
    return mr


@pytest.fixture
def no_approvals():
    return glab("mr-approvals-none-required")


@pytest.mark.unit
class TestFixtures:
    @pytest.mark.parametrize("path", sorted((FIXTURES / "glab").glob("*.json")) + sorted((FIXTURES / "gh").glob("*.json")), ids=lambda p: p.name)
    def test_meta_has_command_version_and_date(self, path):
        meta = json.loads(path.read_text())["_meta"]
        assert meta["command"].startswith(("glab ", "gh "))
        assert meta["tool_version"] in ("1.100.0", "2.100.0")
        assert meta["captured"]

    def test_runner_ip_is_redacted(self):
        for name in ("pipeline-jobs-main-manual", "pipeline-jobs-failed"):
            for job in glab(name):
                manager = job.get("runner_manager") or {}
                assert manager.get("ip_address") in (None, "REDACTED")


@pytest.mark.unit
class TestParseRef:
    def test_gitlab_sigil(self):
        assert state.parse_ref("!70") == {"ref": "!70", "provider": "glab", "number": 70, "repo": None}

    def test_github_sigil(self):
        assert state.parse_ref("#15")["provider"] == "gh"

    def test_gitlab_url_keeps_nested_repo(self):
        parsed = state.parse_ref("https://gitlab.com/grupa/pod/projekt/-/merge_requests/12")
        assert parsed == {"ref": "!12", "provider": "glab", "number": 12, "repo": "grupa/pod/projekt"}

    def test_github_url(self):
        parsed = state.parse_ref("https://github.com/cli/cli/pull/14456")
        assert parsed == {"ref": "#14456", "provider": "gh", "number": 14456, "repo": "cli/cli"}

    @pytest.mark.parametrize("raw", ["70", "!0", "!abc", "PR-12", "https://example.com/x"])
    def test_unknown_ref_is_input_error(self, raw):
        with pytest.raises(state._InputError):
            state.parse_ref(raw)


@pytest.mark.unit
class TestClassifyGlab:
    def test_running_pipeline_waits(self, no_approvals):
        result = state.classify_glab(glab("mr-view-opened-pipeline-running"), no_approvals)
        assert result["verdict"] == "wait"
        assert result["source_branch"] == "feat/sprint-model-selection"
        assert result["target_branch"] == "main"

    def test_merged_mr_reports_merged_with_merge_sha(self, no_approvals):
        mr = glab("mr-view-merged")
        result = state.classify_glab(mr, no_approvals)
        assert result["verdict"] == "merged"
        assert result["merge_sha"] == mr["merge_commit_sha"]

    def test_fast_forward_merge_falls_back_to_mr_sha(self, no_approvals):
        mr = copy.deepcopy(glab("mr-view-merged"))
        mr["merge_commit_sha"] = None
        mr["squash_commit_sha"] = None
        assert state.classify_glab(mr, no_approvals)["merge_sha"] == mr["sha"]

    def test_failed_pipeline_is_ci_failed_with_pipeline_id(self, mr_green, no_approvals):
        mr_green["head_pipeline"]["status"] = "failed"
        result = state.classify_glab(mr_green, no_approvals)
        assert result["verdict"] == "ci_failed"
        assert result["pipeline_id"] == mr_green["head_pipeline"]["id"]

    def test_missing_pipeline_needs_human(self, mr_green, no_approvals):
        mr_green["head_pipeline"] = None
        assert state.classify_glab(mr_green, no_approvals)["verdict"] == "needs_human"

    def test_unknown_pipeline_status_waits_with_warning(self, mr_green, no_approvals):
        mr_green["head_pipeline"]["status"] = "status-z-przyszlosci"
        result = state.classify_glab(mr_green, no_approvals)
        assert result["verdict"] == "wait"
        assert "nieznany status" in result["reason"]

    def test_conflict_checked_after_ci(self, mr_green, no_approvals):
        mr_green["has_conflicts"] = True
        assert state.classify_glab(mr_green, no_approvals)["verdict"] == "conflict"

    def test_failed_ci_wins_over_conflict(self, mr_green, no_approvals):
        mr_green["head_pipeline"]["status"] = "failed"
        mr_green["has_conflicts"] = True
        assert state.classify_glab(mr_green, no_approvals)["verdict"] == "ci_failed"

    def test_approved_true_without_approvers_is_not_approval(self, mr_green, no_approvals):
        """approvals_required=0 daje approved=true, ale approved_by=[] - nikt nie zatwierdzil."""
        assert no_approvals["approved"] is True
        assert state.classify_glab(mr_green, no_approvals)["verdict"] == "needs_approval"

    def test_chat_approval_for_current_sha_is_ready(self, mr_green, no_approvals):
        result = state.classify_glab(mr_green, no_approvals, user_approved_sha=mr_green["sha"])
        assert result["verdict"] == "ready"
        assert "czat" in result["reason"]

    def test_chat_approval_for_older_sha_asks_again(self, mr_green, no_approvals):
        assert state.classify_glab(mr_green, no_approvals, user_approved_sha="0" * 40)["verdict"] == "needs_approval"

    def test_approval_in_mr_is_ready(self, mr_green, no_approvals):
        approvals = copy.deepcopy(no_approvals)
        approvals["approved_by"] = [{"user": {"username": "recenzent"}}]
        assert state.classify_glab(mr_green, approvals)["verdict"] == "ready"

    def test_partial_approval_is_not_enough(self, mr_green, no_approvals):
        approvals = copy.deepcopy(no_approvals)
        approvals["approved_by"] = [{"user": {"username": "recenzent"}}]
        approvals["approvals_left"] = 1
        assert state.classify_glab(mr_green, approvals)["verdict"] == "needs_approval"

    def test_draft_needs_human(self, mr_green, no_approvals):
        mr_green["draft"] = True
        assert state.classify_glab(mr_green, no_approvals, mr_green["sha"])["verdict"] == "needs_human"

    def test_blocked_merge_status_needs_human_even_when_approved(self, mr_green, no_approvals):
        mr_green["detailed_merge_status"] = "discussions_not_resolved"
        assert state.classify_glab(mr_green, no_approvals, mr_green["sha"])["verdict"] == "needs_human"


@pytest.mark.unit
class TestClassifyGh:
    def test_review_required_green_checks_needs_approval(self):
        assert state.classify_gh(gh("pr-view-mergeable-review-required"))["verdict"] == "needs_approval"

    def test_approved_but_blocked_needs_human(self):
        result = state.classify_gh(gh("pr-view-approved-blocked"))
        assert result["verdict"] == "needs_human"
        assert "BLOCKED" in result["reason"]

    def test_approved_conflicting_is_conflict(self):
        assert state.classify_gh(gh("pr-view-approved-conflicting"))["verdict"] == "conflict"

    def test_draft_needs_human(self):
        assert state.classify_gh(gh("pr-view-draft-conflicting-check-failure"))["verdict"] == "needs_human"

    def test_merged(self):
        assert state.classify_gh(gh("pr-view-merged"))["verdict"] == "merged"

    def test_status_context_failure_wins_over_pending(self):
        result = state.classify_gh(gh("pr-view-statuscontext-failure-pending"))
        assert result["verdict"] == "ci_failed"

    def test_status_context_pending_waits(self):
        assert state.classify_gh(gh("pr-view-statuscontext-pending"))["verdict"] == "wait"

    def test_clean_with_chat_approval_is_ready(self):
        pr = copy.deepcopy(gh("pr-view-mergeable-review-required"))
        pr["mergeStateStatus"] = "CLEAN"
        pr["reviewDecision"] = ""
        assert state.classify_gh(pr, user_approved_sha=pr["headRefOid"])["verdict"] == "ready"

    def test_check_run_in_progress_waits(self):
        pr = copy.deepcopy(gh("pr-view-mergeable-review-required"))
        pr["statusCheckRollup"][0]["status"] = "IN_PROGRESS"
        pr["statusCheckRollup"][0]["conclusion"] = ""
        assert state.classify_gh(pr)["verdict"] == "wait"

    def test_no_checks_needs_human(self):
        pr = copy.deepcopy(gh("pr-view-mergeable-review-required"))
        pr["statusCheckRollup"] = []
        assert state.classify_gh(pr)["verdict"] == "needs_human"

    def test_changes_requested_needs_human(self):
        pr = copy.deepcopy(gh("pr-view-mergeable-review-required"))
        pr["reviewDecision"] = "CHANGES_REQUESTED"
        assert state.classify_gh(pr)["verdict"] == "needs_human"


@pytest.mark.unit
class TestTargetCi:
    def test_glab_manual_pipeline_with_green_jobs_is_success(self):
        result = state.target_ci_glab(glab("pipelines-by-sha-main-manual"), glab("pipeline-jobs-main-manual"))
        assert result["status"] == "success"

    def test_glab_manual_pipeline_without_jobs_waits(self):
        assert state.target_ci_glab(glab("pipelines-by-sha-main-manual"), None)["status"] == "wait"

    def test_glab_failed_job_is_failed(self):
        result = state.target_ci_glab([{"id": 1, "status": "manual"}], glab("pipeline-jobs-failed"))
        assert result["status"] == "failed"
        assert "clean-code" in result["reason"]

    def test_glab_no_pipeline_needs_human(self):
        assert state.target_ci_glab([], None)["status"] == "needs_human"

    def test_glab_newest_pipeline_wins(self):
        pipelines = [{"id": 1, "status": "failed"}, {"id": 2, "status": "success"}]
        assert state.target_ci_glab(pipelines, None)["status"] == "success"

    def test_gh_success_runs(self):
        assert state.target_ci_gh(gh("run-list-commit-success"))["status"] == "success"

    def test_gh_failed_runs(self):
        assert state.target_ci_gh(gh("run-list-failure"))["status"] == "failed"

    def test_gh_in_progress_run_waits(self):
        runs = copy.deepcopy(gh("run-list-commit-success"))
        runs[0]["status"] = "in_progress"
        runs[0]["conclusion"] = ""
        assert state.target_ci_gh(runs)["status"] == "wait"


@pytest.mark.unit
class TestQueuePropose:
    def test_glab_single_open_mr(self):
        assert state.queue_propose("glab", glab("mr-list-open")) == {"provider": "glab", "order": ["!70"], "skipped": []}

    def test_gh_stack_merges_bottom_up(self):
        result = state.queue_propose("gh", gh("pr-list-open"))
        order = result["order"]
        stack = ["#14450", "#14451", "#14452", "#14453", "#14454", "#14455", "#14456"]
        positions = [order.index(ref) for ref in stack]
        assert positions == sorted(positions)

    def test_gh_drafts_and_their_children_are_skipped(self):
        result = state.queue_propose("gh", gh("pr-list-open"))
        skipped = {item["ref"]: item["reason"] for item in result["skipped"]}
        assert skipped["#14348"] == "draft"
        assert "#14349" in skipped
        assert not set(skipped) & set(result["order"])

    def test_every_open_pr_is_either_ordered_or_skipped(self):
        entries = gh("pr-list-open")
        result = state.queue_propose("gh", entries)
        assert len(result["order"]) + len(result["skipped"]) == len(entries)

    def test_entry_without_branch_is_input_error(self):
        entries = copy.deepcopy(gh("pr-list-open"))
        del entries[3]["baseRefName"]
        with pytest.raises(state._InputError, match=r"list-file\[3\]"):
            state.queue_propose("gh", entries)


@pytest.mark.unit
class TestQueueFile:
    def test_set_show_and_next(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        result = state.queue_set(queue_file, ["!72", "!70"])
        assert [item["ref"] for item in result["items"]] == ["!72", "!70"]
        assert result["next"] == "!72"

    def test_set_keeps_state_of_existing_refs(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        state.queue_update(queue_file, "!70", None, "abc", True, None)
        result = state.queue_set(queue_file, ["!72", "!70"])
        item = next(i for i in result["items"] if i["ref"] == "!70")
        assert item["fix_rounds"] == 1
        assert item["user_approved_sha"] == "abc"

    def test_merging_item_goes_before_pending(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        result = state.queue_update(queue_file, "!72", "merging", None, False, "deadbeef")
        assert result["next"] == "!72"

    def test_done_items_are_skipped_by_next(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        result = state.queue_update(queue_file, "!70", "done", None, False, None)
        assert result["next"] == "!72"

    def test_duplicate_ref_is_input_error(self, tmp_path):
        with pytest.raises(state._InputError, match="dwa razy"):
            state.queue_set(tmp_path / "q.json", ["!70", "!70"])

    def test_update_unknown_ref_is_input_error(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70"])
        with pytest.raises(state._InputError, match="nie jest w kolejce"):
            state.queue_update(queue_file, "!99", "done", None, False, None)

    def test_update_unknown_status_is_input_error(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70"])
        with pytest.raises(state._InputError, match="nieznany status"):
            state.queue_update(queue_file, "!70", "wstrzymany", None, False, None)

    @pytest.mark.parametrize(
        "content, message",
        [
            ('{"items": ["!70"]}', r"items\[0\]: element nie jest obiektem"),
            ('{"items": [{"ref": "!70", "provider": "glab", "number": 70}]}', "brak pola 'status'"),
            ('{"items": [{"ref": "!70", "provider": "glab", "number": 70, "status": "x"}]}', "nieznany status"),
            ("[]", "oczekiwano obiektu"),
        ],
    )
    def test_malformed_queue_file_is_input_error(self, tmp_path, content, message):
        queue_file = tmp_path / "mr-queue.json"
        queue_file.write_text(content)
        with pytest.raises(state._InputError, match=message):
            state.queue_update(queue_file, "!70", "done", None, False, None)


@pytest.mark.unit
class TestCli:
    def test_classify_gh_prints_json(self, tmp_path):
        pr_file = write_json(tmp_path, "pr.json", gh("pr-view-approved-conflicting"))
        result = run_cli("classify", "--provider", "gh", "--pr-file", pr_file)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["verdict"] == "conflict"

    def test_classify_glab_requires_approvals_file(self, tmp_path):
        mr_file = write_json(tmp_path, "mr.json", glab("mr-view-merged"))
        result = run_cli("classify", "--provider", "glab", "--mr-file", mr_file)
        assert result.returncode == 64

    def test_classify_glab_prints_json(self, tmp_path):
        mr_file = write_json(tmp_path, "mr.json", glab("mr-view-opened-pipeline-running"))
        approvals_file = write_json(tmp_path, "ap.json", glab("mr-approvals-none-required"))
        result = run_cli("classify", "--provider", "glab", "--mr-file", mr_file, "--approvals-file", approvals_file)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["verdict"] == "wait"

    def test_missing_file_exits_2(self, tmp_path):
        result = run_cli("classify", "--provider", "gh", "--pr-file", str(tmp_path / "brak.json"))
        assert result.returncode == 2
        assert "nie istnieje" in result.stderr

    def test_invalid_json_exits_2(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{nie json")
        assert run_cli("classify", "--provider", "gh", "--pr-file", str(bad)).returncode == 2

    def test_pr_without_required_field_exits_2(self, tmp_path):
        pr = copy.deepcopy(gh("pr-view-merged"))
        del pr["headRefOid"]
        result = run_cli("classify", "--provider", "gh", "--pr-file", write_json(tmp_path, "pr.json", pr))
        assert result.returncode == 2
        assert "headRefOid" in result.stderr

    def test_rollup_element_not_object_exits_2(self, tmp_path):
        pr = copy.deepcopy(gh("pr-view-merged"))
        pr["statusCheckRollup"] = ["SUCCESS"]
        assert run_cli("classify", "--provider", "gh", "--pr-file", write_json(tmp_path, "pr.json", pr)).returncode == 2

    def test_jobs_file_element_not_object_exits_2(self, tmp_path):
        pipelines = write_json(tmp_path, "p.json", glab("pipelines-by-sha-main-manual"))
        jobs = write_json(tmp_path, "j.json", ["success"])
        result = run_cli("target-ci", "--provider", "glab", "--pipelines-file", pipelines, "--jobs-file", jobs)
        assert result.returncode == 2
        assert "jobs-file[0]" in result.stderr

    def test_unknown_ref_exits_2(self, tmp_path):
        result = run_cli("queue-set", "--queue-file", str(tmp_path / "q.json"), "MR-70")
        assert result.returncode == 2

    def test_unknown_subcommand_exits_64(self):
        assert run_cli("merge-everything").returncode == 64

    def test_queue_propose_then_set_roundtrip(self, tmp_path):
        list_file = write_json(tmp_path, "list.json", glab("mr-list-open"))
        proposed = run_cli("queue-propose", "--provider", "glab", "--list-file", list_file)
        assert proposed.returncode == 0, proposed.stderr
        order = json.loads(proposed.stdout)["order"]
        queue_file = str(tmp_path / "monolynx" / "mr-queue.json")
        result = run_cli("queue-set", "--queue-file", queue_file, *order)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["next"] == "!70"
