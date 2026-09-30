"""Testy komend monolynx_cli.commands.member: list/invite/remove.

Testy komend CLI (MON-128). Punkt wstrzykniecia HTTP to nazwa
modulowa `monolynx_cli.commands._common.MonolynxClient` (wzorzec `_patch_client` z test_auth_commands.py).
Projekt dla `member *` rozstrzygany: lokalne `--project` > globalne `--project` > MONOLYNX_PROJECT > profil.
"""

from __future__ import annotations

import functools
import json

import httpx
import pytest
from monolynx_cli import client as client_mod
from monolynx_cli import config as config_mod
from monolynx_cli.commands import _common
from monolynx_cli.main import app

pytestmark = pytest.mark.unit

_PROBLEM_CT = {"content-type": "application/problem+json"}

_UID_ZED = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_UID_ALPHA = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_UID_MID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
_UID_TARGET = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def _member(uid, email, role="member"):
    return {"user_id": uid, "name": email.split("@")[0], "email": email, "role": role, "joined_at": "2026-01-02T00:00:00Z"}


# Kolejnosc NIE alfabetyczna wg emaila i rola RBAC bez mapowania.
_ZED = _member(_UID_ZED, "zed@example.com", "owner")
_ALPHA = _member(_UID_ALPHA, "alpha@example.com", "Tester")
_MID = _member(_UID_MID, "mid@example.com", "admin")


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


def _members_handler(items):
    return lambda request: httpx.Response(200, json=_page(items))


# ---------------------------------------------------------------------------
# member list: zrodlo projektu
# ---------------------------------------------------------------------------


def test_list_project_from_global_flag(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _members_handler([_ZED]))

    result = runner.invoke(app, ["--project", "globalny", "-o", "json", "member", "list"])

    assert result.exit_code == 0, result.output
    assert [(req.method, req.url.path) for req in requests] == [("GET", "/api/v2/projects/globalny/members")]


def test_list_project_from_local_flag(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _members_handler([_ZED]))

    result = runner.invoke(app, ["-o", "json", "member", "list", "--project", "lokalny"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.path == "/api/v2/projects/lokalny/members"


def test_list_local_project_wins_over_global(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _members_handler([_ZED]))

    result = runner.invoke(app, ["--project", "globalny", "-o", "json", "member", "list", "--project", "lokalny"])

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].url.path == "/api/v2/projects/lokalny/members"


def test_list_project_from_env(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("MONOLYNX_PROJECT", "z-env")
    requests = _patch_client(monkeypatch, mock_transport, _members_handler([_ZED]))

    result = runner.invoke(app, ["-o", "json", "member", "list"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.path == "/api/v2/projects/z-env/members"


def test_list_local_project_wins_over_env(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("MONOLYNX_PROJECT", "z-env")
    requests = _patch_client(monkeypatch, mock_transport, _members_handler([_ZED]))

    result = runner.invoke(app, ["-o", "json", "member", "list", "--project", "lokalny"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.path == "/api/v2/projects/lokalny/members"


def test_list_project_from_profile(runner, monkeypatch, mock_transport):
    config_mod.save_config({"general": {"active_profile": "p"}, "profile": {"p": {"project": "z-profilu"}}})
    requests = _patch_client(monkeypatch, mock_transport, _members_handler([_ZED]))

    result = runner.invoke(app, ["-o", "json", "member", "list"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.path == "/api/v2/projects/z-profilu/members"


def test_list_without_project_exits_2_and_sends_nothing(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _members_handler([_ZED]))

    result = runner.invoke(app, ["member", "list"])

    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
    assert "Brak projektu" in result.output
    assert requests == []


def test_list_encodes_special_characters_in_project_slug(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _members_handler([_ZED]))

    result = runner.invoke(app, ["-o", "json", "member", "list", "--project", "a/b c"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path.startswith(b"/api/v2/projects/a%2Fb%20c/members")


# ---------------------------------------------------------------------------
# member list: dane
# ---------------------------------------------------------------------------


def test_list_keeps_api_order_and_unmapped_roles(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _members_handler([_ZED, _ALPHA, _MID]))

    result = runner.invoke(app, ["-o", "json", "member", "list", "--project", "p"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert [row["email"] for row in data] == ["zed@example.com", "alpha@example.com", "mid@example.com"]
    assert [row["role"] for row in data] == ["owner", "Tester", "admin"]
    assert "total_pages" not in result.stdout


def test_list_two_pages_returns_all_members(runner, monkeypatch, mock_transport):
    pages = {
        "1": _page([_ZED, _ALPHA], page=1, total_pages=2),
        "2": _page([_MID], page=2, total_pages=2),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params["page"]])

    requests = _patch_client(monkeypatch, mock_transport, handler)

    result = runner.invoke(app, ["-o", "json", "member", "list", "--project", "p"])

    assert result.exit_code == 0, result.output
    assert [row["user_id"] for row in json.loads(result.stdout)] == [_UID_ZED, _UID_ALPHA, _UID_MID]
    assert [req.url.params["page"] for req in requests] == ["1", "2"]
    assert all(req.url.params["per_page"] == "100" for req in requests)


def test_list_table_shows_email_and_custom_role(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    _patch_client(monkeypatch, mock_transport, _members_handler([_ZED, _ALPHA]))

    result = runner.invoke(app, ["-o", "table", "member", "list", "--project", "p"])

    assert result.exit_code == 0, result.output
    assert "alpha@example.com" in result.stdout
    assert "Tester" in result.stdout


# ---------------------------------------------------------------------------
# member invite
# ---------------------------------------------------------------------------

_INVITED = {
    "email": "nowy@example.com",
    "role": "member",
    "is_new_user": True,
    "invitation_token": "tok-zaproszenia",
    "expires_at": "2026-02-01T00:00:00Z",
}


def test_invite_default_role_is_member(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, lambda request: httpx.Response(201, json=_INVITED))

    result = runner.invoke(app, ["-o", "json", "member", "invite", "nowy@example.com", "--project", "p"])

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/api/v2/projects/p/members"
    assert json.loads(requests[0].content) == {"email": "nowy@example.com", "role": "member"}
    assert json.loads(result.stdout)["is_new_user"] is True


def test_invite_role_admin_is_sent(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, lambda request: httpx.Response(201, json={**_INVITED, "role": "admin"}))

    result = runner.invoke(app, ["member", "invite", "nowy@example.com", "--project", "p", "--role", "admin"])

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"email": "nowy@example.com", "role": "admin"}


def test_invite_role_owner_rejected_with_exit_2_and_no_request(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, lambda request: httpx.Response(201, json=_INVITED))

    result = runner.invoke(app, ["member", "invite", "nowy@example.com", "--project", "p", "--role", "owner"])

    assert result.exit_code == 2
    assert requests == []


def test_invite_uses_global_project(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, lambda request: httpx.Response(201, json=_INVITED))

    result = runner.invoke(app, ["--project", "globalny", "member", "invite", "nowy@example.com"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.path == "/api/v2/projects/globalny/members"


def test_invite_without_project_exits_2_and_sends_nothing(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, lambda request: httpx.Response(201, json=_INVITED))

    result = runner.invoke(app, ["member", "invite", "nowy@example.com"])

    assert result.exit_code == 2
    assert "Brak projektu" in result.output
    assert requests == []


def test_invite_api_error_exits_1_with_detail(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, lambda request: _problem(422, "Blad walidacji", "Uzytkownik juz jest czlonkiem"))

    result = runner.invoke(app, ["member", "invite", "nowy@example.com", "--project", "p"])

    assert result.exit_code == 1
    assert "Uzytkownik juz jest czlonkiem" in result.stderr


# ---------------------------------------------------------------------------
# member remove
# ---------------------------------------------------------------------------


def _remove_handler(members_pages, delete_response=None):
    delete_response = delete_response or httpx.Response(204)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=members_pages[request.url.params["page"]])
        return delete_response

    return handler


_TARGET = _member(_UID_TARGET, "Anna@Example.com")
_SINGLE_PAGE = {"1": _page([_ZED, _TARGET, _MID])}


def test_remove_yes_resolves_email_via_get_then_deletes_by_user_id(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, _remove_handler(_SINGLE_PAGE))

    result = runner.invoke(app, ["member", "remove", "Anna@Example.com", "--project", "p", "--yes"])

    assert result.exit_code == 0, result.output
    assert [(req.method, req.url.path) for req in requests] == [
        ("GET", "/api/v2/projects/p/members"),
        ("DELETE", f"/api/v2/projects/p/members/{_UID_TARGET}"),
    ]
    assert result.stdout == ""
    assert "Usunięto Anna@Example.com z projektu p." in result.output


def test_remove_matches_email_case_insensitively(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, _remove_handler(_SINGLE_PAGE))

    result = runner.invoke(app, ["member", "remove", "anna@example.COM", "--project", "p", "--yes"])

    assert result.exit_code == 0, result.output
    assert requests[-1].method == "DELETE"
    assert requests[-1].url.path == f"/api/v2/projects/p/members/{_UID_TARGET}"


def test_remove_finds_member_on_second_page(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    pages = {
        "1": _page([_ZED, _MID], page=1, total_pages=2),
        "2": _page([_TARGET], page=2, total_pages=2),
    }
    requests = _patch_client(monkeypatch, mock_transport, _remove_handler(pages))

    result = runner.invoke(app, ["member", "remove", "anna@example.com", "--project", "p", "--yes"])

    assert result.exit_code == 0, result.output
    assert [req.method for req in requests] == ["GET", "GET", "DELETE"]
    assert requests[-1].url.path == f"/api/v2/projects/p/members/{_UID_TARGET}"


def test_remove_requires_exact_email_match_not_substring(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    lookalikes = _page(
        [
            _member(_UID_ZED, "anna@example.com.pl"),
            _member(_UID_MID, "xanna@example.com"),
            _member(_UID_ALPHA, "anna@example.co"),
        ]
    )
    requests = _patch_client(monkeypatch, mock_transport, _remove_handler({"1": lookalikes}))

    result = runner.invoke(app, ["member", "remove", "anna@example.com", "--project", "p", "--yes"])

    assert result.exit_code == 1
    assert [req.method for req in requests] == ["GET"]


def test_remove_email_not_in_list_exits_1_with_message_and_no_delete(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, _remove_handler(_SINGLE_PAGE))

    result = runner.invoke(app, ["member", "remove", "ktos@example.com", "--project", "p", "--yes"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "ktos@example.com nie jest członkiem projektu p." in result.output
    assert [req.method for req in requests] == ["GET"]
    assert result.stdout == ""


def test_remove_owner_422_exits_nonzero_with_api_message_on_stderr(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    owner_email_member = _member(_UID_ZED, "zed@example.com", "owner")
    handler = _remove_handler(
        {"1": _page([owner_email_member])},
        delete_response=_problem(422, "Blad walidacji", "Nie można usunąć ownera projektu"),
    )
    requests = _patch_client(monkeypatch, mock_transport, handler)

    result = runner.invoke(app, ["member", "remove", "zed@example.com", "--project", "p", "--yes"])

    assert result.exit_code == 1
    assert "Nie można usunąć ownera projektu" in result.stderr
    assert "Usunięto" not in result.output
    assert requests[-1].method == "DELETE"


def test_remove_quiet_suppresses_success_message(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, _remove_handler(_SINGLE_PAGE))

    result = runner.invoke(app, ["--quiet", "member", "remove", "anna@example.com", "--project", "p", "--yes"])

    assert result.exit_code == 0, result.output
    assert requests[-1].method == "DELETE"
    assert "Usunięto" not in result.output


def test_remove_non_tty_without_yes_exits_2_and_sends_nothing(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, _remove_handler(_SINGLE_PAGE))

    result = runner.invoke(app, ["member", "remove", "anna@example.com", "--project", "p"], input="y\n")

    assert result.exit_code == 2
    assert "Operacja wymaga potwierdzenia: dodaj --yes." in result.output
    assert requests == []


def test_remove_tty_refusal_exits_1_and_sends_nothing(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, True)
    requests = _patch_client(monkeypatch, mock_transport, _remove_handler(_SINGLE_PAGE))

    result = runner.invoke(app, ["member", "remove", "anna@example.com", "--project", "p"], input="n\n")

    assert result.exit_code == 1
    assert "Usunąć anna@example.com z projektu p?" in result.output
    assert "Anulowano." in result.output
    assert requests == []


def test_remove_tty_consent_resolves_and_deletes(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, True)
    requests = _patch_client(monkeypatch, mock_transport, _remove_handler(_SINGLE_PAGE))

    result = runner.invoke(app, ["member", "remove", "anna@example.com", "--project", "p"], input="y\n")

    assert result.exit_code == 0, result.output
    assert [req.method for req in requests] == ["GET", "DELETE"]
    assert requests[-1].url.path == f"/api/v2/projects/p/members/{_UID_TARGET}"


def test_remove_without_project_exits_2_and_sends_nothing(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, _remove_handler(_SINGLE_PAGE))

    result = runner.invoke(app, ["member", "remove", "anna@example.com", "--yes"])

    assert result.exit_code == 2
    assert "Brak projektu" in result.output
    assert requests == []


@pytest.mark.parametrize(
    "args",
    [
        ["member", "list", "--project", ".."],
        ["member", "invite", "a@example.com", "--project", ".."],
        ["member", "remove", "anna@example.com", "--project", "..", "--yes"],
        ["member", "list", "--project", "."],
    ],
    ids=["list-dotdot", "invite-dotdot", "remove-dotdot", "list-dot"],
)
def test_member_project_slug_dot_segment_exits_2_without_http(runner, monkeypatch, mock_transport, args):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, _members_handler([_ZED]))

    result = runner.invoke(app, args)

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert requests == []


def test_remove_uses_global_project(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, _remove_handler(_SINGLE_PAGE))

    result = runner.invoke(app, ["--project", "globalny", "member", "remove", "anna@example.com", "--yes"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.path == "/api/v2/projects/globalny/members"
    assert requests[-1].url.path == f"/api/v2/projects/globalny/members/{_UID_TARGET}"
