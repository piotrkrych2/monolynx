"""Testy komend monolynx_cli.commands.project: list/get/create/update/delete/summary.

Testy komend CLI (MON-128). Punkt wstrzykniecia HTTP to nazwa
modulowa `monolynx_cli.commands._common.MonolynxClient`, podmieniana jak w test_auth_commands.py na
`functools.partial(MonolynxClient, transport=..., sleep=...)`. Dane asertujemy na `result.stdout`,
komunikaty stderr na `result.output`. TTY symulujemy hookiem `monolynx_cli.commands._common._stdin_is_tty`.
"""

from __future__ import annotations

import csv
import functools
import io
import json

import httpx
import pytest
import yaml
from monolynx_cli import client as client_mod
from monolynx_cli.commands import _common
from monolynx_cli.main import app

pytestmark = pytest.mark.unit

_PROBLEM_CT = {"content-type": "application/problem+json"}

_P1 = {
    "id": "11111111-1111-4111-8111-111111111111",
    "name": "Zulu",
    "slug": "zulu",
    "code": "ZUL",
    "description": None,
    "role": "owner",
    "created_at": "2026-01-02T00:00:00Z",
}
_P2 = {**_P1, "id": "22222222-2222-4222-8222-222222222222", "name": "Alfa", "slug": "alfa", "code": "ALF", "role": "member"}
_P3 = {**_P1, "id": "33333333-3333-4333-8333-333333333333", "name": "Mid", "slug": "mid", "code": "MID", "role": "admin"}


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("MONOLYNX_TOKEN", "tok-1234567890")
    monkeypatch.setenv("MONOLYNX_ENDPOINT", "https://api.test")


def _patch_client(monkeypatch, mock_transport, handler):
    transport, requests = mock_transport(handler)
    monkeypatch.setattr(
        _common,
        "MonolynxClient",
        functools.partial(client_mod.MonolynxClient, transport=transport, sleep=lambda seconds: None),
    )
    return requests


def _set_tty(monkeypatch, value):
    monkeypatch.setattr(_common, "_stdin_is_tty", lambda: value)


def _problem(status, title, detail):
    return httpx.Response(status, json={"title": title, "detail": detail}, headers=_PROBLEM_CT)


def _page(items, *, page=1, total_pages=1):
    return {"items": items, "page": page, "per_page": 100, "total": len(items), "total_pages": total_pages}


def _one_response(response):
    return lambda request: response


# ---------------------------------------------------------------------------
# project list
# ---------------------------------------------------------------------------


def test_list_json_outputs_items_list_not_envelope(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _one_response(httpx.Response(200, json=_page([_P1, _P2]))))

    result = runner.invoke(app, ["-o", "json", "project", "list"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert data == [_P1, _P2]
    assert "total_pages" not in result.stdout
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/api/v2/projects"
    assert requests[0].headers["authorization"] == "Bearer tok-1234567890"


def test_list_two_pages_returns_all_rows_in_api_order(runner, monkeypatch, mock_transport):
    pages = {
        "1": _page([_P1, _P2], page=1, total_pages=2),
        "2": _page([_P3], page=2, total_pages=2),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params["page"]])

    requests = _patch_client(monkeypatch, mock_transport, handler)

    result = runner.invoke(app, ["-o", "json", "project", "list"])

    assert result.exit_code == 0, result.output
    assert [row["slug"] for row in json.loads(result.stdout)] == ["zulu", "alfa", "mid"]
    assert len(requests) == 2
    assert [req.url.params["page"] for req in requests] == ["1", "2"]
    assert all(req.url.params["per_page"] == "100" for req in requests)


def test_list_table_shows_slugs_and_roles(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    _patch_client(monkeypatch, mock_transport, _one_response(httpx.Response(200, json=_page([_P1, _P2]))))

    result = runner.invoke(app, ["-o", "table", "project", "list"])

    assert result.exit_code == 0, result.output
    assert "zulu" in result.stdout
    assert "alfa" in result.stdout
    assert "owner" in result.stdout
    assert "total_pages" not in result.stdout


def test_list_yaml_outputs_list(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _one_response(httpx.Response(200, json=_page([_P1, _P2]))))

    result = runner.invoke(app, ["-o", "yaml", "project", "list"])

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.stdout) == [_P1, _P2]


def test_list_csv_has_header_and_one_row_per_project(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _one_response(httpx.Response(200, json=_page([_P1, _P2]))))

    result = runner.invoke(app, ["-o", "csv", "project", "list"])

    assert result.exit_code == 0, result.output
    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert len(rows) == 3
    assert "slug" in rows[0]
    slug_index = rows[0].index("slug")
    assert [row[slug_index] for row in rows[1:]] == ["zulu", "alfa"]


def test_list_forbidden_exits_3(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _one_response(_problem(403, "Zabronione", "Brak dostepu")))

    result = runner.invoke(app, ["project", "list"])

    assert result.exit_code == 3
    assert "Brak dostepu" in result.output


# ---------------------------------------------------------------------------
# project get
# ---------------------------------------------------------------------------


_DETAIL = {
    "id": "11111111-1111-4111-8111-111111111111",
    "name": "Zulu",
    "slug": "zulu",
    "code": "ZUL",
    "description": "Opis",
    "created_at": "2026-01-02T00:00:00Z",
    "role": "owner",
    "members_count": 4,
    "tickets_count": 17,
    "active_sprint": {"name": "Sprint 3"},
    "monitors_count": 2,
    "heartbeats_count": 1,
}


def test_get_requests_slug_path_and_prints_detail_fields(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _one_response(httpx.Response(200, json=_DETAIL)))

    result = runner.invoke(app, ["-o", "json", "project", "get", "zulu"])

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/api/v2/projects/zulu"
    data = json.loads(result.stdout)
    assert data["role"] == "owner"
    assert data["members_count"] == 4
    assert data["tickets_count"] == 17
    assert data["active_sprint"] == {"name": "Sprint 3"}


def test_get_encodes_special_characters_in_slug(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _one_response(httpx.Response(200, json=_DETAIL)))

    result = runner.invoke(app, ["-o", "json", "project", "get", "a/b c"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path == b"/api/v2/projects/a%2Fb%20c"


def test_get_not_found_exits_1_with_message_on_stderr(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _one_response(_problem(404, "Nie znaleziono", "Projekt nie istnieje")))

    result = runner.invoke(app, ["project", "get", "brak"])

    assert result.exit_code == 1
    assert "Projekt nie istnieje" in result.stderr
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# project create
# ---------------------------------------------------------------------------


_CREATED = {
    "id": "44444444-4444-4444-8444-444444444444",
    "name": "Nowy",
    "slug": "nowy",
    "code": "NOW",
    "description": None,
    "created_at": "2026-01-02T00:00:00Z",
}


def test_create_with_only_name_omits_optional_keys(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _one_response(httpx.Response(201, json=_CREATED)))

    result = runner.invoke(app, ["-o", "json", "project", "create", "--name", "Nowy"])

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/api/v2/projects"
    assert json.loads(requests[0].content) == {"name": "Nowy"}
    assert json.loads(result.stdout)["slug"] == "nowy"


def test_create_with_all_options_sends_all_fields(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _one_response(httpx.Response(201, json=_CREATED)))

    result = runner.invoke(
        app,
        ["project", "create", "--name", "Nowy", "--slug", "moj-slug", "--description", "Opis", "--code", "ABC"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"name": "Nowy", "slug": "moj-slug", "description": "Opis", "code": "ABC"}


def test_create_without_name_exits_2_without_request(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _one_response(httpx.Response(201, json=_CREATED)))

    result = runner.invoke(app, ["project", "create"])

    assert result.exit_code == 2
    assert requests == []


def test_create_validation_error_422_exits_1_with_detail(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _one_response(_problem(422, "Blad walidacji", "Slug zajety")))

    result = runner.invoke(app, ["project", "create", "--name", "Nowy", "--slug", "zajety"])

    assert result.exit_code == 1
    assert "Slug zajety" in result.stderr


# ---------------------------------------------------------------------------
# project update
# ---------------------------------------------------------------------------


def _update_ok():
    return _one_response(httpx.Response(200, json=_CREATED))


def test_update_name_only_sends_only_name(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _update_ok())

    result = runner.invoke(app, ["-o", "json", "project", "update", "nowy", "--name", "Nazwa2"])

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == "/api/v2/projects/nowy"
    assert json.loads(requests[0].content) == {"name": "Nazwa2"}
    assert json.loads(result.stdout)["id"] == _CREATED["id"]


def test_update_new_slug_is_sent_as_slug_key(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _update_ok())

    result = runner.invoke(app, ["project", "update", "nowy", "--new-slug", "nowy-2"])

    assert result.exit_code == 0, result.output
    body = json.loads(requests[0].content)
    assert body == {"slug": "nowy-2"}
    assert "new_slug" not in body
    assert requests[0].url.path == "/api/v2/projects/nowy"


def test_update_empty_description_is_sent_as_empty_string(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _update_ok())

    result = runner.invoke(app, ["project", "update", "nowy", "--description", ""])

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"description": ""}


def test_update_all_flags_sends_all_three_keys(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _update_ok())

    result = runner.invoke(
        app,
        ["project", "update", "nowy", "--name", "N", "--description", "D", "--new-slug", "s"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"name": "N", "description": "D", "slug": "s"}


def test_update_without_flags_exits_2_with_message_and_no_request(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _update_ok())

    result = runner.invoke(app, ["project", "update", "nowy"])

    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
    assert "Podaj co najmniej jedną z flag: --name, --description, --new-slug." in result.output
    assert requests == []


# ---------------------------------------------------------------------------
# project delete
# ---------------------------------------------------------------------------


def _delete_ok():
    return _one_response(httpx.Response(204))


def test_delete_yes_sends_delete_with_empty_stdout_and_stderr_message(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, _delete_ok())

    result = runner.invoke(app, ["project", "delete", "nowy", "--yes"])

    assert result.exit_code == 0, result.output
    assert [(req.method, req.url.path) for req in requests] == [("DELETE", "/api/v2/projects/nowy")]
    assert result.stdout == ""
    assert "Usunięto projekt nowy." in result.output


def test_delete_short_flag_y_works_like_yes(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, _delete_ok())

    result = runner.invoke(app, ["project", "delete", "nowy", "-y"])

    assert result.exit_code == 0, result.output
    assert len(requests) == 1


def test_delete_quiet_suppresses_success_message(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, _delete_ok())

    result = runner.invoke(app, ["--quiet", "project", "delete", "nowy", "--yes"])

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert "Usunięto" not in result.output


def test_delete_non_tty_without_yes_exits_2_and_sends_nothing(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, _delete_ok())

    result = runner.invoke(app, ["project", "delete", "nowy"], input="y\n")

    assert result.exit_code == 2
    assert "Operacja wymaga potwierdzenia: dodaj --yes." in result.output
    assert requests == []


def test_delete_tty_refusal_exits_1_and_sends_nothing(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, True)
    requests = _patch_client(monkeypatch, mock_transport, _delete_ok())

    result = runner.invoke(app, ["project", "delete", "nowy"], input="n\n")

    assert result.exit_code == 1
    assert "Anulowano." in result.output
    assert "Usunąć projekt nowy?" in result.output
    assert requests == []


def test_delete_tty_consent_sends_delete(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, True)
    requests = _patch_client(monkeypatch, mock_transport, _delete_ok())

    result = runner.invoke(app, ["project", "delete", "nowy"], input="y\n")

    assert result.exit_code == 0, result.output
    assert [(req.method, req.url.path) for req in requests] == [("DELETE", "/api/v2/projects/nowy")]


def test_delete_forbidden_exits_3(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    _patch_client(monkeypatch, mock_transport, _one_response(_problem(403, "Zabronione", "Tylko owner")))

    result = runner.invoke(app, ["project", "delete", "nowy", "--yes"])

    assert result.exit_code == 3
    assert "Tylko owner" in result.output
    assert "Usunięto" not in result.output


# ---------------------------------------------------------------------------
# project summary
# ---------------------------------------------------------------------------


def test_summary_requests_summary_path_and_prints_fields(runner, monkeypatch, mock_transport):
    body = {
        "issues_unresolved": 5,
        "monitors_failing": 1,
        "uptime_24h": 99.5,
        "active_sprint": {"name": "Sprint 3"},
        "backlog_count": 12,
    }
    requests = _patch_client(monkeypatch, mock_transport, _one_response(httpx.Response(200, json=body)))

    result = runner.invoke(app, ["-o", "json", "project", "summary", "zulu"])

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/api/v2/projects/zulu/summary"
    assert json.loads(result.stdout) == body


def test_summary_not_found_exits_1(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _one_response(_problem(404, "Nie znaleziono", "Projekt nie istnieje")))

    result = runner.invoke(app, ["project", "summary", "brak"])

    assert result.exit_code == 1
    assert "Projekt nie istnieje" in result.stderr


# ---------------------------------------------------------------------------
# MON-228 A6: slug w ścieżce - `.` i `..` odrzucane przed żądaniem (exit 2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["project", "get", ".."],
        ["project", "get", "."],
        ["project", "summary", ".."],
        ["project", "update", "..", "--name", "X"],
        ["project", "delete", "..", "--yes"],
    ],
    ids=["get-dotdot", "get-dot", "summary-dotdot", "update-dotdot", "delete-dotdot"],
)
def test_project_slug_dot_segment_exits_2_without_http(runner, monkeypatch, mock_transport, args):
    requests = _patch_client(monkeypatch, mock_transport, _one_response(httpx.Response(200, json={})))

    result = runner.invoke(app, args)

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert requests == []


def test_project_get_slug_with_slash_is_encoded_in_path(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _one_response(httpx.Response(200, json={"slug": "a/b"})))

    result = runner.invoke(app, ["-o", "json", "project", "get", "a/b"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path == b"/api/v2/projects/a%2Fb"
