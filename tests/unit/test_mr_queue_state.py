"""Testy plugin/scripts/mr_queue_state.py - stan kolejki merge /monolynx:mr-queue.

Wejscia to realne wyjscia `glab 1.100.0` i `gh 2.100.0` z tests/fixtures/glab/
i tests/fixtures/gh/ (pole "output"; "_meta" opisuje komende i date zrzutu),
zgodnie z .claude/rules/external-tool-output.md. Warianty, ktorych nie da sie
zlapac bez prawdziwego merge (np. zielony pipeline na otwartym MR), powstaja jako
kopia fixture ze zmienionym JEDNYM polem - reszta ksztaltu zostaje realna.
Kolejnosc w classify: konflikt jest sprawdzany PRZED stanem CI (MON-220), a werdykt
`conflict` niesie pipeline_id/pipeline_status aktywnego pipeline'u HEAD.
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

    @pytest.mark.parametrize("status", ["running", "pending", "created", "waiting_for_resource"])
    def test_conflict_wins_over_active_pipeline_and_carries_it(self, mr_green, no_approvals, status):
        """Konflikt przed CI (MON-220): aktywny pipeline nie maskuje konfliktu, a werdykt niesie jego id i status."""
        mr_green["head_pipeline"]["status"] = status
        mr_green["has_conflicts"] = True
        result = state.classify_glab(mr_green, no_approvals)
        assert result["verdict"] == "conflict"
        assert result["pipeline_id"] == mr_green["head_pipeline"]["id"]
        assert result["pipeline_status"] == status

    @pytest.mark.parametrize("status", ["failed", "canceled", "success"])
    def test_conflict_wins_over_finished_pipeline_without_pipeline_to_cancel(self, mr_green, no_approvals, status):
        """Zakonczony pipeline nie jest do anulowania: pipeline_id i pipeline_status to None (nie id zakonczonego)."""
        mr_green["head_pipeline"]["status"] = status
        mr_green["has_conflicts"] = True
        result = state.classify_glab(mr_green, no_approvals)
        assert result["verdict"] == "conflict"
        assert result["pipeline_id"] is None
        assert result["pipeline_status"] is None

    def test_conflict_without_head_pipeline_is_conflict_with_no_pipeline(self, mr_green, no_approvals):
        mr_green["head_pipeline"] = None
        mr_green["has_conflicts"] = True
        result = state.classify_glab(mr_green, no_approvals)
        assert result["verdict"] == "conflict"
        assert result["pipeline_id"] is None
        assert result["pipeline_status"] is None

    def test_conflict_from_detailed_merge_status_wins_over_running_pipeline(self):
        """MR !70 (realny fixture, pipeline running): has_conflicts=False, ale detailed_merge_status=conflict."""
        mr = copy.deepcopy(glab("mr-view-opened-pipeline-running"))
        assert mr["has_conflicts"] is False
        mr["detailed_merge_status"] = "conflict"
        result = state.classify_glab(mr, glab("mr-approvals-none-required"))
        assert result["verdict"] == "conflict"
        assert result["pipeline_id"] == mr["head_pipeline"]["id"]
        assert result["pipeline_status"] == "running"

    def test_need_rebase_wins_over_running_pipeline(self):
        mr = copy.deepcopy(glab("mr-view-opened-pipeline-running"))
        mr["detailed_merge_status"] = "need_rebase"
        assert state.classify_glab(mr, glab("mr-approvals-none-required"))["verdict"] == "conflict"

    def test_draft_wins_over_conflict(self, mr_green, no_approvals):
        mr_green["draft"] = True
        mr_green["has_conflicts"] = True
        assert state.classify_glab(mr_green, no_approvals)["verdict"] == "needs_human"

    def test_canceled_pipeline_without_conflict_needs_human_and_is_not_retried(self, mr_green, no_approvals):
        """Anulowany pipeline HEAD bez konfliktu: needs_human (czlowiek anulowal celowo), nie ci_failed ani wait."""
        mr_green["head_pipeline"]["status"] = "canceled"
        assert mr_green["has_conflicts"] is False
        result = state.classify_glab(mr_green, no_approvals)
        assert result["verdict"] == "needs_human"
        assert "canceled" in result["reason"]
        assert "pipeline_id" not in result

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

    def test_conflict_wins_over_running_checks(self):
        """Wariant pr-view-approved-conflicting (CONFLICTING/DIRTY) z statusCheckRollup z pr-view-statuscontext-pending."""
        pr = copy.deepcopy(gh("pr-view-approved-conflicting"))
        pr["statusCheckRollup"] = copy.deepcopy(gh("pr-view-statuscontext-pending")["statusCheckRollup"])
        result = state.classify_gh(pr)
        assert result["verdict"] == "conflict"
        assert result["pipeline_id"] is None
        assert result["pipeline_status"] == "running"

    def test_conflict_wins_over_failed_checks(self):
        """Wariant pr-view-draft-conflicting-check-failure (CONFLICTING/DIRTY, check FAILURE) z isDraft=False."""
        pr = copy.deepcopy(gh("pr-view-draft-conflicting-check-failure"))
        pr["isDraft"] = False
        result = state.classify_gh(pr)
        assert result["verdict"] == "conflict"
        assert result["pipeline_id"] is None
        assert result["pipeline_status"] is None

    def test_conflict_with_green_checks_has_no_pipeline(self):
        result = state.classify_gh(gh("pr-view-approved-conflicting"))
        assert result["verdict"] == "conflict"
        assert result["pipeline_id"] is None
        assert result["pipeline_status"] is None

    @pytest.mark.parametrize(
        "mergeable, merge_state",
        [("CONFLICTING", "DIRTY"), ("CONFLICTING", "BLOCKED"), ("MERGEABLE", "DIRTY")],
    )
    def test_each_conflict_signal_alone_wins_over_running_checks(self, mergeable, merge_state):
        """pr-view-statuscontext-pending (checki w toku) ze zmienionymi mergeable/mergeStateStatus: kazdy sygnal osobno."""
        pr = copy.deepcopy(gh("pr-view-statuscontext-pending"))
        pr["mergeable"] = mergeable
        pr["mergeStateStatus"] = merge_state
        result = state.classify_gh(pr)
        assert result["verdict"] == "conflict"
        assert result["pipeline_status"] == "running"

    def test_behind_does_not_outrank_failed_checks(self):
        """pr-view-draft-conflicting-check-failure z isDraft=False, mergeable=MERGEABLE, BEHIND: CI przed BEHIND."""
        pr = copy.deepcopy(gh("pr-view-draft-conflicting-check-failure"))
        pr["isDraft"] = False
        pr["mergeable"] = "MERGEABLE"
        pr["mergeStateStatus"] = "BEHIND"
        assert state.classify_gh(pr)["verdict"] == "ci_failed"

    def test_behind_with_green_checks_is_conflict(self):
        """BEHIND bez CONFLICTING zostaje za CI: zielone checki -> conflict (wciagniecie brancha docelowego)."""
        pr = copy.deepcopy(gh("pr-view-mergeable-review-required"))
        pr["mergeStateStatus"] = "BEHIND"
        assert state.classify_gh(pr)["verdict"] == "conflict"


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
class TestQueueAppend:
    """MON-142: kolejne ticki dopisuja nowe otwarte MR/PR na koniec kolejki."""

    def test_appends_only_missing_refs_in_proposed_order(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["#14451", "#14450"])
        state.queue_update(queue_file, "#14451", None, None, True, None)

        result = state.queue_append(queue_file, "gh", gh("pr-list-open"))

        proposed = state.queue_propose("gh", gh("pr-list-open"))["order"]
        expected_appended = [ref for ref in proposed if ref not in ("#14450", "#14451")]
        assert result["appended"] == expected_appended
        refs = [item["ref"] for item in result["items"]]
        assert refs == ["#14451", "#14450", *expected_appended]
        assert result["items"][0]["fix_rounds"] == 1
        assert result["next"] == "#14451"
        new_item = next(item for item in result["items"] if item["ref"] == expected_appended[0])
        assert new_item["status"] == "pending"
        assert new_item["provider"] == "gh"
        assert new_item["number"] == int(expected_appended[0][1:])

    def test_stacked_child_of_queued_mr_lands_after_it(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["#14450"])

        refs = [item["ref"] for item in state.queue_append(queue_file, "gh", gh("pr-list-open"))["items"]]

        stack = ["#14450", "#14451", "#14452", "#14453", "#14454", "#14455", "#14456"]
        positions = [refs.index(ref) for ref in stack]
        assert positions == sorted(positions)

    @pytest.mark.parametrize("status", ["stopped", "done", "closed"])
    def test_ref_already_in_queue_is_never_appended_again(self, tmp_path, status):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["#14447"])
        state.queue_update(queue_file, "#14447", status, None, False, None)

        result = state.queue_append(queue_file, "gh", gh("pr-list-open"))

        assert "#14447" not in result["appended"]
        matching = [item for item in result["items"] if item["ref"] == "#14447"]
        assert len(matching) == 1
        assert matching[0]["status"] == status

    def test_nothing_new_leaves_queue_file_untouched(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70"])
        before = queue_file.read_text()

        result = state.queue_append(queue_file, "glab", glab("mr-list-open"))

        assert result["appended"] == []
        assert result["next"] == "!70"
        assert queue_file.read_text() == before

    def test_missing_queue_file_gets_whole_proposed_order(self, tmp_path):
        queue_file = tmp_path / "monolynx" / "mr-queue.json"

        result = state.queue_append(queue_file, "glab", glab("mr-list-open"))

        assert result["appended"] == ["!70"]
        assert result["next"] == "!70"
        assert json.loads(queue_file.read_text())["items"][0]["ref"] == "!70"

    def test_skipped_lists_only_refs_outside_queue(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["#14348"])

        result = state.queue_append(queue_file, "gh", gh("pr-list-open"))

        skipped = {item["ref"] for item in result["skipped"]}
        assert "#14348" not in skipped
        assert "#14349" in skipped
        assert not skipped & set(result["appended"])

    def test_cli_appends_and_prints_json(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        list_file = write_json(tmp_path, "list.json", glab("mr-list-open"))

        result = run_cli("queue-append", "--queue-file", str(queue_file), "--provider", "glab", "--list-file", list_file)

        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["appended"] == ["!70"]

    def test_cli_bad_list_element_exits_2_without_writing(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!72"])
        before = queue_file.read_text()
        entries = copy.deepcopy(glab("mr-list-open"))
        del entries[0]["source_branch"]
        list_file = write_json(tmp_path, "list.json", entries)

        result = run_cli("queue-append", "--queue-file", str(queue_file), "--provider", "glab", "--list-file", list_file)

        assert result.returncode == 2
        assert "list-file[0]" in result.stderr
        assert queue_file.read_text() == before

    def test_cli_without_provider_exits_64(self, tmp_path):
        list_file = write_json(tmp_path, "list.json", glab("mr-list-open"))
        result = run_cli("queue-append", "--queue-file", str(tmp_path / "q.json"), "--list-file", list_file)
        assert result.returncode == 64


@pytest.mark.unit
class TestCli:
    def test_classify_gh_prints_json(self, tmp_path):
        pr_file = write_json(tmp_path, "pr.json", gh("pr-view-approved-conflicting"))
        result = run_cli("classify", "--provider", "gh", "--pr-file", pr_file)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["verdict"] == "conflict"

    def test_classify_glab_conflict_with_running_pipeline_prints_pipeline_id(self, tmp_path):
        mr = copy.deepcopy(glab("mr-view-opened-pipeline-running"))
        mr["has_conflicts"] = True
        mr_file = write_json(tmp_path, "mr.json", mr)
        approvals_file = write_json(tmp_path, "ap.json", glab("mr-approvals-none-required"))
        result = run_cli("classify", "--provider", "glab", "--mr-file", mr_file, "--approvals-file", approvals_file)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["verdict"] == "conflict"
        assert out["pipeline_id"] == mr["head_pipeline"]["id"]
        assert out["pipeline_status"] == "running"

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


@pytest.mark.unit
class TestTicketKey:
    """Klucz ticketu (regex [A-Z]+-[0-9]+, re.search, pierwsze dopasowanie) z brancha zrodlowego
    albo tytulu MR. Branche wziete z realnej historii tego repo (git log: 'worktree-MON-192',
    'MON-190-sprint-run-fixes'); tytul MR wziety z fixture'a mr-view-merged."""

    def test_source_branch_with_worktree_prefix_needs_search_not_match(self):
        """'worktree-MON-192' nie zaczyna sie od klucza - re.match (kotwiczony na starcie) by go nie zlapal."""
        result = run_cli("ticket-key", "--source-branch", "worktree-MON-192")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"key": "MON-192"}

    def test_source_branch_with_ticket_key_prefix(self):
        result = run_cli("ticket-key", "--source-branch", "MON-190-sprint-run-fixes")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"key": "MON-190"}

    def test_title_from_real_mr_fixture(self):
        mr = glab("mr-view-merged")
        result = run_cli("ticket-key", "--title", mr["title"])
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"key": "MON-152"}

    def test_source_branch_takes_precedence_over_different_title_key(self):
        mr = glab("mr-view-merged")
        result = run_cli(
            "ticket-key",
            "--source-branch",
            "worktree-MON-192",
            "--title",
            mr["title"],
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"key": "MON-192"}

    def test_source_branch_without_key_falls_back_to_title(self):
        """Odroznia fallback ('brak w branchu -> szukaj w tytule') od twardej precedencji
        ('jesli podano branch, uzyj wylacznie jego wyniku, nawet null')."""
        source_branch = glab("mr-view-opened-pipeline-running")["source_branch"]
        title = glab("mr-view-merged")["title"]
        result = run_cli("ticket-key", "--source-branch", source_branch, "--title", title)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"key": "MON-152"}

    def test_no_key_in_source_branch_or_title(self):
        """feat/sprint-model-selection i jego tytul - realny przypadek bez klucza ticketu."""
        mr = glab("mr-view-opened-pipeline-running")
        result = run_cli(
            "ticket-key",
            "--source-branch",
            mr["source_branch"],
            "--title",
            mr["title"],
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"key": None}

    def test_no_arguments_returns_null_key(self):
        result = run_cli("ticket-key")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"key": None}


GIT_FIXTURE = FIXTURES / "git" / "rev-list-left-right-count.2.40.1.txt"


def git_revlist_counts():
    """(lewa, prawa) z realnego wyjscia `git rev-list --left-right --count` (linie `#` to meta)."""
    lines = [ln for ln in GIT_FIXTURE.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert len(lines) == 1
    left, right = lines[0].split()
    return int(left), int(right)


def target_sync(target="main", current="main", ahead=0, behind=1, dirty="false"):
    result = run_cli(
        "target-sync",
        "--target-branch",
        target,
        "--current-branch",
        current,
        "--ahead",
        str(ahead),
        "--behind",
        str(behind),
        "--dirty",
        dirty,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def assert_no_destructive_git(payload):
    texts = [*payload["commands"], payload["manual"] or ""]
    for text in texts:
        tokens = text.split()
        for forbidden in ("reset", "rebase", "stash", "--force", "-f", "--force-with-lease"):
            assert forbidden not in tokens, f"{forbidden!r} w {text!r}"
        assert "+" not in text, f"refspec z + w {text!r}"


@pytest.mark.unit
class TestTargetSync:
    def test_git_fixture_is_real_capture_with_meta(self):
        header = [ln for ln in GIT_FIXTURE.read_text().splitlines() if ln.startswith("#")]
        assert any("git rev-list --left-right --count" in ln for ln in header)
        assert any("git 2." in ln for ln in header)
        assert any("captured" in ln for ln in header)

    def test_up_to_date_from_real_fixture_counts_is_none(self):
        ahead, behind = git_revlist_counts()
        payload = target_sync(ahead=ahead, behind=behind)
        assert payload["action"] == "none"
        assert payload["commands"] == []
        assert payload["manual"] is None

    def test_up_to_date_wins_over_dirty(self):
        payload = target_sync(ahead=0, behind=0, dirty="true")
        assert payload["action"] == "none"
        assert payload["commands"] == []
        assert payload["manual"] is None

    def test_on_target_clean_behind_is_ff_merge_with_exact_command(self):
        payload = target_sync(target="main", current="main", ahead=0, behind=3, dirty="false")
        assert payload["action"] == "ff-merge"
        assert payload["commands"] == ["git merge --ff-only origin/main"]
        assert payload["manual"] is None

    def test_ff_merge_uses_the_target_branch_name(self):
        payload = target_sync(target="sprint/cli", current="sprint/cli", behind=1)
        assert payload["commands"] == ["git merge --ff-only origin/sprint/cli"]

    def test_on_target_dirty_skips_with_manual_ff_merge(self):
        payload = target_sync(target="main", current="main", ahead=0, behind=2, dirty="true")
        assert payload["action"] == "skip"
        assert payload["commands"] == []
        assert payload["manual"] == "git merge --ff-only origin/main"

    def test_on_other_branch_is_ff_fetch_with_exact_command(self):
        payload = target_sync(target="main", current="feature-x", ahead=0, behind=2)
        assert payload["action"] == "ff-fetch"
        assert payload["commands"] == ["git fetch origin main:main"]
        assert payload["manual"] is None

    def test_ff_fetch_refspec_has_no_plus_prefix(self):
        payload = target_sync(target="sprint/cli", current="main", behind=1)
        assert payload["commands"] == ["git fetch origin sprint/cli:sprint/cli"]

    def test_detached_head_is_treated_like_other_branch(self):
        payload = target_sync(target="main", current="HEAD", behind=1)
        assert payload["action"] == "ff-fetch"
        assert payload["commands"] == ["git fetch origin main:main"]

    def test_dirty_does_not_block_ff_fetch_on_other_branch(self):
        payload = target_sync(target="main", current="feature-x", behind=1, dirty="true")
        assert payload["action"] == "ff-fetch"
        assert payload["commands"] == ["git fetch origin main:main"]

    @pytest.mark.parametrize("current", ["main", "feature-x", "HEAD"])
    @pytest.mark.parametrize("dirty", ["true", "false"])
    @pytest.mark.parametrize("behind", [0, 4])
    def test_local_commits_skip_without_commands_and_never_reset(self, current, dirty, behind):
        payload = target_sync(target="main", current=current, ahead=2, behind=behind, dirty=dirty)
        assert payload["action"] == "skip"
        assert payload["commands"] == []
        assert isinstance(payload["manual"], str) and payload["manual"].strip()
        assert "main" in payload["manual"]
        assert_no_destructive_git(payload)

    def test_ahead_only_is_skip_and_behind_only_is_ff(self):
        """Rozroznia ahead od behind: zamiana kolumn odwrocilaby oba wyniki."""
        assert target_sync(ahead=5, behind=0)["action"] == "skip"
        assert target_sync(ahead=0, behind=5)["action"] == "ff-merge"

    def test_manual_for_local_commits_is_not_the_ff_command(self):
        payload = target_sync(ahead=1, behind=1, dirty="false")
        assert payload["manual"] != "git merge --ff-only origin/main"

    @pytest.mark.parametrize("target", ["main", "sprint/cli-monolynx"])
    @pytest.mark.parametrize("current", ["main", "sprint/cli-monolynx", "other", "HEAD"])
    @pytest.mark.parametrize("dirty", ["true", "false"])
    @pytest.mark.parametrize("ahead", [0, 1, 3])
    @pytest.mark.parametrize("behind", [0, 1, 3])
    def test_no_result_contains_destructive_git(self, target, current, dirty, ahead, behind):
        assert_no_destructive_git(target_sync(target, current, ahead, behind, dirty))

    @pytest.mark.parametrize("target", ["", "   "])
    def test_blank_target_branch_exits_2(self, target):
        result = run_cli("target-sync", "--target-branch", target, "--current-branch", "main", "--ahead", "0", "--behind", "1", "--dirty", "false")
        assert result.returncode == 2
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize("flag", ["--ahead", "--behind"])
    def test_negative_count_exits_2(self, flag):
        values = {"--ahead": "0", "--behind": "1"}
        values[flag] = "-1"
        result = run_cli(
            "target-sync",
            "--target-branch",
            "main",
            "--current-branch",
            "main",
            f"--ahead={values['--ahead']}",
            f"--behind={values['--behind']}",
            "--dirty",
            "false",
        )
        assert result.returncode == 2
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize("dirty", ["maybe", "", "1", "TRUE"])
    def test_dirty_outside_true_false_exits_2_and_never_ff(self, dirty):
        result = run_cli("target-sync", "--target-branch", "main", "--current-branch", "main", "--ahead", "0", "--behind", "1", "--dirty", dirty)
        assert result.returncode == 2
        assert "ff-" not in result.stdout

    @pytest.mark.parametrize("missing", ["--target-branch", "--current-branch", "--ahead", "--behind", "--dirty"])
    def test_missing_required_argument_exits_64(self, missing):
        full = {
            "--target-branch": "main",
            "--current-branch": "main",
            "--ahead": "0",
            "--behind": "1",
            "--dirty": "false",
        }
        del full[missing]
        argv = [item for pair in full.items() for item in pair]
        assert run_cli("target-sync", *argv).returncode == 64

    def test_non_integer_count_exits_64(self):
        result = run_cli("target-sync", "--target-branch", "main", "--current-branch", "main", "--ahead", "x", "--behind", "1", "--dirty", "false")
        assert result.returncode == 64


def config_check(base="", target=""):
    return run_cli("config-check", "--base-branch", base, "--mr-target", target)


@pytest.mark.unit
class TestConfigCheck:
    def test_different_values_exit_2_and_name_both_variables(self):
        result = config_check("develop", "release")
        assert result.returncode == 2
        assert "MONOLYNX_BASE_BRANCH" in result.stderr
        assert "MONOLYNX_MR_TARGET" in result.stderr
        assert "develop" in result.stderr
        assert "release" in result.stderr
        assert result.stdout.strip() == ""

    def test_equal_values_exit_0_with_target(self):
        result = config_check("develop", "develop")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"target": "develop"}

    def test_only_base_branch_gives_its_value(self):
        result = config_check(base="develop")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"target": "develop"}

    def test_only_mr_target_gives_its_value(self):
        result = config_check(target="release")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"target": "release"}

    def test_both_empty_gives_null_target(self):
        result = config_check()
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"target": None}

    def test_no_arguments_gives_null_target(self):
        result = run_cli("config-check")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"target": None}

    def test_whitespace_only_value_counts_as_empty(self):
        result = config_check("  ", "release")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"target": "release"}

    def test_values_differing_only_by_surrounding_whitespace_are_equal(self):
        result = config_check(" develop ", "develop\t")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"target": "develop"}

    def test_target_is_stripped(self):
        result = config_check(" develop ", "")
        assert json.loads(result.stdout) == {"target": "develop"}

    def test_case_difference_is_a_difference(self):
        assert config_check("Develop", "develop").returncode == 2


# --- MON-221: kolejka na branchu integracyjnym nie czeka na CI brancha docelowego ---
#
# Fixture'y sprintu (tests/fixtures/glab/*sprint-success*, repo-view-default-branch)
# to realne przechwycenie z 2026-09-29 (pipeline commita MON-220 na
# sprint/cli-monolynx). Realnego pipeline'u `failed` na branchu sprintu w chwili
# zrzutu nie bylo - wariant czerwony to kopia fixture'a z JEDNYM zmienionym polem
# (status pipeline'u) plus realne joby `pipeline-jobs-failed`.
# Kontrakt `failed_jobs`: pole jest TYLKO w wyniku o statusie `failed`
# (w `success`/`wait`/`needs_human` go nie ma).


def cli_json(*args):
    result = run_cli(*args)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def cli_update(queue_file, ref, *extra):
    return cli_json("queue-update", "--queue-file", str(queue_file), "--ref", ref, *extra)


def cli_show(queue_file):
    return cli_json("queue-show", "--queue-file", str(queue_file))


def item_of(result, ref):
    return next(i for i in result["items"] if i["ref"] == ref)


@pytest.fixture
def sprint_default_branch():
    return glab("repo-view-default-branch")["default_branch"]


@pytest.mark.unit
class TestSprintFixtures:
    def test_sprint_pipeline_fixture_is_on_integration_branch(self):
        pipelines = glab("pipelines-by-sha-sprint-success")
        assert pipelines[0]["ref"] == "sprint/cli-monolynx"
        assert pipelines[0]["status"] == "success"

    def test_sprint_jobs_runner_ip_is_redacted(self):
        for job in glab("pipeline-jobs-sprint-success"):
            manager = job.get("runner_manager") or {}
            assert manager.get("ip_address") in (None, "REDACTED")


@pytest.mark.unit
class TestPostMerge:
    """post-merge (MON-233): branch integracyjny -> done + close_now, inaczej (tez przy pustych wartosciach) merging."""

    @pytest.mark.parametrize(
        "target, default",
        [
            ("sprint/cli-monolynx", "main"),
            ("sprint/cli-monolynx", " main "),
            (" sprint/cli-monolynx ", "main"),
        ],
    )
    def test_integration_branch_closes_item_now(self, target, default):
        out = cli_json("post-merge", "--target-branch", target, "--default-branch", default)
        assert out["status"] != "verifying"
        assert out["status"] == "done"
        assert out["close_now"] is True
        assert isinstance(out["reason"], str) and out["reason"]

    @pytest.mark.parametrize(
        "target, default",
        [
            ("main", "main"),
            (" main ", "main"),
            ("main", " main "),
            ("sprint/cli-monolynx", ""),
            ("sprint/cli-monolynx", "   "),
            ("", "main"),
            ("   ", "main"),
            ("", ""),
        ],
    )
    def test_default_or_unknown_branch_keeps_merging_without_close_now(self, target, default):
        out = cli_json("post-merge", "--target-branch", target, "--default-branch", default)
        assert out["status"] == "merging"
        assert out["close_now"] is False
        assert isinstance(out["reason"], str) and out["reason"]

    def test_function_returns_done_for_integration_branch(self):
        out = state.post_merge("sprint/cli-monolynx", "main")
        assert (out["status"], out["close_now"]) == ("done", True)

    def test_function_returns_merging_for_default_branch(self):
        out = state.post_merge("main", "main")
        assert (out["status"], out["close_now"]) == ("merging", False)

    def test_default_branch_from_repo_view_fixture(self, sprint_default_branch):
        integration = cli_json("post-merge", "--target-branch", "sprint/cli-monolynx", "--default-branch", sprint_default_branch)
        default = cli_json("post-merge", "--target-branch", sprint_default_branch, "--default-branch", sprint_default_branch)
        assert integration["status"] == "done"
        assert integration["close_now"] is True
        assert default["status"] == "merging"
        assert default["close_now"] is False

    def test_missing_default_branch_flag_exits_64(self):
        assert run_cli("post-merge", "--target-branch", "sprint/cli-monolynx").returncode == 64

    def test_missing_target_branch_flag_exits_64(self):
        assert run_cli("post-merge", "--default-branch", "main").returncode == 64

    @pytest.mark.parametrize("target, expected", [("sprint/cli-monolynx", "done"), ("main", "merging")])
    def test_status_is_accepted_by_queue_update(self, tmp_path, target, expected):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        status = cli_json("post-merge", "--target-branch", target, "--default-branch", "main")["status"]
        result = cli_update(queue_file, "!70", "--status", status, "--merge-sha", "deadbeef")
        assert item_of(result, "!70")["status"] == expected

    def test_integration_branch_merge_does_not_block_next_item(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        status = cli_json("post-merge", "--target-branch", "sprint/cli-monolynx", "--default-branch", "main")["status"]
        result = cli_update(queue_file, "!70", "--status", status, "--merge-sha", "deadbeef")
        assert result["next"] == "!72"
        assert result["verifying"] == []
        assert result["halted"] is None

    def test_default_branch_merge_blocks_next_item_until_ci(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        status = cli_json("post-merge", "--target-branch", "main", "--default-branch", "main")["status"]
        result = cli_update(queue_file, "!70", "--status", status, "--merge-sha", "deadbeef")
        assert result["next"] == "!70"


@pytest.mark.unit
class TestVerifyingStatus:
    """Status `verifying` to tylko zastany stan w pliku kolejki (format czytelny dla obu wersji).

    Od MON-233 `post-merge` go nie zwraca; testy opisuja, jak skrypt traktuje pozycje
    zapisane wczesniej: nie blokuja kolejki, nie wchodza do `next` i daja sie domknac.
    """

    def test_stale_verifying_item_is_closed_by_done_without_reading_ci(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        data = json.loads(queue_file.read_text())
        data["items"][0]["status"] = "verifying"
        data["items"][0]["merge_sha"] = "deadbeef"
        queue_file.write_text(json.dumps(data))
        assert cli_show(queue_file)["verifying"] == ["!70"]

        result = cli_update(queue_file, "!70", "--status", "done")

        assert result["verifying"] == []
        assert item_of(result, "!70")["status"] == "done"
        assert item_of(result, "!70")["merge_sha"] == "deadbeef"
        assert result["next"] == "!72"
        assert cli_show(queue_file)["verifying"] == []

    def test_verifying_is_accepted_and_stores_merge_sha(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        result = cli_update(queue_file, "!70", "--status", "verifying", "--merge-sha", "deadbeef")
        item = item_of(result, "!70")
        assert item["status"] == "verifying"
        assert item["merge_sha"] == "deadbeef"

    def test_verifying_item_is_not_next_pending_is(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        result = state.queue_update(queue_file, "!70", "verifying", None, False, "deadbeef")
        assert result["next"] == "!72"
        assert result["verifying"] == ["!70"]

    def test_verifying_list_keeps_file_order(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72", "!74"])
        state.queue_update(queue_file, "!74", "verifying", None, False, "aaa")
        result = state.queue_update(queue_file, "!70", "verifying", None, False, "bbb")
        assert result["verifying"] == ["!70", "!74"]
        assert result["next"] == "!72"

    def test_only_verifying_items_leave_no_next_and_no_halt(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70"])
        result = state.queue_update(queue_file, "!70", "verifying", None, False, "deadbeef")
        assert result["next"] is None
        assert result["halted"] is None

    def test_merging_still_wins_over_verifying_and_pending(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72", "!74"])
        state.queue_update(queue_file, "!70", "verifying", None, False, "aaa")
        result = state.queue_update(queue_file, "!72", "merging", None, False, "bbb")
        assert result["next"] == "!72"
        assert result["verifying"] == ["!70"]

    def test_queue_show_carries_verifying_list_and_halted(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        cli_update(queue_file, "!70", "--status", "verifying", "--merge-sha", "deadbeef")
        shown = cli_show(queue_file)
        assert shown["verifying"] == ["!70"]
        assert shown["halted"] is None
        assert shown["next"] == "!72"

    def test_verifying_done_leaves_verifying_list(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70"])
        state.queue_update(queue_file, "!70", "verifying", None, False, "deadbeef")
        result = state.queue_update(queue_file, "!70", "done", None, False, None)
        assert result["verifying"] == []
        assert item_of(result, "!70")["merge_sha"] == "deadbeef"


@pytest.mark.unit
class TestDefaultBranchBehaviourUnchanged:
    """Kryterium 4 MON-221: na domyslnym branchu (main) zachowanie kolejki bez zmian."""

    def test_merging_item_still_blocks_pending(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        result = state.queue_update(queue_file, "!70", "merging", None, False, "deadbeef")
        assert result["next"] == "!70"
        assert result["halted"] is None
        assert result["verifying"] == []

    def test_merging_item_listed_later_still_goes_before_pending(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        result = state.queue_update(queue_file, "!72", "merging", None, False, "deadbeef")
        assert result["next"] == "!72"

    def test_stopped_without_stop_reason_does_not_halt_queue(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        result = state.queue_update(queue_file, "!70", "stopped", None, False, None)
        assert item_of(result, "!70")["status"] == "stopped"
        assert result["next"] == "!72"
        assert result["halted"] is None

    def test_stopped_via_cli_without_stop_reason_does_not_halt_queue(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        cli_update(queue_file, "!70", "--status", "stopped")
        shown = cli_show(queue_file)
        assert shown["next"] == "!72"
        assert shown["halted"] is None

    def test_plain_queue_has_empty_verifying_and_no_halt(self, tmp_path):
        result = state.queue_set(tmp_path / "mr-queue.json", ["!70"])
        assert result["verifying"] == []
        assert result["halted"] is None
        assert result["next"] == "!70"

    def test_default_branch_target_never_yields_verifying(self):
        out = cli_json("post-merge", "--target-branch", "main", "--default-branch", "main")
        assert out["status"] == "merging"


@pytest.mark.unit
class TestStopReason:
    def test_target_ci_failed_halts_queue_despite_pending(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        result = cli_update(queue_file, "!70", "--status", "stopped", "--stop-reason", "target_ci_failed")
        assert item_of(result, "!70")["stop_reason"] == "target_ci_failed"
        assert result["next"] is None
        assert result["halted"] == "!70"

    def test_halt_is_persisted_in_queue_file(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        cli_update(queue_file, "!70", "--status", "stopped", "--stop-reason", "target_ci_failed")
        shown = cli_show(queue_file)
        assert shown["next"] is None
        assert shown["halted"] == "!70"

    def test_first_halting_item_in_file_order_is_reported(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72", "!74"])
        cli_update(queue_file, "!72", "--status", "stopped", "--stop-reason", "target_ci_failed")
        result = cli_update(queue_file, "!70", "--status", "stopped", "--stop-reason", "target_ci_failed")
        assert result["halted"] == "!70"

    def test_other_stop_reason_does_not_halt_queue(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        result = cli_update(queue_file, "!70", "--status", "stopped", "--stop-reason", "needs_review")
        assert item_of(result, "!70")["stop_reason"] == "needs_review"
        assert result["next"] == "!72"
        assert result["halted"] is None

    @pytest.mark.parametrize("new_status", ["closed", "done", "pending"])
    def test_leaving_stopped_clears_stop_reason_and_releases_queue(self, tmp_path, new_status):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        cli_update(queue_file, "!70", "--status", "stopped", "--stop-reason", "target_ci_failed")
        result = cli_update(queue_file, "!70", "--status", new_status)
        assert item_of(result, "!70")["stop_reason"] is None
        assert result["halted"] is None
        assert result["next"] == ("!70" if new_status == "pending" else "!72")

    def test_update_without_status_keeps_stop_reason(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        cli_update(queue_file, "!70", "--status", "stopped", "--stop-reason", "target_ci_failed")
        result = cli_update(queue_file, "!70", "--fix-round")
        assert item_of(result, "!70")["stop_reason"] == "target_ci_failed"
        assert result["halted"] == "!70"
        assert result["next"] is None


@pytest.mark.unit
class TestQueueSetKeepsHaltingItem:
    """MON-228 A5: queue-set nie gubi pozycji `stopped` + `target_ci_failed` spoza nowej listy.

    Bez tego ponowne `queue-set` (np. po odswiezeniu propozycji) kasowalo zatrzymanie kolejki
    i czerwony commit merge na branchu integracyjnym przestawal blokowac kolejne MR (MON-221).
    """

    def test_halting_item_outside_new_list_is_kept_and_keeps_halting(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72", "!74"])
        state.queue_update(queue_file, "!72", "stopped", None, False, None, "target_ci_failed")

        result = state.queue_set(queue_file, ["!70", "!74"])

        halting = item_of(result, "!72")
        assert halting["status"] == "stopped"
        assert halting["stop_reason"] == "target_ci_failed"
        assert result["halted"] == "!72"
        assert result["next"] is None

    def test_kept_halting_item_survives_in_queue_file(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        cli_update(queue_file, "!72", "--status", "stopped", "--stop-reason", "target_ci_failed")
        state.queue_set(queue_file, ["!70"])

        shown = cli_show(queue_file)

        assert shown["halted"] == "!72"
        assert shown["next"] is None
        assert {item["ref"] for item in shown["items"]} == {"!70", "!72"}

    def test_halting_item_inside_new_list_keeps_status_and_reason(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        state.queue_update(queue_file, "!72", "stopped", None, False, None, "target_ci_failed")

        result = state.queue_set(queue_file, ["!72", "!70"])

        assert item_of(result, "!72")["stop_reason"] == "target_ci_failed"
        assert result["halted"] == "!72"
        assert [item["ref"] for item in result["items"]].count("!72") == 1

    def test_halting_and_verifying_items_outside_new_list_are_both_kept(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72", "!74"])
        state.queue_update(queue_file, "!72", "stopped", None, False, None, "target_ci_failed")
        state.queue_update(queue_file, "!74", "verifying", None, False, "deadbeef")

        result = state.queue_set(queue_file, ["!70"])

        assert {item["ref"] for item in result["items"]} == {"!70", "!72", "!74"}
        assert result["halted"] == "!72"
        assert result["verifying"] == ["!74"]

    @pytest.mark.parametrize("stop_reason", ["needs_review", None])
    def test_stopped_item_with_other_reason_outside_new_list_is_dropped(self, tmp_path, stop_reason):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        state.queue_update(queue_file, "!72", "stopped", None, False, None, stop_reason)

        result = state.queue_set(queue_file, ["!70"])

        assert [item["ref"] for item in result["items"]] == ["!70"]
        assert result["halted"] is None
        assert result["next"] == "!70"

    @pytest.mark.parametrize("status", ["done", "closed"])
    def test_non_halting_non_verifying_item_outside_new_list_is_dropped(self, tmp_path, status):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        state.queue_update(queue_file, "!72", status, None, False, None)

        result = state.queue_set(queue_file, ["!70"])

        assert [item["ref"] for item in result["items"]] == ["!70"]

    def test_released_halting_item_outside_new_list_is_dropped(self, tmp_path):
        # Po odblokowaniu przez czlowieka (status wraca do pending, stop_reason wyczyszczony)
        # pozycja spoza listy jest zwykla pozycja i znika.
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        state.queue_update(queue_file, "!72", "stopped", None, False, None, "target_ci_failed")
        state.queue_update(queue_file, "!72", "pending", None, False, None)

        result = state.queue_set(queue_file, ["!70"])

        assert [item["ref"] for item in result["items"]] == ["!70"]
        assert result["halted"] is None

    def test_cleared_stop_reason_is_persisted(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        cli_update(queue_file, "!70", "--status", "stopped", "--stop-reason", "target_ci_failed")
        cli_update(queue_file, "!70", "--status", "closed")
        assert json.loads(queue_file.read_text())["items"][0]["stop_reason"] is None

    @pytest.mark.parametrize("bad", [5, ["target_ci_failed"], {"a": 1}, True])
    def test_non_string_stop_reason_in_file_exits_2(self, tmp_path, bad):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70"])
        data = json.loads(queue_file.read_text())
        data["items"][0]["stop_reason"] = bad
        queue_file.write_text(json.dumps(data))
        result = run_cli("queue-show", "--queue-file", str(queue_file))
        assert result.returncode == 2
        assert "stop_reason" in result.stderr

    def test_missing_stop_reason_key_in_old_queue_file_is_accepted(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70"])
        data = json.loads(queue_file.read_text())
        del data["items"][0]["stop_reason"]
        queue_file.write_text(json.dumps(data))
        assert cli_show(queue_file)["next"] == "!70"

    def test_null_and_string_stop_reason_in_file_are_accepted(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        data = json.loads(queue_file.read_text())
        data["items"][0]["stop_reason"] = None
        data["items"][1]["stop_reason"] = "target_ci_failed"
        queue_file.write_text(json.dumps(data))
        assert cli_show(queue_file)["items"][1]["stop_reason"] == "target_ci_failed"


@pytest.mark.unit
class TestNewItemsStopReason:
    def test_queue_set_items_have_null_stop_reason(self, tmp_path):
        result = state.queue_set(tmp_path / "mr-queue.json", ["!70", "!72"])
        assert [i["stop_reason"] for i in result["items"]] == [None, None]

    def test_queue_append_items_have_null_stop_reason(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["#14451"])
        result = state.queue_append(queue_file, "gh", gh("pr-list-open"))
        assert result["appended"]
        appended = [i for i in result["items"] if i["ref"] in result["appended"]]
        assert appended
        assert all(i["stop_reason"] is None for i in appended)

    def test_queue_set_keeps_stop_reason_of_existing_ref(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        cli_update(queue_file, "!70", "--status", "stopped", "--stop-reason", "target_ci_failed")
        result = state.queue_set(queue_file, ["!72", "!70"])
        assert item_of(result, "!70")["stop_reason"] == "target_ci_failed"
        assert result["halted"] == "!70"


@pytest.mark.unit
class TestQueueSetKeepsVerifying:
    """Zastane pozycje `verifying` (sprzed MON-233) przezywaja queue-set, zeby tick mogl je domknac."""

    def test_verifying_outside_new_list_is_kept_at_front_in_old_order(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72", "!74"])
        state.queue_update(queue_file, "!74", "verifying", None, False, "sha74")
        state.queue_update(queue_file, "!70", "verifying", None, False, "sha70")
        result = state.queue_set(queue_file, ["!76", "!78"])
        assert [i["ref"] for i in result["items"]] == ["!70", "!74", "!76", "!78"]
        assert result["verifying"] == ["!70", "!74"]
        assert item_of(result, "!70")["merge_sha"] == "sha70"
        assert item_of(result, "!74")["merge_sha"] == "sha74"
        assert result["next"] == "!76"

    def test_verifying_inside_new_list_keeps_list_position_without_duplicate(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72"])
        state.queue_update(queue_file, "!72", "verifying", None, False, "sha72")
        result = state.queue_set(queue_file, ["!76", "!72"])
        assert [i["ref"] for i in result["items"]] == ["!76", "!72"]
        assert item_of(result, "!72")["status"] == "verifying"

    def test_other_items_outside_new_list_are_dropped_as_before(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["!70", "!72", "!74", "!76", "!78"])
        state.queue_update(queue_file, "!72", "merging", None, False, "s")
        state.queue_update(queue_file, "!74", "stopped", None, False, None)
        state.queue_update(queue_file, "!76", "done", None, False, None)
        result = state.queue_set(queue_file, ["!80"])
        assert [i["ref"] for i in result["items"]] == ["!80"]

    def test_queue_append_leaves_verifying_untouched(self, tmp_path):
        queue_file = tmp_path / "mr-queue.json"
        state.queue_set(queue_file, ["#14451"])
        state.queue_update(queue_file, "#14451", "verifying", None, False, "sha")
        result = state.queue_append(queue_file, "gh", gh("pr-list-open"))
        assert item_of(result, "#14451")["status"] == "verifying"
        assert item_of(result, "#14451")["merge_sha"] == "sha"


@pytest.mark.unit
class TestTargetCiFailedJobs:
    def test_glab_manual_pipeline_failed_jobs_are_named(self):
        result = state.target_ci_glab([{"id": 1, "status": "manual"}], glab("pipeline-jobs-failed"))
        assert result["status"] == "failed"
        assert result["failed_jobs"] == ["clean-code"]
        assert "clean-code" in result["reason"]

    def test_glab_failed_pipeline_with_jobs_names_red_jobs(self):
        result = state.target_ci_glab([{"id": 7, "status": "failed"}], glab("pipeline-jobs-failed"))
        assert result["status"] == "failed"
        assert result["failed_jobs"] == ["clean-code"]
        assert "clean-code" in result["reason"]
        assert "pipeline 7" in result["reason"]

    def test_glab_failed_pipeline_lists_every_red_job_in_order(self):
        jobs = copy.deepcopy(glab("pipeline-jobs-failed"))
        for job in jobs:
            if job["name"] == "plugin-consistency":
                job["status"] = "failed"
        result = state.target_ci_glab([{"id": 7, "status": "failed"}], jobs)
        assert result["failed_jobs"] == ["plugin-consistency", "clean-code"]
        assert "plugin-consistency" in result["reason"]
        assert "clean-code" in result["reason"]

    def test_glab_failed_pipeline_ignores_red_job_with_allow_failure(self):
        jobs = copy.deepcopy(glab("pipeline-jobs-failed"))
        for job in jobs:
            if job["name"] == "clean-code":
                job["allow_failure"] = True
        result = state.target_ci_glab([{"id": 7, "status": "failed"}], jobs)
        assert result["status"] == "failed"
        assert result["failed_jobs"] == []

    def test_glab_failed_pipeline_without_jobs_has_empty_failed_jobs_and_old_reason(self):
        result = state.target_ci_glab([{"id": 7, "status": "failed"}], None)
        assert result["status"] == "failed"
        assert result["failed_jobs"] == []
        assert result["reason"] == "pipeline 7 failed"

    @pytest.mark.parametrize("pipeline_status, expected", [("success", "success"), ("running", "wait")])
    def test_glab_non_failed_results_carry_no_failed_jobs(self, pipeline_status, expected):
        result = state.target_ci_glab([{"id": 7, "status": pipeline_status}], glab("pipeline-jobs-failed"))
        assert result["status"] == expected
        assert "failed_jobs" not in result

    def test_glab_no_pipeline_carries_no_failed_jobs(self):
        assert "failed_jobs" not in state.target_ci_glab([], None)

    def test_gh_failed_runs_are_named(self):
        result = state.target_ci_gh(gh("run-list-failure"))
        assert result["status"] == "failed"
        assert result["failed_jobs"] == ["Dependabot PR Triage (skills-driven)", "Unit and Integration Tests", "Code Scanning"]
        assert "Code Scanning" in result["reason"]

    def test_gh_success_carries_no_failed_jobs(self):
        assert "failed_jobs" not in state.target_ci_gh(gh("run-list-commit-success"))

    def test_gh_waiting_carries_no_failed_jobs(self):
        runs = copy.deepcopy(gh("run-list-commit-success"))
        runs[0]["status"] = "in_progress"
        runs[0]["conclusion"] = ""
        assert "failed_jobs" not in state.target_ci_gh(runs)

    def test_cli_glab_failed_prints_failed_jobs(self, tmp_path):
        pipelines = write_json(tmp_path, "p.json", [{"id": 7, "status": "failed"}])
        jobs = write_json(tmp_path, "j.json", glab("pipeline-jobs-failed"))
        out = cli_json("target-ci", "--provider", "glab", "--pipelines-file", pipelines, "--jobs-file", jobs)
        assert out["status"] == "failed"
        assert out["failed_jobs"] == ["clean-code"]

    def test_cli_gh_failed_prints_failed_jobs(self, tmp_path):
        runs = write_json(tmp_path, "r.json", gh("run-list-failure"))
        out = cli_json("target-ci", "--provider", "gh", "--runs-file", runs)
        assert out["failed_jobs"] == ["Dependabot PR Triage (skills-driven)", "Unit and Integration Tests", "Code Scanning"]


@pytest.mark.unit
class TestTargetCiIntegrationBranch:
    """target-ci na realnym pipeline'ie commita na sprint/* (fixture z 2026-09-29)."""

    def test_green_sprint_pipeline_is_success_with_jobs(self):
        result = state.target_ci_glab(glab("pipelines-by-sha-sprint-success"), glab("pipeline-jobs-sprint-success"))
        assert result["status"] == "success"
        assert "2894038602" in result["reason"]
        assert "failed_jobs" not in result

    def test_green_sprint_pipeline_is_success_without_jobs(self):
        assert state.target_ci_glab(glab("pipelines-by-sha-sprint-success"), None)["status"] == "success"

    def test_cli_green_sprint_pipeline_is_success(self, tmp_path):
        pipelines = write_json(tmp_path, "p.json", glab("pipelines-by-sha-sprint-success"))
        jobs = write_json(tmp_path, "j.json", glab("pipeline-jobs-sprint-success"))
        out = cli_json("target-ci", "--provider", "glab", "--pipelines-file", pipelines, "--jobs-file", jobs)
        assert out["status"] == "success"

    def test_sprint_pipeline_turned_red_names_failed_jobs_and_pipeline(self):
        pipelines = copy.deepcopy(glab("pipelines-by-sha-sprint-success"))
        pipelines[0]["status"] = "failed"
        result = state.target_ci_glab(pipelines, glab("pipeline-jobs-failed"))
        assert result["status"] == "failed"
        assert result["failed_jobs"] == ["clean-code"]
        assert "2894038602" in result["reason"]

    def test_sprint_pipeline_still_running_waits(self):
        pipelines = copy.deepcopy(glab("pipelines-by-sha-sprint-success"))
        pipelines[0]["status"] = "running"
        result = state.target_ci_glab(pipelines, glab("pipeline-jobs-sprint-success"))
        assert result["status"] == "wait"
        assert "failed_jobs" not in result


def glab_pipelines_with(*statuses):
    """Lista pipeline'ow z fixture sprintu: kopia realnego wpisu, rozne `id` i `status` (jedyne zmienione pola)."""
    template = glab("pipelines-by-sha-sprint-success")[0]
    pipelines = []
    for offset, status in enumerate(statuses):
        entry = copy.deepcopy(template)
        entry["id"] = template["id"] + offset
        entry["status"] = status
        pipelines.append(entry)
    return pipelines


def gh_runs_with(*statuses):
    """Lista runow z fixture: kopia realnego wpisu, rozne `databaseId`; `status` to (status, conclusion)."""
    template = gh("run-list-commit-success")[0]
    runs = []
    for offset, (status, conclusion) in enumerate(statuses):
        entry = copy.deepcopy(template)
        entry["databaseId"] = template["databaseId"] + offset
        entry["status"] = status
        entry["conclusion"] = conclusion
        runs.append(entry)
    return runs


@pytest.mark.unit
class TestTargetCancel:
    """target-cancel (MON-233): ktore pipeline'y/runy brancha integracyjnego tick anuluje po merge.

    Anulowanie jest nieodwracalne, wiec do `cancel` trafia tylko status znany jako trwajacy;
    kazdy inny, w tym nieznany literal, ladzi w `skipped` (external-tool-output.md pkt 3).
    """

    @pytest.mark.parametrize("status", ["running", "created", "waiting_for_resource", "preparing", "pending", "scheduled"])
    def test_glab_active_pipeline_is_canceled(self, status):
        pipelines = glab_pipelines_with(status)
        result = state.target_cancel_glab(pipelines)
        assert result["cancel"] == [pipelines[0]["id"]]
        assert result["skipped"] == []
        assert "do anulowania: 1, pominietych" in result["reason"]
        assert "nic do anulowania" not in result["reason"]

    @pytest.mark.parametrize("status", ["success", "failed", "canceled", "skipped", "manual"])
    def test_glab_finished_pipeline_is_skipped_with_its_status(self, status):
        pipelines = glab_pipelines_with(status)
        result = state.target_cancel_glab(pipelines)
        assert result["cancel"] == []
        assert result["skipped"] == [{"id": pipelines[0]["id"], "status": status}]

    def test_glab_unknown_status_is_skipped_never_canceled(self):
        pipelines = glab_pipelines_with("status-z-przyszlosci")
        result = state.target_cancel_glab(pipelines)
        assert result["cancel"] == []
        assert result["skipped"] == [{"id": pipelines[0]["id"], "status": "status-z-przyszlosci"}]

    def test_glab_real_green_sprint_fixture_cancels_nothing(self):
        pipelines = glab("pipelines-by-sha-sprint-success")
        result = state.target_cancel_glab(pipelines)
        assert result["cancel"] == []
        assert result["skipped"] == [{"id": 2894038602, "status": "success"}]

    def test_glab_mixed_list_splits_in_input_order(self):
        pipelines = glab_pipelines_with("success", "running", "failed", "pending", "weird")
        ids = [p["id"] for p in pipelines]
        result = state.target_cancel_glab(pipelines)
        assert result["cancel"] == [ids[1], ids[3]]
        assert result["skipped"] == [
            {"id": ids[0], "status": "success"},
            {"id": ids[2], "status": "failed"},
            {"id": ids[4], "status": "weird"},
        ]
        assert "do anulowania: 2, pominietych" in result["reason"]
        assert result["reason"].endswith(": 3")
        assert "nic do anulowania" not in result["reason"]

    def test_glab_empty_list_is_not_an_error(self):
        result = state.target_cancel_glab([])
        assert result["cancel"] == []
        assert result["skipped"] == []
        assert "brak pipeline'ow" in result["reason"]
        assert "nic do anulowania" in result["reason"]
        assert "do anulowania:" not in result["reason"]

    @pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending", "requested"])
    def test_gh_active_run_is_canceled(self, status):
        runs = gh_runs_with((status, ""))
        result = state.target_cancel_gh(runs)
        assert result["cancel"] == [runs[0]["databaseId"]]
        assert result["skipped"] == []
        assert "do anulowania: 1, pominietych" in result["reason"]
        assert "nic do anulowania" not in result["reason"]

    @pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled"])
    def test_gh_completed_run_is_skipped_with_its_status(self, conclusion):
        runs = gh_runs_with(("completed", conclusion))
        result = state.target_cancel_gh(runs)
        assert result["cancel"] == []
        assert result["skipped"] == [{"id": runs[0]["databaseId"], "status": "completed"}]

    def test_gh_unknown_status_is_skipped_never_canceled(self):
        runs = gh_runs_with(("status-z-przyszlosci", ""))
        result = state.target_cancel_gh(runs)
        assert result["cancel"] == []
        assert result["skipped"] == [{"id": runs[0]["databaseId"], "status": "status-z-przyszlosci"}]

    def test_gh_real_success_fixture_cancels_nothing(self):
        runs = gh("run-list-commit-success")
        result = state.target_cancel_gh(runs)
        assert result["cancel"] == []
        assert [s["id"] for s in result["skipped"]] == [r["databaseId"] for r in runs]
        assert {s["status"] for s in result["skipped"]} == {"completed"}

    def test_gh_mixed_list_splits_in_input_order(self):
        runs = gh_runs_with(("completed", "success"), ("in_progress", ""), ("queued", ""), ("completed", "failure"), ("weird", ""))
        ids = [r["databaseId"] for r in runs]
        result = state.target_cancel_gh(runs)
        assert result["cancel"] == [ids[1], ids[2]]
        assert result["skipped"] == [
            {"id": ids[0], "status": "completed"},
            {"id": ids[3], "status": "completed"},
            {"id": ids[4], "status": "weird"},
        ]
        assert "do anulowania: 2, pominietych" in result["reason"]
        assert result["reason"].endswith(": 3")
        assert "nic do anulowania" not in result["reason"]

    def test_gh_empty_list_is_not_an_error(self):
        result = state.target_cancel_gh([])
        assert result["cancel"] == []
        assert result["skipped"] == []
        assert "brak runow" in result["reason"]
        assert "nic do anulowania" in result["reason"]
        assert "do anulowania:" not in result["reason"]

    def test_cli_glab_prints_cancel_and_skipped(self, tmp_path):
        pipelines = glab_pipelines_with("running", "success")
        path = write_json(tmp_path, "p.json", pipelines)
        out = cli_json("target-cancel", "--provider", "glab", "--pipelines-file", path)
        assert out["cancel"] == [pipelines[0]["id"]]
        assert out["skipped"] == [{"id": pipelines[1]["id"], "status": "success"}]

    def test_cli_gh_prints_cancel_and_skipped(self, tmp_path):
        runs = gh_runs_with(("in_progress", ""), ("completed", "success"))
        path = write_json(tmp_path, "r.json", runs)
        out = cli_json("target-cancel", "--provider", "gh", "--runs-file", path)
        assert out["cancel"] == [runs[0]["databaseId"]]
        assert out["skipped"] == [{"id": runs[1]["databaseId"], "status": "completed"}]

    @pytest.mark.parametrize(
        "args",
        [
            ("--provider", "glab", "--pipelines-file"),
            ("--provider", "gh", "--runs-file"),
        ],
    )
    def test_cli_empty_list_exits_0_with_nothing_to_cancel(self, tmp_path, args):
        path = write_json(tmp_path, "empty.json", [])
        out = cli_json("target-cancel", *args, path)
        assert out["cancel"] == []
        assert out["skipped"] == []

    @pytest.mark.parametrize(
        "args",
        [
            ("--provider", "glab", "--pipelines-file"),
            ("--provider", "gh", "--runs-file"),
        ],
    )
    def test_cli_missing_file_exits_2(self, tmp_path, args):
        result = run_cli("target-cancel", *args, str(tmp_path / "nie-ma.json"))
        assert result.returncode == 2
        assert "nie istnieje" in result.stderr

    @pytest.mark.parametrize(
        "args",
        [
            ("--provider", "glab", "--pipelines-file"),
            ("--provider", "gh", "--runs-file"),
        ],
    )
    def test_cli_invalid_json_exits_2(self, tmp_path, args):
        path = tmp_path / "bad.json"
        path.write_text("{nie json")
        result = run_cli("target-cancel", *args, str(path))
        assert result.returncode == 2
        assert "niepoprawny JSON" in result.stderr

    @pytest.mark.parametrize(
        "args",
        [
            ("--provider", "glab", "--pipelines-file"),
            ("--provider", "gh", "--runs-file"),
        ],
    )
    def test_cli_non_object_element_exits_2(self, tmp_path, args):
        path = write_json(tmp_path, "bad.json", [42])
        result = run_cli("target-cancel", *args, path)
        assert result.returncode == 2
        assert result.stdout == ""

    @pytest.mark.parametrize(
        "args",
        [
            ("--provider", "glab"),
            ("--provider", "gh"),
            ("--pipelines-file", "x.json"),
            (),
        ],
    )
    def test_cli_missing_required_arguments_exits_64(self, args):
        assert run_cli("target-cancel", *args).returncode == 64


# provider, flaga pliku, klucz id, etykieta w komunikacie, realny status trwajacy, gotowy poprawny element
_CANCEL_PROVIDERS = [
    pytest.param("glab", "--pipelines-file", "id", "pipelines-file", "running", id="glab"),
    pytest.param("gh", "--runs-file", "databaseId", "runs-file", "in_progress", id="gh"),
]


@pytest.mark.unit
class TestTargetCancelInputValidation:
    """Id z `cancel` idzie do nieodwracalnego `glab ci cancel pipeline` / `gh run cancel`: zly element = kod 2, zero stdout."""

    @staticmethod
    def _element(provider, **fields):
        template = glab_pipelines_with("running")[0] if provider == "glab" else gh_runs_with(("in_progress", ""))[0]
        element = copy.deepcopy(template)
        element.update(fields)
        return element

    @staticmethod
    def _run(tmp_path, provider, flag, elements):
        path = write_json(tmp_path, "in.json", elements)
        return run_cli("target-cancel", "--provider", provider, flag, path)

    @pytest.mark.parametrize("provider, flag, id_key, label, active", _CANCEL_PROVIDERS)
    def test_active_element_without_id_exits_2_with_its_index(self, tmp_path, provider, flag, id_key, label, active):
        good = self._element(provider)
        bad = self._element(provider, status=active)
        del bad[id_key]
        result = self._run(tmp_path, provider, flag, [good, bad])
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr.startswith("mr_queue_state: ")
        assert f"{label}[1]: '{id_key}' musi byc liczba calkowita dla statusu trwajacego '{active}'" in result.stderr

    @pytest.mark.parametrize("bad_id", ["abc; rm x", "123", None, 1.5, [1]])
    @pytest.mark.parametrize("provider, flag, id_key, label, active", _CANCEL_PROVIDERS)
    def test_active_element_with_non_integer_id_exits_2(self, tmp_path, provider, flag, id_key, label, active, bad_id):
        good = self._element(provider)
        bad = self._element(provider, status=active, **{id_key: bad_id})
        result = self._run(tmp_path, provider, flag, [good, bad])
        assert result.returncode == 2
        assert result.stdout == ""
        assert f"{label}[1]: '{id_key}' musi byc liczba calkowita" in result.stderr

    @pytest.mark.parametrize("provider, flag, id_key, label, active", _CANCEL_PROVIDERS)
    def test_active_element_with_bool_id_exits_2(self, tmp_path, provider, flag, id_key, label, active):
        bad = self._element(provider, status=active, **{id_key: True})
        result = self._run(tmp_path, provider, flag, [self._element(provider), bad])
        assert result.returncode == 2
        assert result.stdout == ""
        assert f"{label}[1]: '{id_key}' musi byc liczba calkowita" in result.stderr

    @pytest.mark.parametrize("bad_status", [{}, 5, ["running"], True])
    @pytest.mark.parametrize("provider, flag, id_key, label, active", _CANCEL_PROVIDERS)
    def test_non_string_status_exits_2_with_its_index(self, tmp_path, provider, flag, id_key, label, active, bad_status):
        bad = self._element(provider, status=bad_status)
        result = self._run(tmp_path, provider, flag, [self._element(provider), bad])
        assert result.returncode == 2
        assert result.stdout == ""
        assert f"{label}[1]: 'status' musi byc stringiem albo null" in result.stderr

    @pytest.mark.parametrize("provider, flag, id_key, label, active", _CANCEL_PROVIDERS)
    def test_bad_element_prevents_any_output_even_when_earlier_ones_are_valid(self, tmp_path, provider, flag, id_key, label, active):
        valid_active = self._element(provider)
        bad = self._element(provider, status=active, **{id_key: "abc; rm x"})
        result = self._run(tmp_path, provider, flag, [valid_active, bad])
        assert result.returncode == 2
        assert str(valid_active[id_key]) not in result.stdout

    @pytest.mark.parametrize("provider, flag, id_key, label, active", _CANCEL_PROVIDERS)
    def test_inactive_element_with_non_numeric_id_is_skipped_with_exit_0(self, tmp_path, provider, flag, id_key, label, active):
        inactive = "success" if provider == "glab" else "completed"
        element = self._element(provider, status=inactive, **{id_key: "x"})
        out = cli_json("target-cancel", "--provider", provider, flag, write_json(tmp_path, "in.json", [element]))
        assert out["cancel"] == []
        assert out["skipped"] == [{"id": "x", "status": inactive}]

    @pytest.mark.parametrize("provider, flag, id_key, label, active", _CANCEL_PROVIDERS)
    def test_null_status_is_skipped_with_exit_0(self, tmp_path, provider, flag, id_key, label, active):
        element = self._element(provider, status=None)
        out = cli_json("target-cancel", "--provider", provider, flag, write_json(tmp_path, "in.json", [element]))
        assert out["cancel"] == []
        assert out["skipped"] == [{"id": element[id_key], "status": None}]

    @pytest.mark.parametrize("provider, flag, id_key, label, active", _CANCEL_PROVIDERS)
    def test_active_element_with_integer_id_still_passes_validation(self, tmp_path, provider, flag, id_key, label, active):
        element = self._element(provider, status=active)
        out = cli_json("target-cancel", "--provider", provider, flag, write_json(tmp_path, "in.json", [element]))
        assert out["cancel"] == [element[id_key]]
