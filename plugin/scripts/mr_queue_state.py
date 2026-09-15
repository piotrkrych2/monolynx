#!/usr/bin/env python3
"""Stan kolejki merge dla /monolynx:mr-queue.

Skill nie zgaduje stanu MR/PR z prozy - ten skrypt liczy go z JSON-a zwracanego
przez `glab` (GitLab) i `gh` (GitHub) i prowadzi lokalny plik kolejki.

Podkomendy:

    queue-propose --provider glab|gh --list-file L
        Zaproponuj kolejnosc z listy otwartych MR/PR: MR oparty o branch innego
        otwartego MR idzie zaraz po nim, reszta od najstarszego. Drafty i MR-y
        oparte o draft trafiaja do `skipped`.

    queue-set    --queue-file F REF [REF ...]
        Zapisz kolejnosc merge. REF: `!70` (glab), `#15` (gh) albo URL MR/PR.
        Refy juz obecne w kolejce zachowuja swoj stan (licznik poprawek,
        approve z czatu, SHA merge); refy spoza nowej listy wypadaja.

    queue-show   --queue-file F
        Wypisz kolejke i pierwszy element do obslugi (`next`).

    queue-update --queue-file F --ref REF [--status S] [--user-approved-sha SHA]
                 [--fix-round] [--merge-sha SHA]
        Zmien stan jednego elementu.

    classify     --provider glab --mr-file M --approvals-file A [--user-approved-sha SHA]
    classify     --provider gh --pr-file P [--user-approved-sha SHA]
        Werdykt dla jednego MR/PR, w kolejnosci: CI -> konflikt -> approve -> merge.

    target-ci    --provider glab --pipelines-file P [--jobs-file J]
    target-ci    --provider gh --runs-file R
        Stan CI na branchu docelowym dla commita merge.

Wejscia:
    --list-file       `glab mr list -F json -P 100` albo
                      `gh pr list --state open -L 100 --json number,createdAt,baseRefName,headRefName,isDraft,url`
    --mr-file        `glab mr view <iid> -F json`
    --approvals-file  `glab api projects/<repo>/merge_requests/<iid>/approvals`
    --pr-file         `gh pr view <n> --json number,state,isDraft,mergeable,mergeStateStatus,
                       reviewDecision,statusCheckRollup,headRefName,headRefOid,baseRefName,url`
    --pipelines-file  `glab api projects/<repo>/pipelines?sha=<sha>&ref=<branch>`
    --jobs-file       `glab api projects/<repo>/pipelines/<id>/jobs?per_page=100`
    --runs-file       `gh run list --commit <sha> --json databaseId,status,conclusion,name,headBranch,
                       headSha,event,workflowName`

Werdykty classify: merged, closed, wait, ci_failed, conflict, needs_approval,
needs_human, ready. Stany target-ci: success, failed, wait, needs_human.

Literaly stanow pochodza z fixture w tests/fixtures/glab/ i tests/fixtures/gh/;
wartosci dopisane ze schematu API sa w komentarzach oznaczone osobno. Literal
nieznany mapuje sie na `wait` z ostrzezeniem w `reason` - czekanie cofa nastepny
tick, a pomylkowe `ready` konczy sie merge, ktorego nie da sie cofnac
(.claude/rules/external-tool-output.md pkt 3).

Kody wyjscia:
    0  - wynik wypisany na stdout jako JSON.
    2  - blad wejscia: brak pliku, niepoprawny JSON, zly ksztalt, nieznany REF.
    64 - blad uzycia CLI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# --- GitLab: head_pipeline.status / pipelines[].status ---
# Zaobserwowane: "running" (mr-view-opened-pipeline-running), "success"
# (mr-view-merged), "manual" (pipelines-by-sha-main-manual), "failed" (filtr
# status=failed zwrocil pipeline, joby w pipeline-jobs-failed).
# Ze schematu API (bez fixture): created, waiting_for_resource, preparing,
# pending, scheduled, canceled, skipped.
_GLAB_CI_SUCCESS = {"success"}
_GLAB_CI_FAILED = {"failed"}
_GLAB_CI_RUNNING = {"running", "created", "waiting_for_resource", "preparing", "pending", "scheduled"}
_GLAB_CI_HUMAN = {"canceled", "skipped", "manual"}

# --- GitLab: detailed_merge_status ---
# Zaobserwowane: "mergeable" (MR otwarty), "not_open" (MR zmergowany).
# Ze schematu API: conflict, need_rebase, checking, unchecked, approvals_syncing,
# preparing, ci_must_pass, ci_still_running, not_approved, draft_status,
# discussions_not_resolved i inne blokady (traktowane jako needs_human).
_GLAB_MERGE_CONFLICT = {"conflict", "need_rebase"}
_GLAB_MERGE_WAIT = {"checking", "unchecked", "approvals_syncing", "preparing", "ci_must_pass", "ci_still_running"}

# --- GitLab: status jobow (target-ci przy pipeline "manual") ---
# Zaobserwowane: success, manual, failed, skipped (pipeline-jobs-*).
_GLAB_JOB_OK = {"success", "manual", "skipped"}

# --- GitHub: statusCheckRollup ---
# CheckRun: zaobserwowane status=COMPLETED z conclusion SUCCESS, SKIPPED, FAILURE
# (pr-view-*). Ze schematu: status QUEUED/IN_PROGRESS/WAITING/PENDING/REQUESTED,
# conclusion NEUTRAL, TIMED_OUT, CANCELLED, ACTION_REQUIRED, STARTUP_FAILURE, STALE.
# StatusContext: zaobserwowane state SUCCESS, PENDING, FAILURE
# (pr-view-statuscontext-*). Ze schematu: ERROR, EXPECTED.
_GH_CHECK_OK = {"SUCCESS", "SKIPPED", "NEUTRAL"}
_GH_CHECK_FAILED = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE", "ERROR"}
_GH_CONTEXT_RUNNING = {"PENDING", "EXPECTED"}

# --- GitHub: mergeStateStatus ---
# Zaobserwowane: BLOCKED, DIRTY, UNKNOWN. Ze schematu: CLEAN, HAS_HOOKS,
# UNSTABLE, BEHIND, DRAFT.
_GH_MERGE_READY = {"CLEAN", "HAS_HOOKS", "UNSTABLE"}

# --- GitHub: gh run list ---
# Zaobserwowane: status=completed z conclusion success, failure
# (run-list-*). Ze schematu: status queued/in_progress/waiting/pending/requested,
# conclusion skipped, neutral, cancelled, timed_out, startup_failure, action_required, stale.
_GH_RUN_OK = {"success", "skipped", "neutral"}
_GH_RUN_FAILED = {"failure", "cancelled", "timed_out", "startup_failure", "action_required", "stale"}

_QUEUE_STATUSES = {"pending", "merging", "done", "closed", "stopped"}

_REF_RE = re.compile(r"^(?P<sigil>[!#])(?P<number>[1-9][0-9]*)$")
_GITLAB_URL_RE = re.compile(r"^https?://[^/]+/(?P<repo>.+?)/-/merge_requests/(?P<number>[1-9][0-9]*)/?$")
_GITHUB_URL_RE = re.compile(r"^https?://github\.com/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>[1-9][0-9]*)/?$")


class _UsageError(Exception):
    """Blad parsowania argumentow CLI (kod wyjscia 64)."""


class _InputError(Exception):
    """Blad tresci wejscia (kod wyjscia 2)."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        # argparse wola sys.exit(2), co zlewaloby sie z kodem 2 dla bledow wejscia.
        raise _UsageError(message)


def _verdict(verdict: str, reason: str, **fields: Any) -> dict[str, Any]:
    return {"verdict": verdict, "reason": reason, **fields}


def parse_ref(raw: str) -> dict[str, Any]:
    """Zamien `!70`, `#15` albo URL MR/PR na {ref, provider, number, repo}."""
    text = raw.strip()
    match = _REF_RE.match(text)
    if match:
        provider = "glab" if match.group("sigil") == "!" else "gh"
        return {"ref": text, "provider": provider, "number": int(match.group("number")), "repo": None}
    match = _GITHUB_URL_RE.match(text)
    if match:
        number = int(match.group("number"))
        return {"ref": f"#{number}", "provider": "gh", "number": number, "repo": match.group("repo")}
    match = _GITLAB_URL_RE.match(text)
    if match:
        number = int(match.group("number"))
        return {"ref": f"!{number}", "provider": "glab", "number": number, "repo": match.group("repo")}
    raise _InputError(f"nieznany REF '{raw}': oczekiwano !<iid> (GitLab), #<numer> (GitHub) albo URL MR/PR")


# --- classify: GitLab ---


def _glab_ci(pipeline: Any) -> tuple[str, str]:
    if not isinstance(pipeline, dict) or not pipeline.get("status"):
        return "none", "MR nie ma pipeline'u"
    status = pipeline["status"]
    if status in _GLAB_CI_SUCCESS:
        return "success", f"pipeline {pipeline.get('id')} success"
    if status in _GLAB_CI_FAILED:
        return "failed", f"pipeline {pipeline.get('id')} failed"
    if status in _GLAB_CI_RUNNING:
        return "running", f"pipeline {pipeline.get('id')} {status}"
    if status in _GLAB_CI_HUMAN:
        return "human", f"pipeline {pipeline.get('id')} {status}"
    return "running", f"pipeline {pipeline.get('id')}: nieznany status '{status}', traktuje jak trwajacy"


def classify_glab(mr: dict[str, Any], approvals: dict[str, Any], user_approved_sha: str | None = None) -> dict[str, Any]:
    sha = mr.get("sha")
    fields = {
        "provider": "glab",
        "number": mr.get("iid"),
        "url": mr.get("web_url"),
        "source_branch": mr.get("source_branch"),
        "target_branch": mr.get("target_branch"),
        "sha": sha,
    }
    state = mr.get("state")
    if state == "merged":
        # Fast-forward nie tworzy commita merge: oba pola sa null, a HEAD brancha docelowego to sha MR.
        merge_sha = mr.get("merge_commit_sha") or mr.get("squash_commit_sha") or sha
        return _verdict("merged", "MR zmergowany", merge_sha=merge_sha, **fields)
    if state in ("closed", "locked"):
        return _verdict("closed", f"MR w stanie '{state}'", **fields)
    if state != "opened":
        return _verdict("wait", f"nieznany stan MR '{state}', traktuje jak otwarty w toku", **fields)
    if mr.get("draft") or mr.get("work_in_progress"):
        return _verdict("needs_human", "MR jest draftem", **fields)

    ci, ci_reason = _glab_ci(mr.get("head_pipeline"))
    if ci == "none":
        return _verdict("needs_human", ci_reason, **fields)
    if ci == "failed":
        return _verdict("ci_failed", ci_reason, pipeline_id=mr["head_pipeline"].get("id"), **fields)
    if ci == "running":
        return _verdict("wait", ci_reason, **fields)
    if ci == "human":
        return _verdict("needs_human", ci_reason, **fields)

    merge_status = mr.get("detailed_merge_status")
    if mr.get("has_conflicts") or merge_status in _GLAB_MERGE_CONFLICT:
        return _verdict("conflict", f"konflikt z {mr.get('target_branch')} (detailed_merge_status={merge_status})", **fields)
    if merge_status in _GLAB_MERGE_WAIT:
        return _verdict("wait", f"GitLab liczy mergeability ({merge_status})", **fields)

    # approved=true przy approvals_required=0 nie oznacza, ze ktokolwiek zatwierdzil
    # (fixture mr-approvals-none-required) - liczy sie tylko approved_by albo approve z czatu.
    approved_by = approvals.get("approved_by") or []
    approved_in_mr = bool(approved_by) and not approvals.get("approvals_left")
    approved_in_chat = bool(sha) and user_approved_sha == sha
    if not (approved_in_mr or approved_in_chat):
        return _verdict("needs_approval", "brak approve w MR i w czacie dla biezacego SHA", **fields)

    if merge_status == "mergeable":
        source = "MR" if approved_in_mr else "czat"
        return _verdict("ready", f"CI zielone, bez konfliktu, approve ({source})", **fields)
    return _verdict("needs_human", f"GitLab blokuje merge: detailed_merge_status={merge_status}", **fields)


# --- classify: GitHub ---


def _gh_ci(rollup: Any) -> tuple[str, str]:
    if not rollup:
        return "none", "PR nie ma checkow"
    failed: list[str] = []
    running: list[str] = []
    unknown: list[str] = []
    for check in rollup:
        name = check.get("name") or check.get("context") or "?"
        if check.get("__typename") == "StatusContext":
            state = check.get("state")
            if state == "SUCCESS":
                continue
            if state in _GH_CHECK_FAILED:
                failed.append(name)
            elif state in _GH_CONTEXT_RUNNING:
                running.append(name)
            else:
                unknown.append(f"{name}={state}")
            continue
        if check.get("status") != "COMPLETED":
            running.append(name)
            continue
        conclusion = check.get("conclusion")
        if conclusion in _GH_CHECK_OK:
            continue
        if conclusion in _GH_CHECK_FAILED:
            failed.append(name)
        else:
            unknown.append(f"{name}={conclusion}")
    if failed:
        return "failed", "czerwone checki: " + ", ".join(failed)
    if running or unknown:
        parts = []
        if running:
            parts.append("w toku: " + ", ".join(running))
        if unknown:
            parts.append("nieznany stan, traktuje jak trwajacy: " + ", ".join(unknown))
        return "running", "; ".join(parts)
    return "success", "wszystkie checki zielone"


def classify_gh(pr: dict[str, Any], user_approved_sha: str | None = None) -> dict[str, Any]:
    sha = pr.get("headRefOid")
    fields = {
        "provider": "gh",
        "number": pr.get("number"),
        "url": pr.get("url"),
        "source_branch": pr.get("headRefName"),
        "target_branch": pr.get("baseRefName"),
        "sha": sha,
    }
    state = pr.get("state")
    if state == "MERGED":
        return _verdict("merged", "PR zmergowany", **fields)
    if state == "CLOSED":
        return _verdict("closed", "PR zamkniety", **fields)
    if state != "OPEN":
        return _verdict("wait", f"nieznany stan PR '{state}', traktuje jak otwarty w toku", **fields)
    if pr.get("isDraft"):
        return _verdict("needs_human", "PR jest draftem", **fields)

    ci, ci_reason = _gh_ci(pr.get("statusCheckRollup"))
    if ci == "none":
        return _verdict("needs_human", ci_reason, **fields)
    if ci == "failed":
        return _verdict("ci_failed", ci_reason, **fields)
    if ci == "running":
        return _verdict("wait", ci_reason, **fields)

    mergeable = pr.get("mergeable")
    merge_state = pr.get("mergeStateStatus")
    if mergeable == "CONFLICTING" or merge_state in ("DIRTY", "BEHIND"):
        return _verdict("conflict", f"konflikt z {pr.get('baseRefName')} (mergeable={mergeable}, mergeStateStatus={merge_state})", **fields)
    if mergeable != "MERGEABLE":
        return _verdict("wait", f"GitHub liczy mergeability (mergeable={mergeable})", **fields)

    decision = pr.get("reviewDecision")
    if decision == "CHANGES_REQUESTED":
        return _verdict("needs_human", "review prosi o zmiany (CHANGES_REQUESTED)", **fields)
    approved_in_pr = decision == "APPROVED"
    approved_in_chat = bool(sha) and user_approved_sha == sha
    if not (approved_in_pr or approved_in_chat):
        return _verdict("needs_approval", "brak approve w PR i w czacie dla biezacego SHA", **fields)

    if merge_state in _GH_MERGE_READY:
        source = "PR" if approved_in_pr else "czat"
        return _verdict("ready", f"checki zielone, bez konfliktu, approve ({source})", **fields)
    if merge_state == "UNKNOWN":
        return _verdict("wait", "GitHub liczy mergeStateStatus", **fields)
    return _verdict("needs_human", f"GitHub blokuje merge: mergeStateStatus={merge_state} (np. regula ochrony brancha)", **fields)


# --- target-ci ---


def target_ci_glab(pipelines: list[Any], jobs: list[Any] | None) -> dict[str, str]:
    if not pipelines:
        return {"status": "needs_human", "reason": "brak pipeline'u dla commita merge"}
    pipeline = max(pipelines, key=lambda p: p.get("id", 0))
    status = pipeline.get("status")
    label = f"pipeline {pipeline.get('id')}"
    if status in _GLAB_CI_SUCCESS:
        return {"status": "success", "reason": f"{label} success"}
    if status in _GLAB_CI_FAILED:
        return {"status": "failed", "reason": f"{label} failed"}
    if status in _GLAB_CI_RUNNING:
        return {"status": "wait", "reason": f"{label} {status}"}
    if status == "manual":
        # Pipeline main ma reczny deploy-staging (fixture pipeline-jobs-main-manual):
        # status "manual" jest zielony, gdy zaden job nie padl i nic juz nie biegnie.
        if jobs is None:
            return {"status": "wait", "reason": f"{label} manual - potrzebna lista jobow (--jobs-file)"}
        failed = [j.get("name", "?") for j in jobs if j.get("status") == "failed" and not j.get("allow_failure")]
        if failed:
            return {"status": "failed", "reason": f"{label}: czerwone joby " + ", ".join(failed)}
        pending = [f"{j.get('name', '?')}={j.get('status')}" for j in jobs if j.get("status") not in _GLAB_JOB_OK | {"failed"}]
        if pending:
            return {"status": "wait", "reason": f"{label}: joby w toku " + ", ".join(pending)}
        return {"status": "success", "reason": f"{label} manual - wszystkie joby automatyczne zielone"}
    if status in _GLAB_CI_HUMAN:
        return {"status": "needs_human", "reason": f"{label} {status}"}
    return {"status": "wait", "reason": f"{label}: nieznany status '{status}', traktuje jak trwajacy"}


def target_ci_gh(runs: list[Any]) -> dict[str, str]:
    if not runs:
        return {"status": "needs_human", "reason": "brak runow dla commita merge"}
    failed: list[str] = []
    waiting: list[str] = []
    for run in runs:
        name = run.get("workflowName") or run.get("name") or "?"
        if run.get("status") != "completed":
            waiting.append(f"{name}={run.get('status')}")
        elif run.get("conclusion") in _GH_RUN_FAILED:
            failed.append(name)
        elif run.get("conclusion") not in _GH_RUN_OK:
            waiting.append(f"{name}: nieznany conclusion '{run.get('conclusion')}'")
    if failed:
        return {"status": "failed", "reason": "czerwone runy: " + ", ".join(failed)}
    if waiting:
        return {"status": "wait", "reason": "w toku: " + ", ".join(waiting)}
    return {"status": "success", "reason": "wszystkie runy zielone"}


# --- kolejka ---


def _normalize_list_entry(provider: str, entry: dict[str, Any], index: int) -> dict[str, Any]:
    keys = ("iid", "created_at", "source_branch", "target_branch", "draft") if provider == "glab" else ("number", "createdAt", "headRefName", "baseRefName", "isDraft")
    for key in keys:
        if key not in entry:
            raise _InputError(f"list-file[{index}]: brak pola '{key}'")
    number, created, head, base, draft = (entry[key] for key in keys)
    if not isinstance(number, int) or not isinstance(head, str) or not isinstance(base, str):
        raise _InputError(f"list-file[{index}]: zly typ pola numeru albo brancha")
    sigil = "!" if provider == "glab" else "#"
    return {"ref": f"{sigil}{number}", "number": number, "created": str(created), "head": head, "base": base, "draft": bool(draft)}


def queue_propose(provider: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    items = [_normalize_list_entry(provider, entry, index) for index, entry in enumerate(entries)]
    by_head = {item["head"]: item for item in items}
    children: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for item in items:
        parent = by_head.get(item["base"])
        if parent is not None and parent is not item:
            children.setdefault(parent["ref"], []).append(item)
        else:
            roots.append(item)

    def age(item: dict[str, Any]) -> tuple[str, int]:
        return (item["created"], item["number"])

    order: list[str] = []
    skipped: list[dict[str, str]] = []
    visited: set[str] = set()

    # Stack merguje sie od dolu: rodzic, potem jego dzieci. Dziecko draftu czeka,
    # bo jego baza nie wejdzie do kolejki.
    def walk(item: dict[str, Any], blocked_by: str | None) -> None:
        if item["ref"] in visited:
            return
        visited.add(item["ref"])
        if blocked_by is not None:
            skipped.append({"ref": item["ref"], "reason": f"oparty o {blocked_by}, ktory jest poza kolejka"})
        elif item["draft"]:
            skipped.append({"ref": item["ref"], "reason": "draft"})
        else:
            order.append(item["ref"])
        child_block = blocked_by or (item["ref"] if item["draft"] else None)
        for child in sorted(children.get(item["ref"], []), key=age):
            walk(child, child_block)

    for root in sorted(roots, key=age):
        walk(root, None)
    return {"provider": provider, "order": order, "skipped": skipped}


def _read_queue(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"items": []}
    queue = _load_json(str(path), "queue-file")
    if not isinstance(queue, dict) or not isinstance(queue.get("items"), list):
        raise _InputError("queue-file: oczekiwano obiektu z lista 'items'")
    for index, item in enumerate(queue["items"]):
        if not isinstance(item, dict):
            raise _InputError(f"queue-file items[{index}]: element nie jest obiektem")
        for key in ("ref", "provider", "number", "status"):
            if key not in item:
                raise _InputError(f"queue-file items[{index}]: brak pola '{key}'")
        if item["status"] not in _QUEUE_STATUSES:
            raise _InputError(f"queue-file items[{index}]: nieznany status '{item['status']}'")
    return queue


def _write_queue(path: Path, queue: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n")


def _with_next(queue: dict[str, Any]) -> dict[str, Any]:
    # "merging" idzie przed "pending": po merge kolejka czeka na zielone CI
    # brancha docelowego, zanim wezmie nastepny MR.
    merging = [item for item in queue["items"] if item["status"] == "merging"]
    pending = [item for item in queue["items"] if item["status"] == "pending"]
    active = merging + pending
    return {**queue, "next": active[0]["ref"] if active else None}


def queue_set(path: Path, raw_refs: list[str]) -> dict[str, Any]:
    parsed = [parse_ref(raw) for raw in raw_refs]
    seen: set[str] = set()
    for item in parsed:
        if item["ref"] in seen:
            raise _InputError(f"REF '{item['ref']}' wystepuje w kolejce dwa razy")
        seen.add(item["ref"])
    previous = {item["ref"]: item for item in _read_queue(path)["items"]}
    items = []
    for item in parsed:
        old = previous.get(item["ref"], {})
        items.append(
            {
                **item,
                "repo": item["repo"] or old.get("repo"),
                "status": old.get("status", "pending"),
                "fix_rounds": old.get("fix_rounds", 0),
                "user_approved_sha": old.get("user_approved_sha"),
                "merge_sha": old.get("merge_sha"),
            }
        )
    queue = {"items": items}
    _write_queue(path, queue)
    return _with_next(queue)


def queue_update(
    path: Path, ref: str, status: str | None, user_approved_sha: str | None, fix_round: bool, merge_sha: str | None
) -> dict[str, Any]:
    target = parse_ref(ref)["ref"]
    queue = _read_queue(path)
    matches = [item for item in queue["items"] if item["ref"] == target]
    if not matches:
        raise _InputError(f"REF '{target}' nie jest w kolejce")
    item = matches[0]
    if status is not None:
        if status not in _QUEUE_STATUSES:
            raise _InputError(f"nieznany status '{status}': dozwolone {sorted(_QUEUE_STATUSES)}")
        item["status"] = status
    if user_approved_sha is not None:
        item["user_approved_sha"] = user_approved_sha
    if fix_round:
        item["fix_rounds"] = item.get("fix_rounds", 0) + 1
    if merge_sha is not None:
        item["merge_sha"] = merge_sha
    _write_queue(path, queue)
    return _with_next(queue)


# --- CLI ---


def _load_json(path_str: str, label: str) -> Any:
    path = Path(path_str)
    if not path.is_file():
        raise _InputError(f"{label}: plik nie istnieje: {path_str}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise _InputError(f"{label}: niepoprawny JSON ({exc})") from exc


def _load_object(path_str: str, label: str) -> dict[str, Any]:
    data = _load_json(path_str, label)
    if not isinstance(data, dict):
        raise _InputError(f"{label}: oczekiwano obiektu JSON")
    return data


def _load_list_of_objects(path_str: str, label: str) -> list[dict[str, Any]]:
    data = _load_json(path_str, label)
    if not isinstance(data, list):
        raise _InputError(f"{label}: oczekiwano listy JSON")
    for index, element in enumerate(data):
        if not isinstance(element, dict):
            raise _InputError(f"{label}[{index}]: element nie jest obiektem")
    return data


def _validate_gh_pr(pr: dict[str, Any]) -> None:
    for key in ("state", "headRefOid"):
        if key not in pr:
            raise _InputError(f"pr-file: brak pola '{key}' (uzyj gh pr view --json z pelna lista pol)")
    rollup = pr.get("statusCheckRollup")
    if rollup is not None and not isinstance(rollup, list):
        raise _InputError("pr-file: statusCheckRollup nie jest lista")
    for index, check in enumerate(rollup or []):
        if not isinstance(check, dict):
            raise _InputError(f"pr-file statusCheckRollup[{index}]: element nie jest obiektem")


def _validate_glab_mr(mr: dict[str, Any]) -> None:
    for key in ("state", "sha", "iid"):
        if key not in mr:
            raise _InputError(f"mr-file: brak pola '{key}' (uzyj glab mr view <iid> -F json)")
    pipeline = mr.get("head_pipeline")
    if pipeline is not None and not isinstance(pipeline, dict):
        raise _InputError("mr-file: head_pipeline nie jest obiektem")


def _build_parser() -> _ArgumentParser:
    parser = _ArgumentParser(prog="mr_queue_state.py", description="Stan kolejki merge MR/PR.")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_ArgumentParser)

    p_propose = sub.add_parser("queue-propose")
    p_propose.add_argument("--provider", required=True, choices=["glab", "gh"])
    p_propose.add_argument("--list-file", required=True)

    p_set = sub.add_parser("queue-set")
    p_set.add_argument("--queue-file", required=True)
    p_set.add_argument("refs", nargs="+")

    p_show = sub.add_parser("queue-show")
    p_show.add_argument("--queue-file", required=True)

    p_update = sub.add_parser("queue-update")
    p_update.add_argument("--queue-file", required=True)
    p_update.add_argument("--ref", required=True)
    p_update.add_argument("--status")
    p_update.add_argument("--user-approved-sha")
    p_update.add_argument("--fix-round", action="store_true")
    p_update.add_argument("--merge-sha")

    p_classify = sub.add_parser("classify")
    p_classify.add_argument("--provider", required=True, choices=["glab", "gh"])
    p_classify.add_argument("--mr-file")
    p_classify.add_argument("--approvals-file")
    p_classify.add_argument("--pr-file")
    p_classify.add_argument("--user-approved-sha")

    p_target = sub.add_parser("target-ci")
    p_target.add_argument("--provider", required=True, choices=["glab", "gh"])
    p_target.add_argument("--pipelines-file")
    p_target.add_argument("--jobs-file")
    p_target.add_argument("--runs-file")
    return parser


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "queue-propose":
        return queue_propose(args.provider, _load_list_of_objects(args.list_file, "list-file"))
    if args.command == "queue-set":
        return queue_set(Path(args.queue_file), args.refs)
    if args.command == "queue-show":
        return _with_next(_read_queue(Path(args.queue_file)))
    if args.command == "queue-update":
        return queue_update(Path(args.queue_file), args.ref, args.status, args.user_approved_sha, args.fix_round, args.merge_sha)
    if args.command == "classify":
        if args.provider == "glab":
            if not args.mr_file or not args.approvals_file:
                raise _UsageError("classify --provider glab wymaga --mr-file i --approvals-file")
            mr = _load_object(args.mr_file, "mr-file")
            _validate_glab_mr(mr)
            approvals = _load_object(args.approvals_file, "approvals-file")
            if not isinstance(approvals.get("approved_by", []), list):
                raise _InputError("approvals-file: approved_by nie jest lista")
            return classify_glab(mr, approvals, args.user_approved_sha)
        if not args.pr_file:
            raise _UsageError("classify --provider gh wymaga --pr-file")
        pr = _load_object(args.pr_file, "pr-file")
        _validate_gh_pr(pr)
        return classify_gh(pr, args.user_approved_sha)
    if args.provider == "glab":
        if not args.pipelines_file:
            raise _UsageError("target-ci --provider glab wymaga --pipelines-file")
        pipelines = _load_list_of_objects(args.pipelines_file, "pipelines-file")
        jobs = _load_list_of_objects(args.jobs_file, "jobs-file") if args.jobs_file else None
        return target_ci_glab(pipelines, jobs)
    if not args.runs_file:
        raise _UsageError("target-ci --provider gh wymaga --runs-file")
    return target_ci_gh(_load_list_of_objects(args.runs_file, "runs-file"))


def main(argv: list[str]) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        result = _run(args)
    except _UsageError as exc:
        print(f"mr_queue_state: {exc}", file=sys.stderr)
        return 64
    except _InputError as exc:
        print(f"mr_queue_state: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
