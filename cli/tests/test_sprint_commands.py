"""Testy komend grupy `sprint` (MON-130): mapowanie flag na request API v2, board i burndown.

HTTP jest mockowany przez fixture `mock_transport`; punkt wstrzyknięcia to nazwa modułowa
`monolynx_cli.commands._common.MonolynxClient`.
"""

from __future__ import annotations

import csv
import functools
import io
import json

import httpx
import pytest
import typer.main
import yaml
from monolynx_cli import client as client_mod
from monolynx_cli.commands import _common
from monolynx_cli.main import app

pytestmark = pytest.mark.unit

SLUG = "testproj"
P = f"/api/v2/projects/{SLUG}"
_PROBLEM_CT = {"content-type": "application/problem+json"}

ENVELOPE = {
    "items": [{"id": "s1", "name": "Sprint Alfa"}, {"id": "s2", "name": "Sprint Beta"}],
    "page": 1,
    "per_page": 20,
    "total": 2,
    "total_pages": 1,
}

# Kolumny celowo w kolejności innej niż todo, in_progress, in_review, done.
BOARD = {
    "sprint": {"id": "s1", "name": "Sprint Alfa"},
    "columns": {
        "done": [{"id": "t4", "title": "Zadanie D"}],
        "in_review": [{"id": "t3", "title": "Zadanie C"}],
        "todo": [{"id": "t1", "title": "Zadanie A"}],
        "in_progress": [{"id": "t2", "title": "Zadanie B"}],
    },
}
EMPTY_BOARD = {"sprint": None, "columns": {}}


def _setup(monkeypatch, mock_transport, *, payload=None, status=200, project=SLUG, handler=None):
    monkeypatch.setenv("MONOLYNX_TOKEN", "tok-test-1234")
    monkeypatch.setenv("MONOLYNX_ENDPOINT", "https://api.test")
    monkeypatch.setenv("COLUMNS", "200")
    if project is not None:
        monkeypatch.setenv("MONOLYNX_PROJECT", project)

    def default_handler(request: httpx.Request) -> httpx.Response:
        if status >= 400:
            return httpx.Response(status, json={"title": "Błąd walidacji", "detail": "Zła data"}, headers=_PROBLEM_CT)
        return httpx.Response(status, json=payload if payload is not None else {"id": "s1", "name": "Sprint"})

    transport, requests = mock_transport(handler or default_handler)
    monkeypatch.setattr(
        _common,
        "MonolynxClient",
        functools.partial(client_mod.MonolynxClient, transport=transport, sleep=lambda seconds: None),
    )
    return requests


def _run(runner, monkeypatch, mock_transport, args, *, fmt="json", payload=None, status=200, project=SLUG, handler=None):
    requests = _setup(monkeypatch, mock_transport, payload=payload, status=status, project=project, handler=handler)
    result = runner.invoke(app, ["-o", fmt, "sprint", *args])
    return result, requests


def _body(request: httpx.Request):
    return json.loads(request.content)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_hits_sprints_endpoint_and_maps_status(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["list", "--status", "active"], payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"{P}/sprints"
    assert dict(requests[0].url.params) == {"status": "active", "page": "1", "per_page": "100"}


def test_list_without_status_sends_no_status_param(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["list"], payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert dict(requests[0].url.params) == {"page": "1", "per_page": "100"}


def _two_page_handler(page1, page2):
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        items = {"1": page1, "2": page2}[page]
        return httpx.Response(200, json={"items": items, "page": int(page), "per_page": 100, "total": 3, "total_pages": 2})

    return handler


def test_list_fetches_all_pages_and_emits_flat_list_in_api_order(runner, monkeypatch, mock_transport):
    # Kolejność inna niż alfabetyczna: mutant sortujący albo gubiący drugą stronę pada.
    page1 = [{"id": "s3", "name": "Zulu"}, {"id": "s1", "name": "Alfa"}]
    page2 = [{"id": "s2", "name": "Mid"}]

    result, requests = _run(runner, monkeypatch, mock_transport, ["list"], handler=_two_page_handler(page1, page2))

    assert result.exit_code == 0, result.output
    assert [request.url.params["page"] for request in requests] == ["1", "2"]
    assert all(request.url.path == f"{P}/sprints" for request in requests)
    assert json.loads(result.stdout) == [*page1, *page2]


def test_list_status_filter_is_sent_on_every_page(runner, monkeypatch, mock_transport):
    result, requests = _run(
        runner, monkeypatch, mock_transport, ["list", "--status", "completed"], handler=_two_page_handler([{"id": "s1"}], [{"id": "s2"}])
    )

    assert result.exit_code == 0, result.output
    assert len(requests) == 2
    assert [request.url.params.get("status") for request in requests] == ["completed", "completed"]


def test_list_has_no_page_option(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["list", "--page", "3"], payload=ENVELOPE)

    assert result.exit_code == 2
    assert requests == []


def test_list_renders_json_items_list(runner, monkeypatch, mock_transport):
    result, _ = _run(runner, monkeypatch, mock_transport, ["list"], fmt="json", payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == ENVELOPE["items"]


def test_list_renders_yaml_items_list(runner, monkeypatch, mock_transport):
    result, _ = _run(runner, monkeypatch, mock_transport, ["list"], fmt="yaml", payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.stdout) == ENVELOPE["items"]


def test_list_renders_csv_header_and_rows(runner, monkeypatch, mock_transport):
    result, _ = _run(runner, monkeypatch, mock_transport, ["list"], fmt="csv", payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert list(csv.reader(io.StringIO(result.stdout))) == [["id", "name"], ["s1", "Sprint Alfa"], ["s2", "Sprint Beta"]]


def test_list_renders_table_with_values(runner, monkeypatch, mock_transport):
    result, _ = _run(runner, monkeypatch, mock_transport, ["list"], fmt="table", payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert "Sprint Alfa" in result.stdout
    assert "Sprint Beta" in result.stdout


# ---------------------------------------------------------------------------
# get / create / update / start / complete
# ---------------------------------------------------------------------------


def test_get_hits_sprint_by_id(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["get", "sprint-42"])

    assert result.exit_code == 0, result.output
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"{P}/sprints/sprint-42"


@pytest.mark.parametrize(
    "args",
    [
        ["get", ".."],
        ["get", "."],
        ["update", "..", "--name", "X"],
        ["start", ".."],
        ["complete", ".."],
        ["burndown", ".."],
    ],
    ids=["get-dotdot", "get-dot", "update-dotdot", "start-dotdot", "complete-dotdot", "burndown-dotdot"],
)
def test_sprint_id_dot_segment_exits_2_without_request(runner, monkeypatch, mock_transport, args):
    result, requests = _run(runner, monkeypatch, mock_transport, args)

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert requests == []


def test_get_sprint_id_with_slash_is_encoded_in_path(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["get", "a/b"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path == f"{P}/sprints/a%2Fb".encode()


def test_project_slug_dotdot_exits_2_without_request(runner, monkeypatch, mock_transport):
    requests = _setup(monkeypatch, mock_transport, payload=ENVELOPE)

    result = runner.invoke(app, ["-o", "json", "--project", "..", "sprint", "list"])

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert requests == []


def test_project_slug_with_slash_is_encoded_in_path(runner, monkeypatch, mock_transport):
    requests = _setup(monkeypatch, mock_transport, payload=ENVELOPE)

    result = runner.invoke(app, ["-o", "json", "--project", "a/b", "sprint", "list"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path.startswith(b"/api/v2/projects/a%2Fb/sprints")


def test_create_maps_every_flag_to_body(runner, monkeypatch, mock_transport):
    result, requests = _run(
        runner,
        monkeypatch,
        mock_transport,
        ["create", "--name", "Sprint 5", "--start", "2026-10-01", "--end", "2026-10-14", "--goal", "Wydać CLI"],
    )

    assert result.exit_code == 0, result.output
    assert requests[0].method == "POST"
    assert requests[0].url.path == f"{P}/sprints"
    assert _body(requests[0]) == {"name": "Sprint 5", "start_date": "2026-10-01", "end_date": "2026-10-14", "goal": "Wydać CLI"}


def test_create_minimal_body_has_name_and_start_only(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["create", "--name", "Sprint 5", "--start", "2026-10-01"])

    assert result.exit_code == 0, result.output
    assert _body(requests[0]) == {"name": "Sprint 5", "start_date": "2026-10-01"}


@pytest.mark.parametrize("args", [["--name", "Sprint 5"], ["--start", "2026-10-01"]])
def test_create_missing_required_option_exits_2_without_request(runner, monkeypatch, mock_transport, args):
    result, requests = _run(runner, monkeypatch, mock_transport, ["create", *args])

    assert result.exit_code == 2
    assert requests == []


def test_update_maps_every_flag_to_body(runner, monkeypatch, mock_transport):
    result, requests = _run(
        runner,
        monkeypatch,
        mock_transport,
        ["update", "sprint-42", "--name", "Nowa nazwa", "--goal", "Nowy cel", "--start", "2026-10-02", "--end", "2026-10-15"],
    )

    assert result.exit_code == 0, result.output
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == f"{P}/sprints/sprint-42"
    assert _body(requests[0]) == {"name": "Nowa nazwa", "goal": "Nowy cel", "start_date": "2026-10-02", "end_date": "2026-10-15"}


def test_update_single_flag_sends_only_that_field(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["update", "sprint-42", "--goal", "Tylko cel"])

    assert result.exit_code == 0, result.output
    assert _body(requests[0]) == {"goal": "Tylko cel"}


def test_update_without_any_change_exits_2_without_request(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["update", "sprint-42"])

    assert result.exit_code == 2
    assert requests == []
    assert result.stderr.strip() != ""


@pytest.mark.parametrize("action", ["start", "complete"])
def test_start_and_complete_post_to_action_path(runner, monkeypatch, mock_transport, action):
    result, requests = _run(runner, monkeypatch, mock_transport, [action, "sprint-42"])

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == f"{P}/sprints/sprint-42/{action}"


# ---------------------------------------------------------------------------
# [eceb1b71] board
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["table", "csv"])
def test_board_without_sprint_prints_exact_message_on_stdout(runner, monkeypatch, mock_transport, fmt):
    result, requests = _run(runner, monkeypatch, mock_transport, ["board"], fmt=fmt, payload=EMPTY_BOARD)

    assert result.exit_code == 0, result.output
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"{P}/board"
    assert result.stdout.strip() == "(Brak aktywnego sprintu)"


@pytest.mark.parametrize("fmt", ["json", "yaml"])
def test_board_without_sprint_emits_raw_response_for_machine_formats(runner, monkeypatch, mock_transport, fmt):
    result, _ = _run(runner, monkeypatch, mock_transport, ["board"], fmt=fmt, payload=EMPTY_BOARD)

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout) if fmt == "json" else yaml.safe_load(result.stdout)
    assert parsed == EMPTY_BOARD
    assert "Brak aktywnego sprintu" not in result.stdout


@pytest.mark.parametrize("fmt", ["json", "yaml"])
def test_board_with_sprint_emits_raw_response_for_machine_formats(runner, monkeypatch, mock_transport, fmt):
    result, _ = _run(runner, monkeypatch, mock_transport, ["board"], fmt=fmt, payload=BOARD)

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout) if fmt == "json" else yaml.safe_load(result.stdout)
    assert parsed == BOARD


def test_board_csv_rows_carry_status_column_in_fixed_column_order(runner, monkeypatch, mock_transport):
    result, _ = _run(runner, monkeypatch, mock_transport, ["board"], fmt="csv", payload=BOARD)

    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(io.StringIO(result.stdout)))
    assert [(r["status"], r["id"], r["title"]) for r in rows] == [
        ("todo", "t1", "Zadanie A"),
        ("in_progress", "t2", "Zadanie B"),
        ("in_review", "t3", "Zadanie C"),
        ("done", "t4", "Zadanie D"),
    ]
    assert next(csv.reader(io.StringIO(result.stdout)))[0] == "status"


def test_board_table_shows_rows_with_status(runner, monkeypatch, mock_transport):
    result, _ = _run(runner, monkeypatch, mock_transport, ["board"], fmt="table", payload=BOARD)

    assert result.exit_code == 0, result.output
    for text in ("status", "todo", "in_progress", "in_review", "done", "Zadanie A", "Zadanie D"):
        assert text in result.stdout


def test_board_with_sprint_reports_sprint_name_on_stderr_not_stdout(runner, monkeypatch, mock_transport):
    result, _ = _run(runner, monkeypatch, mock_transport, ["board"], fmt="csv", payload=BOARD)

    assert result.exit_code == 0, result.output
    assert "Sprint: Sprint Alfa" in result.stderr
    assert "Sprint Alfa" not in result.stdout


@pytest.mark.parametrize("fmt", ["csv", "table"])
def test_board_quiet_suppresses_sprint_name_but_keeps_rows_on_stdout(runner, monkeypatch, mock_transport, fmt):
    requests = _setup(monkeypatch, mock_transport, payload=BOARD)

    result = runner.invoke(app, ["-q", "-o", fmt, "sprint", "board"])

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert "Sprint:" not in result.stderr
    assert "Zadanie A" in result.stdout
    assert "Zadanie D" in result.stdout


# ---------------------------------------------------------------------------
# burndown
# ---------------------------------------------------------------------------


def test_burndown_without_argument_omits_sprint_id(runner, monkeypatch, mock_transport):
    payload = {"points": [{"date": "2026-10-01", "remaining": 10}]}

    result, requests = _run(runner, monkeypatch, mock_transport, ["burndown"], payload=payload)

    assert result.exit_code == 0, result.output
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"{P}/burndown"
    assert "sprint_id" not in requests[0].url.params
    assert json.loads(result.stdout) == payload


def test_burndown_with_argument_sends_sprint_id(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["burndown", "sprint-42"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.path == f"{P}/burndown"
    assert dict(requests[0].url.params) == {"sprint_id": "sprint-42"}


# ---------------------------------------------------------------------------
# błędy lokalne i API
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("args", [["list"], ["get", "s1"], ["board"], ["burndown"], ["start", "s1"]])
def test_missing_project_exits_2_without_request(runner, monkeypatch, mock_transport, args):
    result, requests = _run(runner, monkeypatch, mock_transport, args, project=None)

    assert result.exit_code == 2
    assert requests == []
    assert "projekt" in result.stderr.lower()


def test_api_422_exits_1_and_reports_detail_on_stderr(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["create", "--name", "S", "--start", "zła-data"], status=422)

    assert result.exit_code == 1
    assert len(requests) == 1
    assert "Zła data" in result.stderr
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# [3a619f8b] struktura komend
# ---------------------------------------------------------------------------


def test_sprint_group_exposes_all_commands():
    sprint = typer.main.get_command(app).commands["sprint"]

    assert set(sprint.commands) == {"list", "get", "create", "update", "start", "complete", "board", "burndown"}


def test_sprint_help_lists_board_and_burndown(runner):
    result = runner.invoke(app, ["sprint", "--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0
    assert "board" in result.stdout
    assert "burndown" in result.stdout


def test_time_help_shows_no_commands(runner):
    result = runner.invoke(app, ["time", "--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0
    assert "Commands" not in result.stdout
