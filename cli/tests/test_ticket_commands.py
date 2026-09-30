"""Testy komend grupy `ticket` (MON-130): mapowanie flag na request API v2 i kontrakt wyjścia.

HTTP jest mockowany przez fixture `mock_transport`; punkt wstrzyknięcia to nazwa modułowa
`monolynx_cli.commands._common.MonolynxClient`. Asercje dotyczą faktycznego żądania
(metoda, ścieżka, query, body), nie samego kodu wyjścia.
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
TICKET_UUID = "3f2b8a52-1c4d-4e7a-9b6f-0a1b2c3d4e5f"
CRITERION_ID = "9d8c7b6a-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
_PROBLEM_CT = {"content-type": "application/problem+json"}

ENVELOPE = {
    "items": [{"id": "a1", "title": "Alfa"}, {"id": "b2", "title": "Beta"}],
    "page": 1,
    "per_page": 20,
    "total": 2,
    "total_pages": 1,
}


def _setup(monkeypatch, mock_transport, *, payload=None, status=200, project=SLUG, handler=None):
    monkeypatch.setenv("MONOLYNX_TOKEN", "tok-test-1234")
    monkeypatch.setenv("MONOLYNX_ENDPOINT", "https://api.test")
    monkeypatch.setenv("COLUMNS", "200")
    if project is not None:
        monkeypatch.setenv("MONOLYNX_PROJECT", project)

    def default_handler(request: httpx.Request) -> httpx.Response:
        if status == 204:
            return httpx.Response(204)
        if status >= 400:
            return httpx.Response(status, json={"title": "Błąd walidacji", "detail": "Zły status"}, headers=_PROBLEM_CT)
        return httpx.Response(status, json=payload if payload is not None else {"id": "x", "title": "t"})

    transport, requests = mock_transport(handler or default_handler)
    monkeypatch.setattr(
        _common,
        "MonolynxClient",
        functools.partial(client_mod.MonolynxClient, transport=transport, sleep=lambda seconds: None),
    )
    return requests


def _run(runner, monkeypatch, mock_transport, args, *, fmt="json", payload=None, status=200, input=None, project=SLUG, handler=None):
    requests = _setup(monkeypatch, mock_transport, payload=payload, status=status, project=project, handler=handler)
    result = runner.invoke(app, ["-o", fmt, "ticket", *args], input=input)
    return result, requests


def _body(request: httpx.Request):
    return json.loads(request.content)


def _params(request: httpx.Request) -> dict[str, str]:
    return dict(request.url.params)


# ---------------------------------------------------------------------------
# [2fa37534] UUID i klucz trafiają do ścieżki bez zmian
# ---------------------------------------------------------------------------

_ID_COMMANDS = [
    (["get", "{t}"], "GET", "/tickets/{t}", 200),
    (["update", "{t}", "--title", "Nowy"], "PATCH", "/tickets/{t}", 200),
    (["delete", "{t}", "--yes"], "DELETE", "/tickets/{t}", 204),
    (["comment", "list", "{t}"], "GET", "/tickets/{t}/comments", 200),
    (["comment", "add", "{t}", "--body", "Treść"], "POST", "/tickets/{t}/comments", 200),
    (["ac", "list", "{t}"], "GET", "/tickets/{t}/acceptance-criteria", 200),
    (["ac", "add", "{t}", "Opis kryterium"], "POST", "/tickets/{t}/acceptance-criteria", 200),
    (["ac", "update", "{t}", CRITERION_ID, "--done"], "PATCH", f"/tickets/{{t}}/acceptance-criteria/{CRITERION_ID}", 200),
    (["ac", "delete", "{t}", CRITERION_ID], "DELETE", f"/tickets/{{t}}/acceptance-criteria/{CRITERION_ID}", 204),
]


@pytest.mark.parametrize("fmt", ["json", "yaml"])
@pytest.mark.parametrize("ticket", [TICKET_UUID, "MON-42"])
@pytest.mark.parametrize(("args", "method", "suffix", "status"), _ID_COMMANDS)
def test_ticket_identifier_is_passed_unchanged_in_path(runner, monkeypatch, mock_transport, args, method, suffix, status, ticket, fmt):
    args = [a.replace("{t}", ticket) for a in args]

    result, requests = _run(runner, monkeypatch, mock_transport, args, fmt=fmt, status=status, payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].method == method
    assert requests[0].url.path == P + suffix.replace("{t}", ticket)


# ---------------------------------------------------------------------------
# ticket list / search: mapowanie query
# ---------------------------------------------------------------------------


def test_list_maps_every_flag_to_query(runner, monkeypatch, mock_transport):
    result, requests = _run(
        runner,
        monkeypatch,
        mock_transport,
        [
            "list",
            "--status", "todo",
            "--priority", "high",
            "--search", "logowanie",
            "--sprint", "sprint-1",
            "--due-before", "2026-12-31",
            "--due-after", "2026-01-01",
            "--overdue",
            "--label", "lab-1",
            "--page", "3",
        ],
        payload=ENVELOPE,
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"{P}/tickets"
    assert _params(requests[0]) == {
        "status": "todo",
        "priority": "high",
        "search": "logowanie",
        "sprint_id": "sprint-1",
        "due_date_before": "2026-12-31",
        "due_date_after": "2026-01-01",
        "overdue": "true",
        "label_id": "lab-1",
        "page": "3",
    }


def test_list_without_flags_sends_no_filter_params(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["list"], payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    params = _params(requests[0])
    for key in ("overdue", "status", "priority", "search", "sprint_id", "label_id", "due_date_before", "due_date_after"):
        assert key not in params


def test_search_maps_query_and_flags(runner, monkeypatch, mock_transport):
    result, requests = _run(
        runner,
        monkeypatch,
        mock_transport,
        [
            "search", "błąd logowania",
            "--status", "in_progress",
            "--priority", "low",
            "--assignee", "jan@example.com",
            "--sprint", "sprint-2",
            "--due-before", "2026-12-31",
            "--due-after", "2026-01-01",
            "--page", "3",
        ],
        payload=ENVELOPE,
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"{P}/tickets/search"
    assert _params(requests[0]) == {
        "query": "błąd logowania",
        "status": "in_progress",
        "priority": "low",
        "assignee_email": "jan@example.com",
        "sprint_id": "sprint-2",
        "due_before": "2026-12-31",
        "due_after": "2026-01-01",
        "page": "3",
    }


@pytest.mark.parametrize("command", [["list"], ["search", "x"]])
def test_list_and_search_page_flag_maps_to_page_query(runner, monkeypatch, mock_transport, command):
    result, requests = _run(runner, monkeypatch, mock_transport, [*command, "--page", "3"], payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert _params(requests[0])["page"] == "3"


# ---------------------------------------------------------------------------
# [7075260d] listy w czterech formatach
# ---------------------------------------------------------------------------

_PAGED_COMMANDS = [
    ["list"],
    ["search", "x"],
]

# Komendy pobierające WSZYSTKIE strony (_common.fetch_all): wynik to płaska lista elementów, nie koperta.
_FETCH_ALL_COMMANDS = [
    ["comment", "list", "MON-1"],
    ["label", "list"],
    ["ac", "list", "MON-1"],
]

_LIST_COMMANDS = [*_PAGED_COMMANDS, *_FETCH_ALL_COMMANDS]


@pytest.mark.parametrize("command", _PAGED_COMMANDS)
def test_list_commands_render_json_envelope_unchanged(runner, monkeypatch, mock_transport, command):
    result, _ = _run(runner, monkeypatch, mock_transport, command, fmt="json", payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == ENVELOPE


@pytest.mark.parametrize("command", _PAGED_COMMANDS)
def test_list_commands_render_yaml_envelope_unchanged(runner, monkeypatch, mock_transport, command):
    result, _ = _run(runner, monkeypatch, mock_transport, command, fmt="yaml", payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.stdout) == ENVELOPE


@pytest.mark.parametrize("command", _FETCH_ALL_COMMANDS)
def test_fetch_all_commands_render_json_items_list(runner, monkeypatch, mock_transport, command):
    result, _ = _run(runner, monkeypatch, mock_transport, command, fmt="json", payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == ENVELOPE["items"]


@pytest.mark.parametrize("command", _FETCH_ALL_COMMANDS)
def test_fetch_all_commands_render_yaml_items_list(runner, monkeypatch, mock_transport, command):
    result, _ = _run(runner, monkeypatch, mock_transport, command, fmt="yaml", payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.stdout) == ENVELOPE["items"]


# ---------------------------------------------------------------------------
# MON-228 D1: comment list / label list / ac list pobierają wszystkie strony
# ---------------------------------------------------------------------------


def _two_page_handler(page1, page2):
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        items = {"1": page1, "2": page2}[page]
        return httpx.Response(200, json={"items": items, "page": int(page), "per_page": 100, "total": 3, "total_pages": 2})

    return handler


@pytest.mark.parametrize(
    ("command", "suffix"),
    [
        (["comment", "list", "MON-1"], "/tickets/MON-1/comments"),
        (["label", "list"], "/labels"),
        (["ac", "list", "MON-1"], "/tickets/MON-1/acceptance-criteria"),
    ],
    ids=["comment-list", "label-list", "ac-list"],
)
def test_fetch_all_commands_request_every_page_and_emit_flat_list(runner, monkeypatch, mock_transport, command, suffix):
    # Kolejność inna niż alfabetyczna: mutant sortujący albo gubiący drugą stronę pada.
    page1 = [{"id": "c3", "name": "Zulu"}, {"id": "c1", "name": "Alfa"}]
    page2 = [{"id": "c2", "name": "Mid"}]

    result, requests = _run(runner, monkeypatch, mock_transport, command, handler=_two_page_handler(page1, page2))

    assert result.exit_code == 0, result.output
    assert len(requests) == 2
    assert [request.url.path for request in requests] == [P + suffix, P + suffix]
    assert [dict(request.url.params) for request in requests] == [
        {"page": "1", "per_page": "100"},
        {"page": "2", "per_page": "100"},
    ]
    assert json.loads(result.stdout) == [*page1, *page2]


@pytest.mark.parametrize("command", _LIST_COMMANDS)
def test_list_commands_render_csv_header_and_rows(runner, monkeypatch, mock_transport, command):
    result, _ = _run(runner, monkeypatch, mock_transport, command, fmt="csv", payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert list(csv.reader(io.StringIO(result.stdout))) == [["id", "title"], ["a1", "Alfa"], ["b2", "Beta"]]


@pytest.mark.parametrize("command", _LIST_COMMANDS)
def test_list_commands_render_table_with_values(runner, monkeypatch, mock_transport, command):
    result, _ = _run(runner, monkeypatch, mock_transport, command, fmt="table", payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert "Alfa" in result.stdout
    assert "Beta" in result.stdout


# ---------------------------------------------------------------------------
# ticket get / create / update
# ---------------------------------------------------------------------------


def test_get_renders_single_record_json(runner, monkeypatch, mock_transport):
    record = {"id": "t1", "key": "MON-1", "title": "Rekord"}

    result, _ = _run(runner, monkeypatch, mock_transport, ["get", "MON-1"], payload=record)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == record


def test_create_minimal_body_has_only_title(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["create", "--title", "Tytuł"])

    assert result.exit_code == 0, result.output
    assert requests[0].method == "POST"
    assert requests[0].url.path == f"{P}/tickets"
    assert _body(requests[0]) == {"title": "Tytuł"}


def test_create_maps_every_flag_to_body(runner, monkeypatch, mock_transport):
    result, requests = _run(
        runner,
        monkeypatch,
        mock_transport,
        [
            "create",
            "--title", "Tytuł",
            "--description", "Opis",
            "--priority", "high",
            "--sp", "5",
            "--sprint", "sprint-1",
            "--assignee", "jan@example.com",
            "--due", "2026-12-31",
            "--label", "l1",
            "--label", "l2",
            "--ac", "a",
            "--ac", "b",
            "--spec-page", "page-1",
            "--blocked-by", "MON-1",
            "--blocked-by", "MON-2",
        ],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert _body(requests[0]) == {
        "title": "Tytuł",
        "description": "Opis",
        "priority": "high",
        "story_points": 5,
        "sprint_id": "sprint-1",
        "assignee_email": "jan@example.com",
        "due_date": "2026-12-31",
        "label_ids": ["l1", "l2"],
        "acceptance_criteria": ["a", "b"],
        "spec_page_id": "page-1",
        "blocked_by_ids": ["MON-1", "MON-2"],
    }


def test_create_without_title_exits_2_without_request(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["create", "--description", "x"])

    assert result.exit_code == 2
    assert requests == []


def test_update_maps_every_flag_to_body(runner, monkeypatch, mock_transport):
    result, requests = _run(
        runner,
        monkeypatch,
        mock_transport,
        [
            "update", "MON-42",
            "--title", "Nowy tytuł",
            "--description", "Nowy opis",
            "--status", "done",
            "--priority", "low",
            "--sp", "8",
            "--sprint", "",
            "--assignee", "",
            "--due", "",
            "--label", "l1",
            "--blocked-by", "MON-1",
        ],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert requests[0].method == "PATCH"
    assert _body(requests[0]) == {
        "title": "Nowy tytuł",
        "description": "Nowy opis",
        "status": "done",
        "priority": "low",
        "story_points": 8,
        "sprint_id": "",
        "assignee_email": "",
        "due_date": "",
        "label_ids": ["l1"],
        "blocked_by_ids": ["MON-1"],
    }


def test_update_without_any_change_exits_2_without_request(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["update", "MON-42"])

    assert result.exit_code == 2
    assert requests == []
    assert result.stderr.strip() != ""


# ---------------------------------------------------------------------------
# [3aae661b] opis z pliku / stdin
# ---------------------------------------------------------------------------

_DESC_COMMANDS = [["create", "--title", "T"], ["update", "MON-42"]]


@pytest.mark.parametrize("command", _DESC_COMMANDS)
def test_description_file_sets_description_field(runner, monkeypatch, mock_transport, tmp_path, command):
    path = tmp_path / "opis.md"
    path.write_text("Opis z pliku\nDruga linia ąę", encoding="utf-8")

    result, requests = _run(runner, monkeypatch, mock_transport, [*command, "--description-file", str(path)])

    assert result.exit_code == 0, result.output
    assert _body(requests[0])["description"] == "Opis z pliku\nDruga linia ąę"


@pytest.mark.parametrize("command", _DESC_COMMANDS)
def test_description_dash_reads_stdin(runner, monkeypatch, mock_transport, command):
    result, requests = _run(runner, monkeypatch, mock_transport, [*command, "--description", "-"], input="Opis ze stdin\n")

    assert result.exit_code == 0, result.output
    assert _body(requests[0])["description"] == "Opis ze stdin\n"


@pytest.mark.parametrize("command", _DESC_COMMANDS)
def test_description_and_description_file_together_exit_2_without_request(runner, monkeypatch, mock_transport, tmp_path, command):
    path = tmp_path / "opis.md"
    path.write_text("x", encoding="utf-8")

    result, requests = _run(runner, monkeypatch, mock_transport, [*command, "--description", "inline", "--description-file", str(path)])

    assert result.exit_code == 2
    assert requests == []


# ---------------------------------------------------------------------------
# [1fef0dad] --sp / --story-points
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", _DESC_COMMANDS)
@pytest.mark.parametrize("flag", ["--sp", "--story-points"])
def test_story_points_flags_send_integer(runner, monkeypatch, mock_transport, command, flag):
    result, requests = _run(runner, monkeypatch, mock_transport, [*command, flag, "5"])

    assert result.exit_code == 0, result.output
    body = _body(requests[0])
    assert body["story_points"] == 5
    assert isinstance(body["story_points"], int)


# ---------------------------------------------------------------------------
# [aa3d690f] blokery
# ---------------------------------------------------------------------------


def test_create_blocked_by_repeated_sends_list_in_order(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["create", "--title", "T", "--blocked-by", "MON-9", "--blocked-by", "MON-3"])

    assert result.exit_code == 0, result.output
    assert _body(requests[0])["blocked_by_ids"] == ["MON-9", "MON-3"]


def test_update_blocked_by_repeated_sends_list_in_order(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["update", "MON-42", "--blocked-by", "MON-9", "--blocked-by", "MON-3"])

    assert result.exit_code == 0, result.output
    assert _body(requests[0])["blocked_by_ids"] == ["MON-9", "MON-3"]


def test_update_without_blocker_flags_omits_blocked_by_ids(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["update", "MON-42", "--title", "Nowy"])

    assert result.exit_code == 0, result.output
    assert "blocked_by_ids" not in _body(requests[0])


def test_update_no_blockers_sends_empty_list(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["update", "MON-42", "--no-blockers"])

    assert result.exit_code == 0, result.output
    assert _body(requests[0]) == {"blocked_by_ids": []}


def test_update_blocked_by_with_no_blockers_exits_2_without_request(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["update", "MON-42", "--blocked-by", "MON-1", "--no-blockers"])

    assert result.exit_code == 2
    assert requests == []


# ---------------------------------------------------------------------------
# [651c8296] --ac
# ---------------------------------------------------------------------------


def test_create_ac_repeated_sends_acceptance_criteria_list(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["create", "--title", "T", "--ac", "b", "--ac", "a"])

    assert result.exit_code == 0, result.output
    assert _body(requests[0])["acceptance_criteria"] == ["b", "a"]


# ---------------------------------------------------------------------------
# [e0fb018e] comment add
# ---------------------------------------------------------------------------


def test_comment_add_body_dash_reads_stdin_into_content(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["comment", "add", "MON-42", "--body", "-"], input="Komentarz ze stdin\n")

    assert result.exit_code == 0, result.output
    assert requests[0].method == "POST"
    assert _body(requests[0]) == {"content": "Komentarz ze stdin\n"}


def test_comment_add_body_literal_maps_to_content(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["comment", "add", "MON-42", "--body", "Jawny tekst"])

    assert result.exit_code == 0, result.output
    assert _body(requests[0]) == {"content": "Jawny tekst"}


def test_comment_add_without_body_exits_2_without_request(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["comment", "add", "MON-42"])

    assert result.exit_code == 2
    assert requests == []


# ---------------------------------------------------------------------------
# [ee7e4a1e] delete
# ---------------------------------------------------------------------------


def test_delete_non_tty_without_yes_exits_2_with_hint_and_no_request(runner, monkeypatch, mock_transport):
    monkeypatch.setattr(_common, "_stdin_is_tty", lambda: False)

    result, requests = _run(runner, monkeypatch, mock_transport, ["delete", "MON-42"], status=204)

    assert result.exit_code == 2
    assert requests == []
    assert "--yes" in result.stderr


def test_delete_tty_declined_exits_1_without_request(runner, monkeypatch, mock_transport):
    monkeypatch.setattr(_common, "_stdin_is_tty", lambda: True)

    result, requests = _run(runner, monkeypatch, mock_transport, ["delete", "MON-42"], status=204, input="n\n")

    assert result.exit_code == 1
    assert requests == []
    assert "Anulowano" in result.stderr


def test_delete_tty_confirmed_sends_delete(runner, monkeypatch, mock_transport):
    monkeypatch.setattr(_common, "_stdin_is_tty", lambda: True)

    result, requests = _run(runner, monkeypatch, mock_transport, ["delete", "MON-42"], status=204, input="y\n")

    assert result.exit_code == 0, result.output
    assert [r.method for r in requests] == ["DELETE"]
    assert requests[0].url.path == f"{P}/tickets/MON-42"


@pytest.mark.parametrize("flag", ["--yes", "-y"])
def test_delete_yes_skips_prompt_and_reports_deleted(runner, monkeypatch, mock_transport, flag):
    monkeypatch.setattr(_common, "_stdin_is_tty", lambda: True)

    result, requests = _run(runner, monkeypatch, mock_transport, ["delete", "MON-42", flag], status=204)

    assert result.exit_code == 0, result.output
    assert [r.method for r in requests] == ["DELETE"]
    assert json.loads(result.stdout) == {"id": "MON-42", "deleted": True}


def test_ac_delete_reports_deleted_true(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["ac", "delete", "MON-42", CRITERION_ID], status=204)

    assert result.exit_code == 0, result.output
    assert [r.method for r in requests] == ["DELETE"]
    assert json.loads(result.stdout)["deleted"] is True


# ---------------------------------------------------------------------------
# bulk-update, label, ac
# ---------------------------------------------------------------------------


def test_bulk_update_maps_arguments_and_flags(runner, monkeypatch, mock_transport):
    result, requests = _run(
        runner,
        monkeypatch,
        mock_transport,
        ["bulk-update", "MON-1", "MON-2", "--status", "done", "--priority", "low", "--assignee", "", "--sprint", "", "--due", ""],
        payload={"updated": 2, "failed": []},
    )

    assert result.exit_code == 0, result.output
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == f"{P}/tickets/bulk"
    assert _body(requests[0]) == {
        "ticket_ids": ["MON-1", "MON-2"],
        "status": "done",
        "priority": "low",
        "assignee_email": "",
        "sprint_id": "",
        "due_date": "",
    }
    assert json.loads(result.stdout) == {"updated": 2, "failed": []}


def test_label_list_hits_labels_endpoint(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["label", "list"], payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"{P}/labels"


def test_label_create_maps_name_and_color(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["label", "create", "--name", "bug", "--color", "#ff0000"])

    assert result.exit_code == 0, result.output
    assert requests[0].method == "POST"
    assert requests[0].url.path == f"{P}/labels"
    assert _body(requests[0]) == {"name": "bug", "color": "#ff0000"}


def test_label_create_without_color_omits_color(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["label", "create", "--name", "bug"])

    assert result.exit_code == 0, result.output
    assert _body(requests[0]) == {"name": "bug"}


def test_ac_add_maps_description(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["ac", "add", "MON-42", "Działa offline"])

    assert result.exit_code == 0, result.output
    assert _body(requests[0]) == {"description": "Działa offline"}


@pytest.mark.parametrize(("flag", "expected"), [("--done", True), ("--not-done", False)])
def test_ac_update_done_flags_map_to_is_completed(runner, monkeypatch, mock_transport, flag, expected):
    result, requests = _run(runner, monkeypatch, mock_transport, ["ac", "update", "MON-42", CRITERION_ID, flag])

    assert result.exit_code == 0, result.output
    assert _body(requests[0]) == {"is_completed": expected}


def test_ac_update_description_only_omits_is_completed(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["ac", "update", "MON-42", CRITERION_ID, "--description", "Nowy opis"])

    assert result.exit_code == 0, result.output
    body = _body(requests[0])
    assert body == {"description": "Nowy opis"}
    assert "is_completed" not in body


def test_ac_update_without_change_exits_2_without_request(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["ac", "update", "MON-42", CRITERION_ID])

    assert result.exit_code == 2
    assert requests == []
    assert result.stderr.strip() != ""


# ---------------------------------------------------------------------------
# błędy lokalne i błędy API
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [["list"], ["get", "MON-1"], ["create", "--title", "T"], ["label", "list"], ["comment", "list", "MON-1"]],
)
def test_missing_project_exits_2_without_request(runner, monkeypatch, mock_transport, args):
    result, requests = _run(runner, monkeypatch, mock_transport, args, project=None)

    assert result.exit_code == 2
    assert requests == []
    assert "projekt" in result.stderr.lower()


def test_api_422_exits_1_and_reports_detail_on_stderr(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["update", "MON-1", "--status", "nieistniejacy"], status=422)

    assert result.exit_code == 1
    assert len(requests) == 1
    assert "Zły status" in result.stderr
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# MON-228 A6: wartości użytkownika w ścieżce - kodowanie i odrzucanie `.` / `..` przed żądaniem
# ---------------------------------------------------------------------------

_DOTDOT_COMMANDS = [
    (["get", ".."], "ticket-get"),
    (["get", "."], "ticket-get-dot"),
    (["update", "..", "--title", "X"], "ticket-update"),
    (["delete", "..", "--yes"], "ticket-delete"),
    (["comment", "list", ".."], "comment-list"),
    (["comment", "add", "..", "--body", "x"], "comment-add"),
    (["ac", "list", ".."], "ac-list"),
    (["ac", "add", "..", "Opis"], "ac-add"),
    (["ac", "update", "MON-1", "..", "--done"], "ac-update-criterion"),
    (["ac", "update", "..", CRITERION_ID, "--done"], "ac-update-ticket"),
    (["ac", "delete", "MON-1", ".."], "ac-delete-criterion"),
    (["ac", "delete", "..", CRITERION_ID], "ac-delete-ticket"),
]


@pytest.mark.parametrize("args", [entry[0] for entry in _DOTDOT_COMMANDS], ids=[entry[1] for entry in _DOTDOT_COMMANDS])
def test_dot_segment_identifier_exits_2_without_request(runner, monkeypatch, mock_transport, args):
    result, requests = _run(runner, monkeypatch, mock_transport, args, status=204)

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert requests == []


def test_ticket_with_path_traversal_is_encoded_not_interpreted(runner, monkeypatch, mock_transport):
    result, requests = _run(runner, monkeypatch, mock_transport, ["get", "../../klient-x"])

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].url.raw_path == f"{P}/tickets/..%2F..%2Fklient-x".encode()


@pytest.mark.parametrize(
    ("args", "raw_suffix"),
    [
        (["get", "a/b"], "/tickets/a%2Fb"),
        (["comment", "list", "a/b"], "/tickets/a%2Fb/comments"),
        (["ac", "update", "MON-1", "a/b", "--done"], "/tickets/MON-1/acceptance-criteria/a%2Fb"),
        (["ac", "delete", "MON-1", "a b"], "/tickets/MON-1/acceptance-criteria/a%20b"),
    ],
    ids=["ticket", "comment-list-ticket", "criterion-slash", "criterion-space"],
)
def test_identifier_with_special_characters_is_encoded_in_path(runner, monkeypatch, mock_transport, args, raw_suffix):
    result, requests = _run(runner, monkeypatch, mock_transport, args, status=200, payload=ENVELOPE)

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path.startswith(f"{P}{raw_suffix}".encode())


def test_project_slug_dotdot_exits_2_without_request(runner, monkeypatch, mock_transport):
    requests = _setup(monkeypatch, mock_transport, payload=ENVELOPE)

    result = runner.invoke(app, ["-o", "json", "--project", "..", "ticket", "list"])

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert requests == []


def test_project_slug_with_slash_is_encoded_in_ticket_path(runner, monkeypatch, mock_transport):
    requests = _setup(monkeypatch, mock_transport, payload=ENVELOPE)

    result = runner.invoke(app, ["-o", "json", "--project", "a/b", "ticket", "list"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path.startswith(b"/api/v2/projects/a%2Fb/tickets")


# ---------------------------------------------------------------------------
# MON-228 B3/B4: pomoc nie obiecuje tego, czego serwer nie robi
# ---------------------------------------------------------------------------


def _help_text(runner, args):
    result = runner.invoke(app, [*args, "--help"], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    return " ".join(result.stdout.split())


@pytest.mark.parametrize("command", [["ticket", "create"], ["ticket", "update"]], ids=["create", "update"])
def test_blocked_by_help_promises_uuid_not_key(runner, command):
    text = _help_text(runner, command)

    assert "--blocked-by" in text
    # Argument `ticket` w update mówi o kluczu zupełnie poprawnie, więc sprawdzamy tylko opis opcji.
    option_help = text.split("--blocked-by", 1)[1].split(" --", 1)[0]
    assert "uuid" in option_help.lower()
    assert "klucz" not in option_help.lower()


def test_ticket_list_search_help_does_not_promise_description_search(runner):
    text = _help_text(runner, ["ticket", "list"])

    assert "--search" in text
    option_help = text.split("--search", 1)[1].split(" --", 1)[0]
    assert "tytu" in option_help.lower()
    assert "opis" not in option_help.lower()


# ---------------------------------------------------------------------------
# [3a619f8b] struktura komend
# ---------------------------------------------------------------------------


def _click_root():
    return typer.main.get_command(app)


def test_scrum_subcommands_are_not_registered_at_root():
    root = _click_root()

    for name in ("board", "burndown", "label", "ac", "comment"):
        assert name not in root.commands


def test_ticket_group_exposes_all_commands_and_subgroups():
    ticket = _click_root().commands["ticket"]

    assert {"list", "search", "get", "create", "update", "delete", "bulk-update", "comment", "label", "ac"} <= set(ticket.commands)
    assert set(ticket.commands["comment"].commands) == {"list", "add"}
    assert set(ticket.commands["label"].commands) == {"list", "create"}
    assert set(ticket.commands["ac"].commands) == {"list", "add", "update", "delete"}


def test_ticket_help_lists_subgroups(runner):
    result = runner.invoke(app, ["ticket", "--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0
    for name in ("comment", "label", "ac"):
        assert name in result.stdout


def test_time_group_has_no_commands():
    assert _click_root().commands["time"].commands == {}
