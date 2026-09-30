"""Testy komend monolynx_cli.commands.wiki (MON-133): list, get, create, update, edit, delete, search, config get/set.

Wszystkie testy przechodza przez root app (`monolynx_cli.main.app`) z flagami globalnymi PRZED grupa
`wiki` (entrypoint gap: sam modul komendy bez roota nie dowodzi, ze grupa jest podpieta). Punkt
wstrzykniecia HTTP to nazwa modulowa `monolynx_cli.commands._common.MonolynxClient` (transport z fixture
`mock_transport`, sleep no-op). Edytor w `wiki edit` to `monolynx_cli.commands.wiki.subprocess.run`,
a tryb TTY dla `wiki delete` to helper `_stdin_is_tty` w `_common`. Import modulu wiki jest wewnatrz helperow, zeby
brak modulu dal czerwone testy, nie blad kolekcji calego pliku.
"""

from __future__ import annotations

import csv
import functools
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from monolynx_cli import client as client_mod
from monolynx_cli.main import app

pytestmark = pytest.mark.unit

SLUG = "proj-x"
PAGE_ID = "22222222-2222-4222-8222-222222222222"
PARENT_ID = "33333333-3333-4333-8333-333333333333"
BASE = f"/api/v2/projects/{SLUG}/wiki"
_PROBLEM_CT = {"content-type": "application/problem+json"}


def _patch_client(monkeypatch, mock_transport, handler):
    from monolynx_cli.commands import _common

    monkeypatch.setenv("MONOLYNX_TOKEN", "tok-value-1234")
    transport, requests = mock_transport(handler)
    monkeypatch.setattr(
        _common,
        "MonolynxClient",
        functools.partial(client_mod.MonolynxClient, transport=transport, sleep=lambda seconds: None),
    )
    return requests


def _json(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def _problem(status, title="Blad", detail="Szczegoly"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"type": "about:blank", "title": title, "status": status, "detail": detail}, headers=_PROBLEM_CT)

    return handler


def _run(runner, *args, output="json", input=None):
    return runner.invoke(app, ["-o", output, "--project", SLUG, "wiki", *args], input=input)


def _detail(content="tresc", **extra):
    base = {
        "id": PAGE_ID,
        "title": "Strona",
        "slug": "strona",
        "parent_id": None,
        "position": 0,
        "content": content,
        "is_ai_touched": False,
        "created_by": "a@example.com",
        "last_edited_by": "a@example.com",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
    }
    base.update(extra)
    return base


def _created(**extra):
    base = {"id": PAGE_ID, "title": "Nowa", "slug": "nowa", "parent_id": None, "position": 0, "is_ai_touched": False, "is_public": False}
    base.update(extra)
    return base


def _item(idx, title, depth=0):
    return {"id": f"id-{idx}", "title": title, "depth": depth, "parent_id": None, "updated_at": "2026-01-01T00:00:00Z"}


def _envelope(items, page, total_pages, total):
    return {"items": items, "page": page, "per_page": 100, "total": total, "total_pages": total_pages}


# ---------------------------------------------------------------------------
# wiki list
# ---------------------------------------------------------------------------


def test_list_fetches_all_pages_and_emits_flat_list_of_items(runner, monkeypatch, mock_transport):
    # Kolejnosc tytulow celowo niealfabetyczna: mutant sortujacy albo gubiacy jedna ze stron pada.
    page1 = [_item(1, "Zulu"), _item(2, "Alfa")]
    page2 = [_item(3, "Mid")]

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        return httpx.Response(200, json=_envelope(page1, 1, 2, 3) if page == "1" else _envelope(page2, 2, 2, 3))

    requests = _patch_client(monkeypatch, mock_transport, handler)

    result = _run(runner, "list")

    assert result.exit_code == 0, result.output
    assert [row["title"] for row in json.loads(result.stdout)] == ["Zulu", "Alfa", "Mid"]
    assert [r.url.path for r in requests] == [f"{BASE}/pages", f"{BASE}/pages"]
    assert [dict(r.url.params) for r in requests] == [{"page": "1", "per_page": "100"}, {"page": "2", "per_page": "100"}]
    assert {r.method for r in requests} == {"GET"}


def test_list_single_page_makes_exactly_one_request(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_envelope([_item(1, "Jedyna")], 1, 1, 1)))

    result = _run(runner, "list")

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert [row["title"] for row in json.loads(result.stdout)] == ["Jedyna"]


def test_list_tree_indents_title_by_depth_in_server_order(runner, monkeypatch, mock_transport):
    items = [_item(1, "Korzen", 0), _item(2, "Dziecko", 1), _item(3, "Wnuk", 2), _item(4, "Drugi", 0)]
    _patch_client(monkeypatch, mock_transport, _json(_envelope(items, 1, 1, 4)))

    result = _run(runner, "list", "--tree")

    assert result.exit_code == 0, result.output
    assert [row["title"] for row in json.loads(result.stdout)] == ["Korzen", "  Dziecko", "    Wnuk", "Drugi"]


def test_list_without_tree_keeps_titles_unindented(runner, monkeypatch, mock_transport):
    items = [_item(1, "Korzen", 0), _item(2, "Dziecko", 1)]
    _patch_client(monkeypatch, mock_transport, _json(_envelope(items, 1, 1, 2)))

    result = _run(runner, "list")

    assert [row["title"] for row in json.loads(result.stdout)] == ["Korzen", "Dziecko"]


def test_list_table_shows_titles_and_server_key_columns(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    _patch_client(monkeypatch, mock_transport, _json(_envelope([_item(1, "Tabelka")], 1, 1, 1)))

    result = _run(runner, "list", output="table")

    assert result.exit_code == 0, result.output
    assert "Tabelka" in result.stdout
    assert "updated_at" in result.stdout


def test_list_yaml_parses_to_list_of_items(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json(_envelope([_item(1, "Yaml")], 1, 1, 1)))

    result = _run(runner, "list", output="yaml")

    assert yaml.safe_load(result.stdout) == [_item(1, "Yaml")]


def test_list_csv_header_follows_server_key_order(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json(_envelope([_item(1, "Csv")], 1, 1, 1)))

    result = _run(runner, "list", output="csv")

    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows[0] == ["id", "title", "depth", "parent_id", "updated_at"]
    assert rows[1][1] == "Csv"


def test_list_empty_project_json_is_empty_list(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json(_envelope([], 1, 1, 0)))

    result = _run(runner, "list")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


# ---------------------------------------------------------------------------
# wiki get
# ---------------------------------------------------------------------------


def test_get_raw_writes_exactly_content_to_stdout(runner, monkeypatch, mock_transport):
    content = "# Tytul\n\n[bold]nie markup[/bold] zażółć\nostatnia linia bez nowej linii"
    requests = _patch_client(monkeypatch, mock_transport, _json(_detail(content)))

    result = _run(runner, "get", PAGE_ID, "--raw")

    assert result.exit_code == 0, result.output
    assert result.stdout == content
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"{BASE}/pages/{PAGE_ID}"


def test_get_raw_preserves_trailing_newline_without_adding_one(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json(_detail("linia\n")))

    result = _run(runner, "get", PAGE_ID, "--raw")

    assert result.stdout == "linia\n"


def test_get_without_raw_emits_full_payload(runner, monkeypatch, mock_transport):
    payload = _detail("tresc strony")
    _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "get", PAGE_ID)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload


def test_get_nonexistent_page_exits_1_with_error_on_stderr(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _problem(404, "Nie znaleziono", "Brak strony"))

    result = _run(runner, "get", PAGE_ID)

    assert result.exit_code == 1
    assert "Brak strony" in result.stderr
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# wiki create
# ---------------------------------------------------------------------------


def test_create_with_content_posts_only_title_and_content(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created(), 201))

    result = _run(runner, "create", "--title", "Nowa", "--content", "tresc")

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == f"{BASE}/pages"
    assert json.loads(requests[0].content) == {"title": "Nowa", "content": "tresc"}
    assert json.loads(result.stdout) == _created()


def test_create_with_optional_fields_sends_parent_position_and_public(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created(), 201))

    result = _run(runner, "create", "--title", "T", "--content", "c", "--parent", PARENT_ID, "--position", "5", "--public")

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"title": "T", "content": "c", "parent_id": PARENT_ID, "position": 5, "is_public": True}


def test_create_no_public_sends_explicit_false(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created(), 201))

    result = _run(runner, "create", "--title", "T", "--content", "c", "--no-public")

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"title": "T", "content": "c", "is_public": False}


def test_create_position_zero_is_sent_not_dropped_as_falsy(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created(), 201))

    result = _run(runner, "create", "--title", "T", "--content", "c", "--position", "0")

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"title": "T", "content": "c", "position": 0}


def test_create_content_dash_reads_stdin(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created(), 201))

    result = _run(runner, "create", "--title", "T", "--content", "-", input="treść ze stdin")

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"title": "T", "content": "treść ze stdin"}


def test_create_file_reads_content_from_file(runner, monkeypatch, mock_transport, tmp_path):
    path = tmp_path / "strona.md"
    path.write_text("z pliku ąę\nlinia2\n", encoding="utf-8")
    requests = _patch_client(monkeypatch, mock_transport, _json(_created(), 201))

    result = _run(runner, "create", "--title", "T", "--file", str(path))

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"title": "T", "content": "z pliku ąę\nlinia2\n"}


def test_create_content_and_file_together_exits_2_without_http(runner, monkeypatch, mock_transport, tmp_path):
    path = tmp_path / "strona.md"
    path.write_text("x", encoding="utf-8")
    requests = _patch_client(monkeypatch, mock_transport, _json(_created(), 201))

    result = _run(runner, "create", "--title", "T", "--content", "c", "--file", str(path))

    assert result.exit_code == 2
    assert result.stderr != ""
    assert len(requests) == 0


def test_create_without_any_content_exits_2_without_http(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created(), 201))

    result = _run(runner, "create", "--title", "T")

    assert result.exit_code == 2
    assert result.stderr != ""
    assert len(requests) == 0


def test_create_forbidden_exits_3(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _problem(403, "Brak uprawnien", "Brak uprawnienia wiki:write"))

    result = _run(runner, "create", "--title", "T", "--content", "c")

    assert result.exit_code == 3
    assert "Brak uprawnienia wiki:write" in result.stderr


# ---------------------------------------------------------------------------
# wiki update
# ---------------------------------------------------------------------------


def test_update_title_only_sends_exactly_title_key(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created(title="Nowy")))

    result = _run(runner, "update", PAGE_ID, "--title", "Nowy")

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == f"{BASE}/pages/{PAGE_ID}"
    assert json.loads(requests[0].content) == {"title": "Nowy"}


def test_update_content_dash_reads_stdin_and_sends_only_content(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created()))

    result = _run(runner, "update", PAGE_ID, "--content", "-", input="nowa treść")

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"content": "nowa treść"}


def test_update_file_sends_file_content(runner, monkeypatch, mock_transport, tmp_path):
    path = tmp_path / "nowa.md"
    path.write_text("plik ęąć\n", encoding="utf-8")
    requests = _patch_client(monkeypatch, mock_transport, _json(_created()))

    result = _run(runner, "update", PAGE_ID, "--file", str(path))

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"content": "plik ęąć\n"}


def test_update_position_zero_is_sent_not_dropped_as_falsy(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created()))

    result = _run(runner, "update", PAGE_ID, "--position", "0")

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"position": 0}


def test_update_public_flag_sets_is_public_true(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created()))

    result = _run(runner, "update", PAGE_ID, "--public")

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"is_public": True}


def test_update_no_public_flag_sets_is_public_false(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created()))

    result = _run(runner, "update", PAGE_ID, "--no-public")

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"is_public": False}


def test_update_without_public_flag_omits_is_public_key(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created()))

    result = _run(runner, "update", PAGE_ID, "--title", "T", "--position", "2")

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"title": "T", "position": 2}


def test_update_no_fields_exits_2_without_http(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created()))

    result = _run(runner, "update", PAGE_ID)

    assert result.exit_code == 2
    assert result.stderr != ""
    assert len(requests) == 0


def test_update_content_and_file_together_exits_2_without_http(runner, monkeypatch, mock_transport, tmp_path):
    path = tmp_path / "x.md"
    path.write_text("x", encoding="utf-8")
    requests = _patch_client(monkeypatch, mock_transport, _json(_created()))

    result = _run(runner, "update", PAGE_ID, "--content", "c", "--file", str(path))

    assert result.exit_code == 2
    assert len(requests) == 0


def test_update_validation_error_422_exits_1(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _problem(422, "Blad walidacji", "Zly parent"))

    result = _run(runner, "update", PAGE_ID, "--title", "T")

    assert result.exit_code == 1
    assert "Zly parent" in result.stderr


# ---------------------------------------------------------------------------
# wiki edit
# ---------------------------------------------------------------------------


def _install_editor(monkeypatch, *, new_content=None, returncode=0):
    from monolynx_cli.commands import wiki as wiki_mod

    calls: list[dict] = []

    def fake_run(argv, *args, **kwargs):
        path = Path(argv[-1])
        calls.append({"argv": list(argv), "path": path, "seen": path.read_text(encoding="utf-8")})
        if new_content is not None:
            path.write_text(new_content, encoding="utf-8")
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(wiki_mod.subprocess, "run", fake_run)
    return calls


def _get_and_patch_handler(content):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_detail(content))
        return httpx.Response(200, json=_created())

    return handler


def test_edit_changed_content_patches_exactly_new_content(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("EDITOR", "myeditor")
    calls = _install_editor(monkeypatch, new_content="nowa treść ąę")
    requests = _patch_client(monkeypatch, mock_transport, _get_and_patch_handler("stara treść"))

    result = _run(runner, "edit", PAGE_ID)

    assert result.exit_code == 0, result.output
    assert [r.method for r in requests] == ["GET", "PATCH"]
    assert requests[0].url.path == f"{BASE}/pages/{PAGE_ID}"
    assert requests[1].url.path == f"{BASE}/pages/{PAGE_ID}"
    assert json.loads(requests[1].content) == {"content": "nowa treść ąę"}
    assert len(calls) == 1
    assert calls[0]["seen"] == "stara treść"


def test_edit_removes_temp_file_after_success(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("EDITOR", "myeditor")
    calls = _install_editor(monkeypatch, new_content="zmiana")
    _patch_client(monkeypatch, mock_transport, _get_and_patch_handler("stara"))

    result = _run(runner, "edit", PAGE_ID)

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert not calls[0]["path"].exists()


def test_edit_unchanged_content_exits_0_without_patch_and_notes_on_stderr(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("EDITOR", "myeditor")
    calls = _install_editor(monkeypatch, new_content=None)
    requests = _patch_client(monkeypatch, mock_transport, _get_and_patch_handler("bez zmian"))

    result = _run(runner, "edit", PAGE_ID)

    assert result.exit_code == 0, result.output
    assert [r.method for r in requests] == ["GET"]
    assert result.stderr.strip() != ""
    assert not calls[0]["path"].exists()


def test_edit_editor_nonzero_exit_fails_without_patch_and_removes_temp_file(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("EDITOR", "myeditor")
    # Edytor zapisal zmiane, ale wyszedl z bledem: zmiana NIE moze trafic do PATCH.
    calls = _install_editor(monkeypatch, new_content="zapisane mimo bledu", returncode=1)
    requests = _patch_client(monkeypatch, mock_transport, _get_and_patch_handler("stara"))

    result = _run(runner, "edit", PAGE_ID)

    assert result.exit_code == 1
    assert result.stderr.strip() != ""
    assert [r.method for r in requests] == ["GET"]
    assert not calls[0]["path"].exists()


def test_edit_without_editor_env_falls_back_to_vi(runner, monkeypatch, mock_transport):
    monkeypatch.delenv("EDITOR", raising=False)
    calls = _install_editor(monkeypatch, new_content="zmiana")
    _patch_client(monkeypatch, mock_transport, _get_and_patch_handler("stara"))

    result = _run(runner, "edit", PAGE_ID)

    assert result.exit_code == 0, result.output
    assert calls[0]["argv"][0] == "vi"
    assert len(calls[0]["argv"]) == 2


def test_edit_editor_with_arguments_is_split_with_shlex(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("EDITOR", "code --wait")
    calls = _install_editor(monkeypatch, new_content="zmiana")
    _patch_client(monkeypatch, mock_transport, _get_and_patch_handler("stara"))

    result = _run(runner, "edit", PAGE_ID)

    assert result.exit_code == 0, result.output
    argv = calls[0]["argv"]
    assert argv[:2] == ["code", "--wait"]
    assert len(argv) == 3
    assert argv[2] == str(calls[0]["path"])


# ---------------------------------------------------------------------------
# wiki delete
# ---------------------------------------------------------------------------


def _set_tty(monkeypatch, value):
    from monolynx_cli.commands import _common

    monkeypatch.setattr(_common, "_stdin_is_tty", lambda: value)


def test_delete_yes_deletes_without_prompt_on_non_tty(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, lambda request: httpx.Response(204))

    result = _run(runner, "delete", PAGE_ID, "--yes")

    assert result.exit_code == 0, result.output
    assert [(r.method, r.url.path) for r in requests] == [("DELETE", f"{BASE}/pages/{PAGE_ID}")]


def test_delete_yes_deletes_without_prompt_on_tty(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, True)
    requests = _patch_client(monkeypatch, mock_transport, lambda request: httpx.Response(204))

    result = _run(runner, "delete", PAGE_ID, "--yes", input="")

    assert result.exit_code == 0, result.output
    assert [r.method for r in requests] == ["DELETE"]


def test_delete_without_yes_on_non_tty_exits_2_without_http(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    requests = _patch_client(monkeypatch, mock_transport, lambda request: httpx.Response(204))

    result = _run(runner, "delete", PAGE_ID)

    assert result.exit_code == 2
    assert "--yes" in result.stderr
    assert len(requests) == 0


def test_delete_tty_confirmation_declined_exits_1_without_http(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, True)
    requests = _patch_client(monkeypatch, mock_transport, lambda request: httpx.Response(204))

    result = _run(runner, "delete", PAGE_ID, input="n\n")

    assert result.exit_code == 1
    assert len(requests) == 0


def test_delete_tty_confirmation_accepted_deletes(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, True)
    requests = _patch_client(monkeypatch, mock_transport, lambda request: httpx.Response(204))

    result = _run(runner, "delete", PAGE_ID, input="y\n")

    assert result.exit_code == 0, result.output
    assert [(r.method, r.url.path) for r in requests] == [("DELETE", f"{BASE}/pages/{PAGE_ID}")]


def test_delete_forbidden_exits_3(runner, monkeypatch, mock_transport):
    _set_tty(monkeypatch, False)
    _patch_client(monkeypatch, mock_transport, _problem(403, "Brak uprawnien", "Brak uprawnienia wiki:delete"))

    result = _run(runner, "delete", PAGE_ID, "--yes")

    assert result.exit_code == 3


# ---------------------------------------------------------------------------
# wiki search
# ---------------------------------------------------------------------------


def _hit(title, snippet, similarity):
    return {"id": f"id-{title}", "title": title, "slug": title.lower(), "snippet": snippet, "similarity": similarity}


def test_search_default_limit_is_10_and_param_is_named_query(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json([_hit("Alfa", "fragment", 0.9)]))

    result = _run(runner, "search", "jak wdrozyc")

    assert result.exit_code == 0, result.output
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"{BASE}/search"
    assert dict(requests[0].url.params) == {"query": "jak wdrozyc", "limit": "10"}


def test_search_custom_limit_is_forwarded(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json([_hit("Alfa", "fragment", 0.9)]))

    result = _run(runner, "search", "x", "--limit", "3")

    assert result.exit_code == 0, result.output
    assert dict(requests[0].url.params) == {"query": "x", "limit": "3"}


def test_search_json_emits_bare_list_in_server_order(runner, monkeypatch, mock_transport):
    hits = [_hit("Zulu", "s1", 0.9), _hit("Alfa", "s2", 0.5)]
    _patch_client(monkeypatch, mock_transport, _json(hits))

    result = _run(runner, "search", "x")

    assert json.loads(result.stdout) == hits


def test_search_csv_columns_start_with_title_then_snippet(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json([_hit("Alfa", "fragment", 0.9)]))

    result = _run(runner, "search", "x", output="csv")

    header = next(csv.reader(io.StringIO(result.stdout)))
    assert header[:2] == ["title", "snippet"]
    assert set(header) == {"id", "title", "slug", "snippet", "similarity"}


def test_search_table_shows_title_and_snippet(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    _patch_client(monkeypatch, mock_transport, _json([_hit("Alfa", "ciekawy-fragment", 0.9)]))

    result = _run(runner, "search", "x", output="table")

    assert "Alfa" in result.stdout
    assert "ciekawy-fragment" in result.stdout


def test_search_empty_result_table_prints_embeddings_hint_on_stderr_and_no_table(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json([]))

    result = _run(runner, "search", "x", output="table")

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "embedding" in result.stderr.lower()


def test_search_empty_result_json_stdout_is_empty_list(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json([]))

    result = _run(runner, "search", "x")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


# ---------------------------------------------------------------------------
# wiki config get / set
# ---------------------------------------------------------------------------


def test_config_get_emits_enabled_config_with_ids(runner, monkeypatch, mock_transport):
    payload = {
        "wiki_llm_enabled": True,
        "index_page_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "log_page_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "schema_page_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    }
    requests = _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "config", "get")

    assert result.exit_code == 0, result.output
    assert [(r.method, r.url.path) for r in requests] == [("GET", f"{BASE}/config")]
    assert json.loads(result.stdout) == payload


def test_config_get_disabled_with_null_ids(runner, monkeypatch, mock_transport):
    payload = {"wiki_llm_enabled": False, "index_page_id": None, "log_page_id": None, "schema_page_id": None}
    _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "config", "get")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload


def test_config_get_table_shows_flag_key(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    payload = {"wiki_llm_enabled": False, "index_page_id": None, "log_page_id": None, "schema_page_id": None}
    _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "config", "get", output="table")

    assert "wiki_llm_enabled" in result.stdout


def test_config_set_enabled_patches_true(runner, monkeypatch, mock_transport):
    payload = {"wiki_llm_enabled": True, "index_page_id": None, "log_page_id": None, "schema_page_id": None}
    requests = _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "config", "set", "--enabled")

    assert result.exit_code == 0, result.output
    assert [(r.method, r.url.path) for r in requests] == [("PATCH", f"{BASE}/config")]
    assert json.loads(requests[0].content) == {"enabled": True}


def test_config_set_disabled_patches_false(runner, monkeypatch, mock_transport):
    payload = {"wiki_llm_enabled": False, "index_page_id": None, "log_page_id": None, "schema_page_id": None}
    requests = _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "config", "set", "--disabled")

    assert result.exit_code == 0, result.output
    assert json.loads(requests[0].content) == {"enabled": False}


def test_config_set_without_flag_exits_2_without_http(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json({}))

    result = _run(runner, "config", "set")

    assert result.exit_code == 2
    assert result.stderr != ""
    assert len(requests) == 0


def test_config_set_forbidden_exits_3(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _problem(403, "Brak uprawnien", "Brak uprawnienia settings:write"))

    result = _run(runner, "config", "set", "--enabled")

    assert result.exit_code == 3
    assert "Brak uprawnienia settings:write" in result.stderr


# ---------------------------------------------------------------------------
# wiki --help
# ---------------------------------------------------------------------------


def _command_names(help_text: str) -> set[str]:
    names: set[str] = set()
    in_section = False
    for line in help_text.splitlines():
        if not in_section:
            in_section = "Commands" in line
            continue
        if line.startswith("╰") or not line.strip():
            break
        match = re.match(r"^[│|]?\s+([a-z][a-z-]*)(?:\s|$)", line)
        if match:
            names.add(match.group(1))
    return names


def test_wiki_help_lists_supported_commands_and_omits_out_of_scope_ones(runner, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    result = runner.invoke(app, ["wiki", "--help"])

    assert result.exit_code == 0, result.output
    names = _command_names(result.stdout)
    assert {"list", "get", "create", "update", "edit", "delete", "search", "config"} <= names
    assert names.isdisjoint({"init", "lint", "backlinks", "index", "log", "attachment", "file"})


# ---------------------------------------------------------------------------
# kodowanie page_id w sciezce
# ---------------------------------------------------------------------------

_ENCODED_PATH = f"{BASE}/pages/a%2Fb".encode()


def test_get_encodes_slash_in_page_id_in_request_path(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_detail()))

    result = _run(runner, "get", "a/b")

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path == _ENCODED_PATH


def test_delete_encodes_slash_in_page_id_in_request_path(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, lambda request: httpx.Response(204))

    result = _run(runner, "delete", "a/b", "--yes")

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path == _ENCODED_PATH


def test_update_encodes_slash_in_page_id_in_request_path(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created()))

    result = _run(runner, "update", "a/b", "--title", "T")

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path == _ENCODED_PATH


def test_edit_encodes_slash_in_page_id_in_get_and_patch_paths(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("EDITOR", "myeditor")
    _install_editor(monkeypatch, new_content="zmiana")
    requests = _patch_client(monkeypatch, mock_transport, _get_and_patch_handler("stara"))

    result = _run(runner, "edit", "a/b")

    assert result.exit_code == 0, result.output
    assert [r.url.raw_path for r in requests] == [_ENCODED_PATH, _ENCODED_PATH]


# ---------------------------------------------------------------------------
# edit: edytor nie daje sie uruchomic
# ---------------------------------------------------------------------------


def test_edit_editor_not_found_exits_1_without_patch_and_removes_temp_file(runner, monkeypatch, mock_transport):
    from monolynx_cli.commands import wiki as wiki_mod

    monkeypatch.setenv("EDITOR", "nieistniejacy-edytor")
    seen: list[Path] = []

    def fake_run(argv, *args, **kwargs):
        seen.append(Path(argv[-1]))
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    monkeypatch.setattr(wiki_mod.subprocess, "run", fake_run)
    requests = _patch_client(monkeypatch, mock_transport, _get_and_patch_handler("stara"))

    result = _run(runner, "edit", PAGE_ID)

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Nie można uruchomić edytora (nieistniejacy-edytor)" in result.stderr
    assert [r.method for r in requests] == ["GET"]
    assert len(seen) == 1
    assert not seen[0].exists()


# ---------------------------------------------------------------------------
# --file: nieczytelny plik
# ---------------------------------------------------------------------------


def test_create_file_missing_path_exits_2_without_http(runner, monkeypatch, mock_transport, tmp_path):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created(), 201))

    result = _run(runner, "create", "--title", "T", "--file", str(tmp_path / "brak.md"))

    assert result.exit_code == 2
    assert "Nie można odczytać pliku" in result.stderr
    assert len(requests) == 0


def test_create_file_with_non_utf8_bytes_exits_2_without_http(runner, monkeypatch, mock_transport, tmp_path):
    path = tmp_path / "binarny.md"
    path.write_bytes(b"\xff\xfe\x00zly")
    requests = _patch_client(monkeypatch, mock_transport, _json(_created(), 201))

    result = _run(runner, "create", "--title", "T", "--file", str(path))

    assert result.exit_code == 2
    assert "Nie można odczytać pliku" in result.stderr
    assert len(requests) == 0


def test_update_file_missing_path_exits_2_without_http(runner, monkeypatch, mock_transport, tmp_path):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created()))

    result = _run(runner, "update", PAGE_ID, "--file", str(tmp_path / "brak.md"))

    assert result.exit_code == 2
    assert "Nie można odczytać pliku" in result.stderr
    assert len(requests) == 0


def test_update_file_with_non_utf8_bytes_exits_2_without_http(runner, monkeypatch, mock_transport, tmp_path):
    path = tmp_path / "binarny.md"
    path.write_bytes(b"\xff\xfe\x00zly")
    requests = _patch_client(monkeypatch, mock_transport, _json(_created()))

    result = _run(runner, "update", PAGE_ID, "--file", str(path))

    assert result.exit_code == 2
    assert "Nie można odczytać pliku" in result.stderr
    assert len(requests) == 0


# ---------------------------------------------------------------------------
# ksztalt odpowiedzi serwera: exit 1 i komunikat "Błąd: 200 Nieprawidłowa odpowiedź serwera: ..." na stderr
# ---------------------------------------------------------------------------


def test_edit_response_without_content_exits_1_without_running_editor(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("EDITOR", "myeditor")
    calls = _install_editor(monkeypatch, new_content="zmiana")
    payload = _detail()
    del payload["content"]
    requests = _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "edit", PAGE_ID)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert "Odpowiedź nie zawiera treści strony." in result.stderr
    assert calls == []
    assert [r.method for r in requests] == ["GET"]


def test_get_raw_response_without_content_exits_1_with_error_and_empty_stdout(runner, monkeypatch, mock_transport):
    payload = _detail()
    del payload["content"]
    _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "get", PAGE_ID, "--raw")

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert "Odpowiedź nie zawiera treści strony." in result.stderr
    assert result.stdout == ""


def test_list_response_with_non_list_items_exits_1(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json({"items": "nie-lista", "page": 1, "per_page": 100, "total": 0, "total_pages": 1}))

    result = _run(runner, "list")

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert "Odpowiedź nie zawiera listy items." in result.stderr
    assert result.stdout == ""


def test_search_response_that_is_object_instead_of_list_exits_1(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json({"results": []}))

    result = _run(runner, "search", "x")

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert "Oczekiwano listy wyników." in result.stderr
    assert result.stdout == ""


def test_config_get_response_that_is_list_exits_1(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json([{"wiki_llm_enabled": True}]))

    result = _run(runner, "config", "get")

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert "Oczekiwano obiektu JSON." in result.stderr
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# MON-228 A3: pusty stdin i brak projektu to błąd użycia (exit 2) przed jakimkolwiek żądaniem HTTP
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stdin", ["", "   \n\t\n"], ids=["empty", "whitespace-only"])
def test_update_content_dash_with_blank_stdin_exits_2_without_http(runner, monkeypatch, mock_transport, stdin):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created()))

    result = _run(runner, "update", PAGE_ID, "--content", "-", input=stdin)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr.strip() != ""
    assert len(requests) == 0


@pytest.mark.parametrize("stdin", ["", "   \n\t\n"], ids=["empty", "whitespace-only"])
def test_create_content_dash_with_blank_stdin_exits_2_without_http(runner, monkeypatch, mock_transport, stdin):
    requests = _patch_client(monkeypatch, mock_transport, _json(_created(), 201))

    result = _run(runner, "create", "--title", "T", "--content", "-", input=stdin)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr.strip() != ""
    assert len(requests) == 0


def test_update_title_with_content_dash_and_blank_stdin_does_not_send_title_only(runner, monkeypatch, mock_transport):
    # Pusty stdin nie może po cichu zdegradować się do zmiany samego tytułu.
    requests = _patch_client(monkeypatch, mock_transport, _json(_created()))

    result = _run(runner, "update", PAGE_ID, "--title", "Nowy", "--content", "-", input="")

    assert result.exit_code == 2
    assert len(requests) == 0


@pytest.mark.parametrize(
    "args",
    [
        ["list"],
        ["get", PAGE_ID],
        ["create", "--title", "T", "--content", "c"],
        ["update", PAGE_ID, "--title", "T"],
        ["delete", PAGE_ID, "--yes"],
        ["search", "x"],
        ["config", "get"],
        ["config", "set", "--enabled"],
    ],
    ids=["list", "get", "create", "update", "delete", "search", "config-get", "config-set"],
)
def test_wiki_command_without_project_exits_2_without_http(runner, monkeypatch, mock_transport, args):
    requests = _patch_client(monkeypatch, mock_transport, _json({}))

    result = runner.invoke(app, ["-o", "json", "wiki", *args])

    assert result.exit_code == 2
    assert "Brak projektu" in result.stderr
    assert result.stdout == ""
    assert len(requests) == 0


# ---------------------------------------------------------------------------
# MON-228 A6: wartości użytkownika w ścieżce są kodowane, `.` i `..` odrzucane przed żądaniem
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page_id", ["..", ".", ""], ids=["dotdot", "dot", "empty"])
def test_get_page_id_dot_segment_exits_2_without_http(runner, monkeypatch, mock_transport, page_id):
    requests = _patch_client(monkeypatch, mock_transport, _json(_detail()))

    result = _run(runner, "get", page_id)

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert len(requests) == 0


@pytest.mark.parametrize("subcommand", [["get"], ["update", "--title", "T"], ["delete", "--yes"]], ids=["get", "update", "delete"])
def test_page_commands_reject_dotdot_page_id_without_http(runner, monkeypatch, mock_transport, subcommand):
    requests = _patch_client(monkeypatch, mock_transport, _json(_detail()))

    result = _run(runner, subcommand[0], "..", *subcommand[1:])

    assert result.exit_code == 2
    assert len(requests) == 0


def test_get_page_id_with_slash_is_encoded_in_path(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_detail()))

    result = _run(runner, "get", "a/b")

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].url.raw_path == f"{BASE}/pages/a%2Fb".encode()


def test_project_slug_dotdot_exits_2_without_http(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json({"items": [], "page": 1, "per_page": 100, "total": 0, "total_pages": 1}))

    result = runner.invoke(app, ["-o", "json", "--project", "..", "wiki", "list"])

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert len(requests) == 0


def test_project_slug_with_slash_is_encoded_in_wiki_path(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json({"items": [], "page": 1, "per_page": 100, "total": 0, "total_pages": 1}))

    result = runner.invoke(app, ["-o", "json", "--project", "a/b", "wiki", "list"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path.startswith(b"/api/v2/projects/a%2Fb/wiki/pages")
