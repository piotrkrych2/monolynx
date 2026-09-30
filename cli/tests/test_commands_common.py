"""Testy monolynx_cli.commands._common: fetch_all, require_project, confirm_destructive, path_segment, load_settings.

Testy wspolnych helperow komend CLI (MON-128).
TTY symulujemy hookiem `_stdin_is_tty` w module `_common`; prompty sprawdzamy przez maly probe-app Typer,
bo `typer.confirm` czyta stdin podstawiany przez CliRunner.
"""

from __future__ import annotations

import httpx
import pytest
import typer
from monolynx_cli import config as config_mod
from monolynx_cli.client import MonolynxAPIError, MonolynxClient
from monolynx_cli.commands import _common
from monolynx_cli.commands._common import confirm_destructive, fetch_all, load_settings, path_segment, require_project
from monolynx_cli.config import Settings
from monolynx_cli.options import GlobalOptions

pytestmark = pytest.mark.unit

_PROBLEM_CT = {"content-type": "application/problem+json"}


def _client(transport: httpx.MockTransport):
    return MonolynxClient("https://api.test", "tok-1234567890", transport=transport, sleep=lambda seconds: None)


def _paged_handler(pages: dict[int, object]):
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(200, json=pages[page])

    return handler


def _page(items, *, page=1, total_pages=1):
    return {"items": items, "page": page, "per_page": 100, "total": len(items), "total_pages": total_pages}


# ---------------------------------------------------------------------------
# fetch_all
# ---------------------------------------------------------------------------


def test_fetch_all_collects_items_from_two_pages_in_api_order(mock_transport):
    # Kolejnosc inna niz alfabetyczna: zabija mutanta sortujacego wynik.
    pages = {
        1: _page([{"slug": "zulu"}, {"slug": "alpha"}], page=1, total_pages=2),
        2: _page([{"slug": "mid"}], page=2, total_pages=2),
    }
    transport, requests = mock_transport(_paged_handler(pages))

    with _client(transport) as client:
        result = fetch_all(client, "/api/v2/projects")

    assert result == [{"slug": "zulu"}, {"slug": "alpha"}, {"slug": "mid"}]
    assert len(requests) == 2
    assert [req.url.params["page"] for req in requests] == ["1", "2"]
    assert all(req.url.params["per_page"] == "100" for req in requests)
    assert all(req.url.path == "/api/v2/projects" for req in requests)


def test_fetch_all_single_page_makes_exactly_one_request(mock_transport):
    transport, requests = mock_transport(_paged_handler({1: _page([{"a": 1}])}))

    with _client(transport) as client:
        result = fetch_all(client, "/api/v2/projects")

    assert result == [{"a": 1}]
    assert len(requests) == 1


def test_fetch_all_three_pages_requests_pages_1_2_3_only(mock_transport):
    pages = {n: _page([{"n": n}], page=n, total_pages=3) for n in (1, 2, 3)}
    transport, requests = mock_transport(_paged_handler(pages))

    with _client(transport) as client:
        result = fetch_all(client, "/api/v2/projects")

    assert result == [{"n": 1}, {"n": 2}, {"n": 3}]
    assert [req.url.params["page"] for req in requests] == ["1", "2", "3"]


def test_fetch_all_empty_items_returns_empty_list(mock_transport):
    transport, requests = mock_transport(_paged_handler({1: _page([])}))

    with _client(transport) as client:
        assert fetch_all(client, "/api/v2/projects") == []
    assert len(requests) == 1


_BAD_PAYLOADS = [
    pytest.param([{"a": 1}], id="payload-nie-dict"),
    pytest.param({"total_pages": 1}, id="brak-items"),
    pytest.param({"items": {"a": 1}, "total_pages": 1}, id="items-nie-lista"),
    pytest.param({"items": [{"a": 1}, "tekst"], "total_pages": 1}, id="element-nie-dict"),
    pytest.param({"items": [], "total_pages": "2"}, id="total_pages-string"),
    pytest.param({"items": [], "total_pages": True}, id="total_pages-bool"),
    pytest.param({"items": [], "total_pages": 0}, id="total_pages-zero"),
    pytest.param({"items": []}, id="brak-total_pages"),
]


@pytest.mark.parametrize("payload", _BAD_PAYLOADS)
def test_fetch_all_bad_first_page_shape_raises_api_error(mock_transport, payload):
    transport, _requests = mock_transport(_paged_handler({1: payload}))

    with _client(transport) as client, pytest.raises(MonolynxAPIError) as exc_info:
        fetch_all(client, "/api/v2/projects")

    assert exc_info.value.status == 200
    assert exc_info.value.exit_code == 1


def test_fetch_all_bad_second_page_shape_raises_api_error(mock_transport):
    pages = {
        1: _page([{"a": 1}], page=1, total_pages=2),
        2: {"items": "nie-lista", "total_pages": 2},
    }
    transport, requests = mock_transport(_paged_handler(pages))

    with _client(transport) as client, pytest.raises(MonolynxAPIError):
        fetch_all(client, "/api/v2/projects")

    assert len(requests) == 2


# ---------------------------------------------------------------------------
# require_project
# ---------------------------------------------------------------------------


def _settings(project):
    return Settings(endpoint="https://api.test", project=project, profile="default", auth_type="token", bearer="tok")


def test_require_project_returns_slug_when_set():
    assert require_project(_settings("moj-projekt")) == "moj-projekt"


@pytest.mark.parametrize("value", [None, ""])
def test_require_project_missing_exits_2_with_message(capsys, value):
    with pytest.raises(typer.Exit) as exc_info:
        require_project(_settings(value))

    assert exc_info.value.exit_code == 2
    err = capsys.readouterr().err
    assert "Brak projektu" in err
    assert "MONOLYNX_PROJECT" in err


# ---------------------------------------------------------------------------
# confirm_destructive
# ---------------------------------------------------------------------------


def _probe_app():
    probe = typer.Typer()

    @probe.command()
    def run(yes: bool = typer.Option(False, "--yes")) -> None:
        confirm_destructive("Czy na pewno usunac?", yes=yes)
        typer.echo("PRZESZLO")

    return probe


def _set_tty(monkeypatch, value):
    monkeypatch.setattr(_common, "_stdin_is_tty", lambda: value)


def test_confirm_destructive_yes_skips_prompt_even_without_tty(runner, monkeypatch):
    _set_tty(monkeypatch, False)

    result = runner.invoke(_probe_app(), ["--yes"])

    assert result.exit_code == 0, result.output
    assert "PRZESZLO" in result.stdout
    assert "Czy na pewno usunac?" not in result.output


def test_confirm_destructive_yes_skips_prompt_on_tty(runner, monkeypatch):
    _set_tty(monkeypatch, True)

    result = runner.invoke(_probe_app(), ["--yes"])

    assert result.exit_code == 0, result.output
    assert "Czy na pewno usunac?" not in result.output


def test_confirm_destructive_non_tty_without_yes_exits_2_with_message(runner, monkeypatch):
    _set_tty(monkeypatch, False)

    result = runner.invoke(_probe_app(), [], input="y\n")

    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
    assert "Operacja wymaga potwierdzenia: dodaj --yes." in result.output
    assert "PRZESZLO" not in result.stdout
    assert "Czy na pewno usunac?" not in result.output


def test_confirm_destructive_tty_refusal_exits_1_with_cancel_message(runner, monkeypatch):
    _set_tty(monkeypatch, True)

    result = runner.invoke(_probe_app(), [], input="n\n")

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Anulowano." in result.output
    assert "Czy na pewno usunac?" in result.output
    assert "PRZESZLO" not in result.stdout


def test_confirm_destructive_tty_empty_answer_defaults_to_refusal(runner, monkeypatch):
    _set_tty(monkeypatch, True)

    result = runner.invoke(_probe_app(), [], input="\n")

    assert result.exit_code == 1
    assert "PRZESZLO" not in result.stdout


def test_confirm_destructive_tty_consent_passes(runner, monkeypatch):
    _set_tty(monkeypatch, True)

    result = runner.invoke(_probe_app(), [], input="y\n")

    assert result.exit_code == 0, result.output
    assert "PRZESZLO" in result.stdout
    assert "Anulowano." not in result.output


# ---------------------------------------------------------------------------
# path_segment
# ---------------------------------------------------------------------------


def test_path_segment_encodes_slash_and_space():
    assert path_segment("a/b c") == "a%2Fb%20c"


def test_path_segment_leaves_plain_slug_unchanged():
    assert path_segment("moj-projekt-1") == "moj-projekt-1"


def test_path_segment_encodes_query_and_fragment_chars():
    assert path_segment("x?y#z") == "x%3Fy%23z"


def _exit_code(exc: BaseException) -> int | None:
    # typer.Exit niesie `exit_code`, SystemExit `code`; test sprawdza kod wyjścia, nie typ wyjątku.
    return getattr(exc, "exit_code", getattr(exc, "code", None))


@pytest.mark.parametrize("value", [".", "..", ""], ids=["dot", "dotdot", "empty"])
def test_path_segment_dot_segments_and_empty_end_command_with_exit_2(capsys, value):
    with pytest.raises((typer.Exit, SystemExit)) as exc_info:
        path_segment(value)

    assert _exit_code(exc_info.value) == 2
    assert capsys.readouterr().err.strip() != ""


@pytest.mark.parametrize(
    "value",
    ["...", ".a", "a.", "..a", "a..b"],
    ids=["three-dots", "dot-prefix", "dot-suffix", "dotdot-prefix", "dotdot-inside"],
)
def test_path_segment_only_exact_dot_segments_are_rejected(value):
    # Granica: tylko dokładnie `.` i `..`; dłuższe ciągi z kropkami to zwykłe wartości.
    assert path_segment(value) == value


def test_path_segment_encodes_percent_so_encoded_dotdot_cannot_sneak_through():
    assert path_segment("%2e%2e") == "%252e%252e"


# ---------------------------------------------------------------------------
# project_path
# ---------------------------------------------------------------------------


def test_project_path_encodes_slug_slash_and_space():
    assert _common.project_path("a/b c", "/tickets") == "/api/v2/projects/a%2Fb%20c/tickets"


def test_project_path_plain_slug_without_suffix():
    assert _common.project_path("moj-projekt") == "/api/v2/projects/moj-projekt"


@pytest.mark.parametrize("slug", [".", "..", ""], ids=["dot", "dotdot", "empty"])
def test_project_path_dot_segment_slug_ends_command_with_exit_2(capsys, slug):
    with pytest.raises((typer.Exit, SystemExit)) as exc_info:
        _common.project_path(slug, "/tickets")

    assert _exit_code(exc_info.value) == 2
    assert capsys.readouterr().err.strip() != ""


# ---------------------------------------------------------------------------
# read_text_option: plik nie-UTF-8 to błąd użycia (kontrakt dziedziczony po wiki._resolve_content)
# ---------------------------------------------------------------------------


def test_read_text_option_non_utf8_file_exits_2_with_message(capsys, tmp_path):
    path = tmp_path / "binarny.md"
    path.write_bytes(b"\xff\xfe\x00zly")

    with pytest.raises((typer.Exit, SystemExit)) as exc_info:
        _common.read_text_option(None, path, option="--content", file_option="--file")

    assert _exit_code(exc_info.value) == 2
    assert "Nie można odczytać pliku" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# load_settings
# ---------------------------------------------------------------------------


def _settings_probe(global_project):
    probe = typer.Typer()

    @probe.command()
    def run(ctx: typer.Context, project: str | None = typer.Option(None, "--project")) -> None:
        ctx.obj = GlobalOptions(project=global_project)
        settings = load_settings(ctx, project)
        typer.echo(f"project={settings.project}")

    return probe


def test_load_settings_local_project_wins_over_global(runner):
    result = runner.invoke(_settings_probe("globalny"), ["--project", "lokalny"])

    assert result.exit_code == 0, result.output
    assert "project=lokalny" in result.stdout


def test_load_settings_uses_global_project_when_no_local(runner):
    result = runner.invoke(_settings_probe("globalny"), [])

    assert result.exit_code == 0, result.output
    assert "project=globalny" in result.stdout


def test_load_settings_none_when_no_project_anywhere(runner):
    result = runner.invoke(_settings_probe(None), [])

    assert result.exit_code == 0, result.output
    assert "project=None" in result.stdout


def test_load_settings_broken_config_exits_1_with_message(runner):
    path = config_mod.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("to nie jest [poprawny toml", encoding="utf-8")

    result = runner.invoke(_settings_probe(None), [])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "uszkodzony" in result.output
