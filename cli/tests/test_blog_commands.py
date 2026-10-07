"""Testy komend monolynx_cli.commands.blog (MON-261, MON-262, MON-263): list, get, meta, lint, publish, unpublish.

Wszystkie testy przechodza przez root app (`monolynx_cli.main.app`) z flagami globalnymi PRZED grupa
`blog` (sama komenda bez roota nie dowodzi, ze grupa jest podpieta). Punkt wstrzykniecia HTTP to nazwa
modulowa `monolynx_cli.commands._common.MonolynxClient` (transport z fixture `mock_transport`, sleep no-op).
`blog list`, `blog get` i `blog lint` to odczyt (GET), `blog meta` to czesciowy zapis (PATCH): do body trafiaja
tylko opcje, ktore uzytkownik podal. `blog lint` konczy sie kodem 1, gdy wynik zawiera blad lintu (po wypisaniu
raportu); same ostrzezenia i informacje to kod 0. `blog publish` i `blog unpublish` to zapisy (POST): publish wysyla
body `{"force": <bool>}`, unpublish nie wysyla body; odmowa publikacji to 422 (kod 1, powod na stderr), a odpowiedz 200
z raportem zawierajacym bledy (force) to kod 0. Odpowiedz 200 o zlym ksztalcie to blad kontraktu: kod 1 i komunikat
"Nieprawidłowa odpowiedź serwera" na stderr, nigdy pusty wynik z kodem 0.
"""

from __future__ import annotations

import functools
import json
from typing import Any

import httpx
import pytest
from monolynx_cli import client as client_mod
from monolynx_cli.main import app

pytestmark = pytest.mark.unit

SLUG = "proj-x"
PAGE_ID = "22222222-2222-4222-8222-222222222222"
OTHER_ID = "33333333-3333-4333-8333-333333333333"
BASE = f"/api/v2/projects/{SLUG}/blog/posts"
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


def _problem(status, detail="Szczegoly", title="Blad"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"type": "about:blank", "title": title, "status": status, "detail": detail}, headers=_PROBLEM_CT)

    return handler


def _run(runner, *args, output="json"):
    return runner.invoke(app, ["-o", output, "--project", SLUG, "blog", *args])


def _item(page_id=PAGE_ID, title="Wpis", slug="wpis", **extra):
    base = {
        "page_id": page_id,
        "title": title,
        "slug": slug,
        "previous_slugs": [],
        "description": "Opis",
        "published_at": None,
        "author_name": "Zespół Monolynx",
        "tags": ["alfa", "beta"],
        "lang": "pl",
        "translation_of_id": None,
        "cover_attachment_id": None,
        "last_verified_at": None,
        "is_public": False,
        "public_url": None,
        "markdown_url": None,
    }
    return {**base, **extra}


def _published(**extra):
    return _item(
        is_public=True,
        published_at="2026-04-05T06:07:00Z",
        public_url="https://monolynx.test/blog/wpis",
        markdown_url="https://monolynx.test/blog/wpis.md",
        **extra,
    )


def _envelope(items, page=1, total_pages=1, total=None):
    return {"items": items, "page": page, "per_page": 100, "total": len(items) if total is None else total, "total_pages": total_pages}


def _detail(**extra):
    return {**_item(), "content": "# Treść wpisu", **extra}


def _lint_issue(rule="tldr-missing", level="error", line=3, message="Wpis nie zaczyna sie od TLDR"):
    return {"rule": rule, "level": level, "line": line, "message": message}


def _lint(issues=None, score=100, **extra):
    issues = [] if issues is None else issues
    levels = [i.get("level") for i in issues if isinstance(i, dict)]
    counts = {level: levels.count(level) for level in ("error", "warning", "info")}
    base = {
        "page_id": PAGE_ID,
        "score": score,
        "error_count": counts["error"],
        "warning_count": counts["warning"],
        "info_count": counts["info"],
        "issues": issues,
    }
    return {**base, **extra}


def _without(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in payload.items() if name != key}


# Odpowiedzi 200 o zlym ksztalcie elementu wpisu: po jednym przypadku na kazde wymagane pole i na zly typ.
_BAD_ITEMS: list[dict[str, Any]] = [
    _without(_item(), "page_id"),
    _without(_item(), "title"),
    _without(_item(), "slug"),
    _without(_item(), "is_public"),
    _without(_item(), "tags"),
    _without(_item(), "public_url"),
    _without(_item(), "markdown_url"),
    {**_item(), "page_id": 5},
    {**_item(), "title": None},
    {**_item(), "slug": ["a"]},
    {**_item(), "is_public": "tak"},
    {**_item(), "is_public": 1},
    {**_item(), "tags": "alfa"},
    {**_item(), "tags": None},
    {**_item(), "tags": [1]},
    {**_item(), "public_url": 7},
    {**_item(), "markdown_url": 7},
    # lang i published_at (MON-271): klucz musi byc obecny, lang jest tekstem, published_at tekstem albo null.
    _without(_item(), "lang"),
    {**_item(), "lang": 5},
    {**_item(), "lang": ["pl"]},
    {**_item(), "lang": None},
    _without(_item(), "published_at"),
    {**_item(), "published_at": 5},
    {**_item(), "published_at": ["2026-04-05"]},
]
_BAD_ITEM_IDS = [f"bad-item-{i}" for i in range(len(_BAD_ITEMS))]

_BAD_DETAILS: list[dict[str, Any]] = [
    _without(_detail(), "content"),
    {**_detail(), "content": 5},
    {**_detail(), "content": None},
    *({**item, "content": "x"} for item in _BAD_ITEMS),
]
_BAD_DETAIL_IDS = ["bad-content-missing", "bad-content-int", "bad-content-null", *(f"bad-item-{i}" for i in range(len(_BAD_ITEMS)))]


# ---------------------------------------------------------------------------
# Wspolne: sciezka, metoda, bledy HTTP, brak projektu
# ---------------------------------------------------------------------------

# (id, argumenty komendy, metoda, sciezka, poprawna odpowiedz)
_COMMANDS: list[tuple[str, list[str], str, str, Any]] = [
    ("list", ["list"], "GET", BASE, _envelope([_item()])),
    ("get", ["get", PAGE_ID], "GET", f"{BASE}/{PAGE_ID}", _detail()),
    ("meta", ["meta", PAGE_ID, "--lang", "en"], "PATCH", f"{BASE}/{PAGE_ID}", _item(lang="en")),
    ("lint", ["lint", PAGE_ID], "GET", f"{BASE}/{PAGE_ID}/lint", _lint([_lint_issue("last-verified-missing", "warning", 0, "Brak daty")], 95)),
    ("publish", ["publish", PAGE_ID], "POST", f"{BASE}/{PAGE_ID}/publish", {**_published(), "lint": _lint()}),
    ("unpublish", ["unpublish", PAGE_ID], "POST", f"{BASE}/{PAGE_ID}/unpublish", _item()),
]
_COMMAND_IDS = [case[0] for case in _COMMANDS]


@pytest.mark.parametrize(("_id", "args", "method", "path", "payload"), _COMMANDS, ids=_COMMAND_IDS)
def test_command_sends_expected_method_to_blog_path_of_project(runner, monkeypatch, mock_transport, _id, args, method, path, payload):
    requests = _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, *args)

    assert result.exit_code == 0, result.output
    assert [(r.method, r.url.path) for r in requests] == [(method, path)]


@pytest.mark.parametrize(("_id", "args", "_method", "_path", "payload"), _COMMANDS, ids=_COMMAND_IDS)
def test_json_output_is_full_api_structure(runner, monkeypatch, mock_transport, _id, args, _method, _path, payload):
    _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, *args)

    assert result.exit_code == 0, result.output
    expected = payload.get("items", payload)
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize(("_id", "args", "_method", "_path", "_payload"), _COMMANDS, ids=_COMMAND_IDS)
def test_forbidden_403_exits_3_with_detail_on_stderr(runner, monkeypatch, mock_transport, _id, args, _method, _path, _payload):
    _patch_client(monkeypatch, mock_transport, _problem(403, "Brak uprawnienia wiki:read"))

    result = _run(runner, *args)

    assert result.exit_code == 3
    assert "Brak uprawnienia wiki:read" in result.stderr
    assert result.stdout == ""


_BLOG_WRITE_COMMANDS = [case for case in _COMMANDS if case[0] in {"meta", "publish", "unpublish"}]


@pytest.mark.parametrize(("_id", "args", "_method", "_path", "_payload"), _BLOG_WRITE_COMMANDS, ids=[case[0] for case in _BLOG_WRITE_COMMANDS])
def test_missing_blog_write_permission_403_exits_3_with_detail_on_stderr(runner, monkeypatch, mock_transport, _id, args, _method, _path, _payload):
    _patch_client(monkeypatch, mock_transport, _problem(403, "Brak uprawnienia blog:write"))

    result = _run(runner, *args)

    assert result.exit_code == 3
    assert "Brak uprawnienia blog:write" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(("_id", "args", "_method", "_path", "_payload"), _COMMANDS, ids=_COMMAND_IDS)
def test_not_found_404_exits_1_with_detail_on_stderr(runner, monkeypatch, mock_transport, _id, args, _method, _path, _payload):
    _patch_client(monkeypatch, mock_transport, _problem(404, "Wpis bloga nie istnieje"))

    result = _run(runner, *args)

    assert result.exit_code == 1
    assert "Wpis bloga nie istnieje" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(("_id", "args", "_method", "_path", "_payload"), _COMMANDS, ids=_COMMAND_IDS)
def test_validation_422_exits_1_with_detail_on_stderr(runner, monkeypatch, mock_transport, _id, args, _method, _path, _payload):
    _patch_client(monkeypatch, mock_transport, _problem(422, "Język musi być jednym z: pl, en"))

    result = _run(runner, *args)

    assert result.exit_code == 1
    assert "Język musi być jednym z: pl, en" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(("_id", "args", "_method", "_path", "_payload"), _COMMANDS, ids=_COMMAND_IDS)
def test_network_error_exits_4(runner, monkeypatch, mock_transport, _id, args, _method, _path, _payload):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("polaczenie odrzucone", request=request)

    _patch_client(monkeypatch, mock_transport, handler)

    result = _run(runner, *args)

    assert result.exit_code == 4
    assert result.stdout == ""


@pytest.mark.parametrize(("_id", "args", "_method", "_path", "_payload"), _COMMANDS, ids=_COMMAND_IDS)
def test_missing_project_exits_2_without_http(runner, monkeypatch, mock_transport, _id, args, _method, _path, _payload):
    requests = _patch_client(monkeypatch, mock_transport, _json({}))

    result = runner.invoke(app, ["-o", "json", "blog", *args])

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert len(requests) == 0


@pytest.mark.parametrize(("_id", "args", "_method", "path", "payload"), _COMMANDS, ids=_COMMAND_IDS)
def test_project_flag_selects_project_in_path(runner, monkeypatch, mock_transport, _id, args, _method, path, payload):
    requests = _patch_client(monkeypatch, mock_transport, _json(payload))

    result = runner.invoke(app, ["-o", "json", "--project", "inny-projekt", "blog", *args])

    assert result.exit_code == 0, result.output
    assert requests[0].url.path == path.replace(SLUG, "inny-projekt")


# ---------------------------------------------------------------------------
# blog list
# ---------------------------------------------------------------------------


def test_list_fetches_all_pages_and_emits_flat_list_of_items(runner, monkeypatch, mock_transport):
    # Kolejnosc tytulow celowo niealfabetyczna: mutant sortujacy albo gubiacy jedna ze stron pada.
    page1 = [_item("p-1", "Zulu", "zulu"), _item("p-2", "Alfa", "alfa")]
    page2 = [_item("p-3", "Mid", "mid")]

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        return httpx.Response(200, json=_envelope(page1, 1, 2, 3) if page == "1" else _envelope(page2, 2, 2, 3))

    requests = _patch_client(monkeypatch, mock_transport, handler)

    result = _run(runner, "list")

    assert result.exit_code == 0, result.output
    assert [row["title"] for row in json.loads(result.stdout)] == ["Zulu", "Alfa", "Mid"]
    assert [dict(r.url.params) for r in requests] == [{"page": "1", "per_page": "100"}, {"page": "2", "per_page": "100"}]


@pytest.mark.parametrize("status", ["published", "draft"])
def test_list_status_filter_is_sent_on_every_page(runner, monkeypatch, mock_transport, status):
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(200, json=_envelope([_item(f"p-{page}", f"T{page}", f"t{page}")], page, 2, 2))

    requests = _patch_client(monkeypatch, mock_transport, handler)

    result = _run(runner, "list", "--status", status)

    assert result.exit_code == 0, result.output
    assert len(requests) == 2
    assert [r.url.params["status"] for r in requests] == [status, status]


def test_list_without_status_does_not_send_status(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_envelope([_item()])))

    result = _run(runner, "list")

    assert result.exit_code == 0, result.output
    assert "status" not in requests[0].url.params


def test_list_empty_json_is_empty_array_with_exit_0(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json(_envelope([])))

    result = _run(runner, "list")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


def test_list_table_shows_title_and_slug_of_every_item(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    items = [_item("p-1", "Pierwszy wpis", "pierwszy-wpis"), _published(page_id="p-2", title="Drugi wpis", slug="drugi-wpis")]
    _patch_client(monkeypatch, mock_transport, _json(_envelope(items)))

    result = _run(runner, "list", output="table")

    assert result.exit_code == 0, result.output
    assert "Pierwszy wpis" in result.stdout
    assert "Drugi wpis" in result.stdout
    assert "pierwszy-wpis" in result.stdout


def test_list_table_has_exactly_the_six_projection_columns_and_joins_tags(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    item = _published(title="Tytul", slug="slug-a", lang="en", tags=["a", "b"], description="ukryty-opis-xyz", author_name="ukryty-autor-xyz")
    _patch_client(monkeypatch, mock_transport, _json(_envelope([item])))

    result = _run(runner, "list", output="table")

    assert result.exit_code == 0, result.output
    header = next(line for line in result.stdout.splitlines() if "title" in line)
    for column in ("title", "slug", "is_public", "lang", "published_at", "tags"):
        assert column in header
    for column in ("description", "author_name", "page_id", "public_url", "markdown_url", "previous_slugs"):
        assert column not in result.stdout
    assert "Tytul" in result.stdout
    assert "slug-a" in result.stdout
    assert "True" in result.stdout
    assert "en" in result.stdout
    assert "2026-04-05" in result.stdout
    assert "a, b" in result.stdout
    assert "['a', 'b']" not in result.stdout
    assert "ukryty-opis-xyz" not in result.stdout
    assert "ukryty-autor-xyz" not in result.stdout


@pytest.mark.parametrize("bad", _BAD_ITEMS, ids=_BAD_ITEM_IDS)
def test_list_item_with_bad_shape_is_contract_error_exit_1_with_empty_stdout(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json(_envelope([_item(), bad])))

    result = _run(runner, "list")

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "bad",
    [
        {"items": "nie-lista", "page": 1, "per_page": 100, "total": 0, "total_pages": 1},
        {"items": ["x"], "page": 1, "per_page": 100, "total": 1, "total_pages": 1},
        {"page": 1, "per_page": 100, "total": 0, "total_pages": 1},
        [_item()],
    ],
    ids=["items-not-list", "item-not-object", "no-items", "bare-list"],
)
def test_list_bad_envelope_is_contract_error_exit_1(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json(bad))

    result = _run(runner, "list")

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


def test_list_accepts_null_published_at_and_text_published_at(runner, monkeypatch, mock_transport):
    items = [_item("p-1", "Szkic", "szkic", published_at=None, lang="en"), _item("p-2", "Data", "data", published_at="2026-04-05T06:07:00Z")]
    _patch_client(monkeypatch, mock_transport, _json(_envelope(items)))

    result = _run(runner, "list")

    assert result.exit_code == 0, result.output
    assert [(row["lang"], row["published_at"]) for row in json.loads(result.stdout)] == [("en", None), ("pl", "2026-04-05T06:07:00Z")]


def test_list_table_of_a_bad_lang_is_contract_error_not_a_blank_cell(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json(_envelope([_item(), {**_item(), "lang": 5}])))

    result = _run(runner, "list", output="table")

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


def test_list_accepts_published_and_draft_item_shapes(runner, monkeypatch, mock_transport):
    items = [_item("p-1", "Szkic", "szkic"), _published(page_id="p-2", title="Opublikowany", slug="opublikowany")]
    _patch_client(monkeypatch, mock_transport, _json(_envelope(items)))

    result = _run(runner, "list")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == items


# ---------------------------------------------------------------------------
# blog get
# ---------------------------------------------------------------------------


def test_get_json_contains_content_and_url_fields(runner, monkeypatch, mock_transport):
    payload = {**_published(), "content": "# Treść publiczna"}
    _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "get", PAGE_ID)

    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["content"] == "# Treść publiczna"
    assert body["public_url"] == "https://monolynx.test/blog/wpis"
    assert body["markdown_url"] == "https://monolynx.test/blog/wpis.md"


def test_get_table_shows_title(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    _patch_client(monkeypatch, mock_transport, _json(_detail(title="Wpis w tabeli")))

    result = _run(runner, "get", PAGE_ID, output="table")

    assert result.exit_code == 0, result.output
    assert "Wpis w tabeli" in result.stdout


@pytest.mark.parametrize("bad", _BAD_DETAILS, ids=_BAD_DETAIL_IDS)
def test_get_bad_shape_is_contract_error_exit_1_with_empty_stdout(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json(bad))

    result = _run(runner, "get", PAGE_ID)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("bad", ["tekst", 5, [_detail()], None], ids=["string", "int", "list", "null"])
def test_get_non_object_response_is_contract_error_exit_1(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json(bad))

    result = _run(runner, "get", PAGE_ID)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


def test_get_requires_page_id_argument(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_detail()))

    result = _run(runner, "get")

    assert result.exit_code == 2
    assert len(requests) == 0


# ---------------------------------------------------------------------------
# blog meta: mapowanie opcji na body
# ---------------------------------------------------------------------------


def _body(requests):
    return json.loads(requests[0].content)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--slug", "nowy-slug"], {"slug": "nowy-slug"}),
        (["--description", "Nowy opis"], {"description": "Nowy opis"}),
        (["--author", "Ada Autor"], {"author_name": "Ada Autor"}),
        (["--lang", "en"], {"lang": "en"}),
        (["--translation-of", OTHER_ID], {"translation_of_id": OTHER_ID}),
        (["--cover", OTHER_ID], {"cover_attachment_id": OTHER_ID}),
        (["--verified", "2026-08-09"], {"last_verified_at": "2026-08-09"}),
        (["--tag", "jeden"], {"tags": ["jeden"]}),
    ],
    ids=["slug", "description", "author", "lang", "translation-of", "cover", "verified", "tag"],
)
def test_meta_sends_only_the_given_option_in_body(runner, monkeypatch, mock_transport, args, expected):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(runner, "meta", PAGE_ID, *args)

    assert result.exit_code == 0, result.output
    assert _body(requests) == expected


def test_meta_all_options_together_map_to_api_field_names(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(
        runner,
        "meta",
        PAGE_ID,
        "--slug",
        "s",
        "--description",
        "d",
        "--author",
        "a",
        "--tag",
        "x",
        "--lang",
        "en",
        "--translation-of",
        OTHER_ID,
        "--cover",
        PAGE_ID,
        "--verified",
        "2026-01-02",
    )

    assert result.exit_code == 0, result.output
    assert _body(requests) == {
        "slug": "s",
        "description": "d",
        "author_name": "a",
        "tags": ["x"],
        "lang": "en",
        "translation_of_id": OTHER_ID,
        "cover_attachment_id": PAGE_ID,
        "last_verified_at": "2026-01-02",
    }


def test_meta_repeated_tag_keeps_given_order(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(runner, "meta", PAGE_ID, "--tag", "zulu", "--tag", "alfa", "--tag", "mid")

    assert result.exit_code == 0, result.output
    assert _body(requests)["tags"] == ["zulu", "alfa", "mid"]


def test_meta_no_tags_sends_empty_list(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item(tags=[])))

    result = _run(runner, "meta", PAGE_ID, "--no-tags")

    assert result.exit_code == 0, result.output
    assert _body(requests) == {"tags": []}


@pytest.mark.parametrize(
    ("option", "field"),
    [("--translation-of", "translation_of_id"), ("--cover", "cover_attachment_id"), ("--verified", "last_verified_at")],
)
def test_meta_empty_string_clears_nullable_field_with_null(runner, monkeypatch, mock_transport, option, field):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(runner, "meta", PAGE_ID, option, "")

    assert result.exit_code == 0, result.output
    body = _body(requests)
    assert body == {field: None}
    assert field in body


def test_meta_mixed_clear_and_set_in_one_call(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(runner, "meta", PAGE_ID, "--translation-of", "", "--lang", "en", "--no-tags")

    assert result.exit_code == 0, result.output
    assert _body(requests) == {"translation_of_id": None, "lang": "en", "tags": []}


def test_meta_unset_options_are_not_in_body(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(runner, "meta", PAGE_ID, "--description", "Tylko opis")

    assert result.exit_code == 0, result.output
    body = _body(requests)
    assert set(body) == {"description"}


def test_meta_without_any_option_exits_2_without_http(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(runner, "meta", PAGE_ID)

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert len(requests) == 0


def test_meta_tag_with_no_tags_exits_2_without_http(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(runner, "meta", PAGE_ID, "--tag", "x", "--no-tags")

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert len(requests) == 0


def test_meta_requires_page_id_argument(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(runner, "meta", "--lang", "en")

    assert result.exit_code == 2
    assert len(requests) == 0


def test_meta_table_shows_title_of_updated_post(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    _patch_client(monkeypatch, mock_transport, _json(_item(title="Zaktualizowany wpis")))

    result = _run(runner, "meta", PAGE_ID, "--lang", "pl", output="table")

    assert result.exit_code == 0, result.output
    assert "Zaktualizowany wpis" in result.stdout


@pytest.mark.parametrize("bad", _BAD_ITEMS, ids=_BAD_ITEM_IDS)
def test_meta_response_with_bad_shape_is_contract_error_exit_1(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json(bad))

    result = _run(runner, "meta", PAGE_ID, "--lang", "en")

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# blog lint
# ---------------------------------------------------------------------------

_ERROR_ISSUE = _lint_issue("tldr-missing", "error", 3, "Wpis nie zaczyna sie od TLDR")
_WARNING_ISSUE = _lint_issue("h2-no-question", "warning", 0, "Zaden H2 nie jest pytaniem")
_INFO_ISSUE = _lint_issue("translation-missing", "info", 0, "Brak tlumaczenia")


def test_lint_json_is_the_full_object_and_exit_1_when_there_is_an_error(runner, monkeypatch, mock_transport):
    payload = _lint([_WARNING_ISSUE, _ERROR_ISSUE], 75)
    _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "lint", PAGE_ID)

    assert result.exit_code == 1
    assert json.loads(result.stdout) == payload
    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize(
    "issues",
    [
        pytest.param([_ERROR_ISSUE, _WARNING_ISSUE, _INFO_ISSUE], id="error-first"),
        pytest.param([_WARNING_ISSUE, _INFO_ISSUE, _ERROR_ISSUE], id="error-last"),
        pytest.param([_WARNING_ISSUE, _ERROR_ISSUE, _INFO_ISSUE], id="error-in-the-middle"),
        pytest.param([_ERROR_ISSUE], id="only-error"),
    ],
)
def test_lint_exits_1_when_at_least_one_issue_is_an_error(runner, monkeypatch, mock_transport, issues):
    _patch_client(monkeypatch, mock_transport, _json(_lint(issues, 55)))

    result = _run(runner, "lint", PAGE_ID)

    assert result.exit_code == 1
    assert json.loads(result.stdout)["issues"] == issues


@pytest.mark.parametrize(
    "issues",
    [
        pytest.param([], id="no-issues"),
        pytest.param([_WARNING_ISSUE], id="only-warning"),
        pytest.param([_INFO_ISSUE], id="only-info"),
        pytest.param([_WARNING_ISSUE, _INFO_ISSUE, _lint_issue("em-dash", "warning", 9, "Myslnik")], id="warnings-and-info"),
    ],
)
def test_lint_exits_0_without_errors_and_still_prints_the_report(runner, monkeypatch, mock_transport, issues):
    payload = _lint(issues, 90)
    _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "lint", PAGE_ID)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload


def test_lint_error_exit_is_a_controlled_exit_not_an_unhandled_exception(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json(_lint([_ERROR_ISSUE], 80)))

    result = _run(runner, "lint", PAGE_ID)

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output
    assert "Nieprawidłowa odpowiedź serwera" not in result.stderr


def test_lint_percent_encodes_the_page_id_in_the_path(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_lint()))

    result = _run(runner, "lint", "a/b")

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path.decode().split("?")[0] == f"{BASE}/a%2Fb/lint"


@pytest.mark.parametrize("page_id", ["..", "."])
def test_lint_dot_segment_page_id_exits_2_without_http(runner, monkeypatch, mock_transport, page_id):
    requests = _patch_client(monkeypatch, mock_transport, _json(_lint()))

    result = _run(runner, "lint", page_id)

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert len(requests) == 0


def test_lint_table_shows_one_row_per_issue_with_the_four_columns_in_order(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    issues = [
        _lint_issue("zulu-rule", "warning", 7, "komunikat-zulu"),
        _lint_issue("alfa-rule", "error", 3, "komunikat-alfa"),
    ]
    _patch_client(monkeypatch, mock_transport, _json(_lint(issues, 75)))

    result = _run(runner, "lint", PAGE_ID, output="table")

    assert result.exit_code == 1
    header = next(line for line in result.stdout.splitlines() if "rule" in line and "level" in line)
    positions = [header.index(column) for column in ("line", "level", "rule", "message")]
    assert positions == sorted(positions)
    for token in ("zulu-rule", "komunikat-zulu", "alfa-rule", "komunikat-alfa", "warning", "error"):
        assert token in result.stdout
    assert result.stdout.index("zulu-rule") < result.stdout.index("alfa-rule")
    assert "{'rule'" not in result.stdout
    assert "page_id" not in result.stdout


def test_lint_table_prints_the_score_on_stderr_not_on_stdout(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    issues = [_lint_issue("alfa-rule", "error", 3, "komunikat"), _lint_issue("beta-rule", "error", 4, "komunikat"), _WARNING_ISSUE]
    _patch_client(monkeypatch, mock_transport, _json(_lint(issues, 55)))

    result = _run(runner, "lint", PAGE_ID, output="table")

    assert "55" in result.stderr
    assert "55" not in result.stdout


def test_lint_quiet_silences_the_score_line_but_keeps_the_rows_and_exit_code(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    issues = [_lint_issue("alfa-rule", "error", 3, "komunikat"), _lint_issue("beta-rule", "error", 4, "komunikat"), _WARNING_ISSUE]
    _patch_client(monkeypatch, mock_transport, _json(_lint(issues, 55)))

    result = runner.invoke(app, ["-o", "table", "-q", "--project", SLUG, "blog", "lint", PAGE_ID])

    assert result.exit_code == 1
    assert "55" not in result.stderr
    assert "alfa-rule" in result.stdout


def test_lint_table_without_issues_exits_0_and_reports_the_score_on_stderr(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json(_lint([], 100)))

    result = _run(runner, "lint", PAGE_ID, output="table")

    assert result.exit_code == 0, result.output
    assert "100" in result.stderr


def test_lint_yaml_output_is_the_full_object(runner, monkeypatch, mock_transport):
    import yaml

    payload = _lint([_WARNING_ISSUE], 95)
    _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "lint", PAGE_ID, output="yaml")

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.stdout) == payload


def test_lint_requires_page_id_argument(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_lint()))

    result = _run(runner, "lint")

    assert result.exit_code == 2
    assert len(requests) == 0


_BAD_LINT_ISSUES: list[Any] = [
    "tekst",
    5,
    None,
    ["rule"],
    _without(_ERROR_ISSUE, "rule"),
    _without(_ERROR_ISSUE, "level"),
    _without(_ERROR_ISSUE, "line"),
    _without(_ERROR_ISSUE, "message"),
    {**_ERROR_ISSUE, "rule": 7},
    {**_ERROR_ISSUE, "level": None},
    {**_ERROR_ISSUE, "message": ["x"]},
    {**_ERROR_ISSUE, "line": "3"},
    {**_ERROR_ISSUE, "line": 3.5},
    {**_ERROR_ISSUE, "line": True},
    {**_ERROR_ISSUE, "line": None},
]
_BAD_LINT_ISSUE_IDS = [
    "not-dict-str",
    "not-dict-int",
    "not-dict-null",
    "not-dict-list",
    "no-rule",
    "no-level",
    "no-line",
    "no-message",
    "rule-int",
    "level-null",
    "message-list",
    "line-str",
    "line-float",
    "line-bool",
    "line-null",
]


@pytest.mark.parametrize("bad", _BAD_LINT_ISSUES, ids=_BAD_LINT_ISSUE_IDS)
def test_lint_issue_with_bad_shape_is_contract_error_exit_1_with_empty_stdout(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json(_lint([_WARNING_ISSUE, bad], 80)))

    result = _run(runner, "lint", PAGE_ID)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("bad", _BAD_LINT_ISSUES, ids=_BAD_LINT_ISSUE_IDS)
def test_lint_bad_issue_is_not_masked_by_an_earlier_error_issue(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json(_lint([_ERROR_ISSUE, bad], 60)))

    result = _run(runner, "lint", PAGE_ID)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


_BAD_LINT_BODIES: list[Any] = [
    _without(_lint([_WARNING_ISSUE], 95), "issues"),
    _without(_lint([_WARNING_ISSUE], 95), "score"),
    {**_lint([_WARNING_ISSUE], 95), "issues": None},
    {**_lint([_WARNING_ISSUE], 95), "issues": "brak"},
    {**_lint([_WARNING_ISSUE], 95), "issues": {"rule": "x"}},
    {**_lint([_WARNING_ISSUE], 95), "score": True},
    {**_lint([_WARNING_ISSUE], 95), "score": "95"},
    {**_lint([_WARNING_ISSUE], 95), "score": None},
    {**_lint([_WARNING_ISSUE], 95), "score": 95.5},
    _without(_lint([_WARNING_ISSUE], 95), "page_id"),
    {**_lint([_WARNING_ISSUE], 95), "page_id": 7},
    {**_lint([_WARNING_ISSUE], 95), "page_id": None},
    *[_without(_lint([_WARNING_ISSUE], 95), name) for name in ("error_count", "warning_count", "info_count")],
    *[{**_lint([_WARNING_ISSUE], 95), name: "1"} for name in ("error_count", "warning_count", "info_count")],
    *[{**_lint([_WARNING_ISSUE], 95), name: True} for name in ("error_count", "warning_count", "info_count")],
    *[{**_lint([_WARNING_ISSUE], 95), name: None} for name in ("error_count", "warning_count", "info_count")],
    "tekst",
    5,
    None,
    [_lint()],
]
_BAD_LINT_BODY_IDS = [
    "no-issues",
    "no-score",
    "issues-null",
    "issues-str",
    "issues-dict",
    "score-bool",
    "score-str",
    "score-null",
    "score-float",
    "no-page-id",
    "page-id-int",
    "page-id-null",
    "no-error-count",
    "no-warning-count",
    "no-info-count",
    "error-count-str",
    "warning-count-str",
    "info-count-str",
    "error-count-bool",
    "warning-count-bool",
    "info-count-bool",
    "error-count-null",
    "warning-count-null",
    "info-count-null",
    "body-str",
    "body-int",
    "body-null",
    "body-list",
]


@pytest.mark.parametrize("bad", _BAD_LINT_BODIES, ids=_BAD_LINT_BODY_IDS)
def test_lint_bad_response_body_is_contract_error_exit_1_with_empty_stdout(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json(bad))

    result = _run(runner, "lint", PAGE_ID)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


def test_lint_bad_body_in_table_mode_is_contract_error_without_rows(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json(_lint([_ERROR_ISSUE, "x"], 60)))

    result = _run(runner, "lint", PAGE_ID, output="table")

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert "tldr-missing" not in result.stdout


# ---------------------------------------------------------------------------
# blog publish / blog unpublish (MON-263)
# ---------------------------------------------------------------------------


def _published_with_lint(lint=None, **extra):
    return {**_published(**extra), "lint": _lint() if lint is None else lint}


def test_publish_sends_force_false_in_body_by_default(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_published_with_lint()))

    result = _run(runner, "publish", PAGE_ID)

    assert result.exit_code == 0, result.output
    assert _body(requests) == {"force": False}


def test_publish_force_flag_sends_force_true_in_body(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_published_with_lint()))

    result = _run(runner, "publish", PAGE_ID, "--force")

    assert result.exit_code == 0, result.output
    assert _body(requests) == {"force": True}


def test_publish_force_is_a_flag_not_an_option_with_value(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_published_with_lint()))

    result = _run(runner, "publish", PAGE_ID, "--force", "false")

    assert result.exit_code == 2
    assert len(requests) == 0


def test_publish_emits_published_view_with_lint(runner, monkeypatch, mock_transport):
    payload = _published_with_lint(_lint([_WARNING_ISSUE, _INFO_ISSUE], 95))
    _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "publish", PAGE_ID)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload
    assert json.loads(result.stdout)["public_url"] == "https://monolynx.test/blog/wpis"


def test_publish_with_force_and_errors_in_the_report_is_success_exit_0_with_the_report_in_output(runner, monkeypatch, mock_transport):
    payload = _published_with_lint(_lint([_ERROR_ISSUE], 80))
    _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "publish", PAGE_ID, "--force")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["lint"]["error_count"] == 1
    assert json.loads(result.stdout)["lint"]["issues"] == [_ERROR_ISSUE]


def test_publish_refusal_422_exits_1_with_the_rule_list_on_stderr_and_empty_stdout(runner, monkeypatch, mock_transport):
    detail = "Publikacja odrzucona: wpis ma błędy lintu (1): tldr-missing (linia 3). Napraw je albo użyj force."
    _patch_client(monkeypatch, mock_transport, _problem(422, detail))

    result = _run(runner, "publish", PAGE_ID)

    assert result.exit_code == 1
    assert "Publikacja odrzucona" in result.stderr
    assert "tldr-missing" in result.stderr
    assert result.stdout == ""
    assert isinstance(result.exception, SystemExit)


def test_publish_percent_encodes_the_page_id_in_the_path(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_published_with_lint()))

    result = _run(runner, "publish", "a/b")

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path.decode().split("?")[0] == f"{BASE}/a%2Fb/publish"


@pytest.mark.parametrize("page_id", ["..", "."])
def test_publish_dot_segment_page_id_exits_2_without_http(runner, monkeypatch, mock_transport, page_id):
    requests = _patch_client(monkeypatch, mock_transport, _json(_published_with_lint()))

    result = _run(runner, "publish", page_id)

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert len(requests) == 0


def test_publish_requires_page_id_argument(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_published_with_lint()))

    result = _run(runner, "publish")

    assert result.exit_code == 2
    assert len(requests) == 0


@pytest.mark.parametrize("bad", _BAD_ITEMS, ids=_BAD_ITEM_IDS)
def test_publish_view_with_bad_shape_is_contract_error_exit_1(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json({**bad, "lint": _lint()}))

    result = _run(runner, "publish", PAGE_ID)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


def test_publish_response_without_the_report_is_contract_error_exit_1(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json(_published()))

    result = _run(runner, "publish", PAGE_ID)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("bad", _BAD_LINT_BODIES, ids=_BAD_LINT_BODY_IDS)
def test_publish_report_with_bad_shape_is_contract_error_exit_1(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json({**_published(), "lint": bad}))

    result = _run(runner, "publish", PAGE_ID)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("bad", _BAD_LINT_ISSUES, ids=_BAD_LINT_ISSUE_IDS)
def test_publish_report_issue_with_bad_shape_is_contract_error_exit_1(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json(_published_with_lint(_lint([_WARNING_ISSUE, bad], 80))))

    result = _run(runner, "publish", PAGE_ID)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("bad", [None, "tekst", 5, [_published()]], ids=["null", "str", "int", "list"])
def test_publish_non_object_response_is_contract_error_exit_1(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json(bad))

    result = _run(runner, "publish", PAGE_ID)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


def test_unpublish_sends_post_without_body(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(runner, "unpublish", PAGE_ID)

    assert result.exit_code == 0, result.output
    assert requests[0].content == b""


def test_unpublish_emits_the_private_view_without_the_report(runner, monkeypatch, mock_transport):
    payload = _item(published_at="2026-04-05T06:07:00Z")
    _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, "unpublish", PAGE_ID)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload
    assert "lint" not in json.loads(result.stdout)


def test_unpublish_has_no_force_option(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(runner, "unpublish", PAGE_ID, "--force")

    assert result.exit_code == 2
    assert len(requests) == 0


def test_unpublish_percent_encodes_the_page_id_in_the_path(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(runner, "unpublish", "a/b")

    assert result.exit_code == 0, result.output
    assert requests[0].url.raw_path.decode().split("?")[0] == f"{BASE}/a%2Fb/unpublish"


@pytest.mark.parametrize("page_id", ["..", "."])
def test_unpublish_dot_segment_page_id_exits_2_without_http(runner, monkeypatch, mock_transport, page_id):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(runner, "unpublish", page_id)

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert len(requests) == 0


def test_unpublish_requires_page_id_argument(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_item()))

    result = _run(runner, "unpublish")

    assert result.exit_code == 2
    assert len(requests) == 0


@pytest.mark.parametrize("bad", _BAD_ITEMS, ids=_BAD_ITEM_IDS)
def test_unpublish_response_with_bad_shape_is_contract_error_exit_1(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json(bad))

    result = _run(runner, "unpublish", PAGE_ID)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("bad", [None, "tekst", 5, [_item()]], ids=["null", "str", "int", "list"])
def test_unpublish_non_object_response_is_contract_error_exit_1(runner, monkeypatch, mock_transport, bad):
    _patch_client(monkeypatch, mock_transport, _json(bad))

    result = _run(runner, "unpublish", PAGE_ID)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""
