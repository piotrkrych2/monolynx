"""Testy warstwy AI bloga (MON-258): bliźniak markdown `/blog/{slug}.md`, `/blog/index.md`,
negocjacja `Accept: text/markdown`, nagłówek `Link`, JSON-LD i cache warunkowy (ETag, 304).

Wpisy powstają bezpośrednio w DB (helpery z `test_blog_public.py`), a treść z MinIO jest
podstawiana przez `monolynx.services.wiki.get_markdown` -- `get_page_content()` rozwiązuje
tę nazwę w globalsach modułu wiki, więc patch działa niezależnie od tego, gdzie nowy kod
importuje `get_page_content`.

Funkcje czyste `absolutize_markdown_urls` i `conditional_response` mają testy bez bazy
(klasy `TestAbsolutizeMarkdownUrls`, `TestConditionalResponse`).

MON-259 dopisuje: feedy `/blog/feed.xml`, `/blog/rss.xml`, `/blog/feed.json` (pełna treść, znaki specjalne,
filtr `lang`, limit, ETag), dynamiczną sekcję `## Blog` w `/llms.txt`, `/llms-full.txt` (separator, limit
bajtów, linia `Truncated`), JSON-LD z bloków (`FAQPage`, `HowTo`, `VideoObject`), linki `rel="alternate"`
do feedów w `<head>` bloga i link do bloga w `/index.md`.
"""

import hashlib
import json
import re
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import quote, urlsplit

import pytest
import yaml
from starlette.requests import Request

from monolynx import blog_ai
from monolynx.blog_ai import absolutize_markdown_urls, conditional_response, wants_markdown
from monolynx.config import settings
from monolynx.services import blog as blog_service
from monolynx.services.blog import DEFAULT_AUTHOR_NAME
from monolynx.services.blog import list_public_posts as real_list_public_posts
from monolynx.services.blog_blocks import render_post_markdown
from monolynx.services.wiki import get_page_content as real_get_page_content
from monolynx.services.wiki import update_wiki_page
from tests.integration.test_blog_public import (
    _add_owner,
    _blog_post_of,
    _create_attachment,
    _create_project,
    _create_user,
    _create_wiki_page,
    _wiki_side_effects,
)

BASE = settings.APP_URL.rstrip("/")
MD_TYPE = "text/markdown; charset=utf-8"
FRONTMATTER_KEYS = [
    "title",
    "description",
    "url",
    "lang",
    "author",
    "published",
    "modified",
    "last_verified",
    "tags",
    "translations",
    "reading_time_minutes",
    "word_count",
]
PUBLISHED = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
UPDATED = datetime(2026, 3, 2, 8, 30, tzinfo=UTC)
_LD_BLOCK = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _clear_llms_cache() -> None:
    """Czyści cache `/llms-full.txt`; odporne na brak funkcji, dopóki nie powstała implementacja (MON-271)."""
    clear = getattr(blog_ai, "clear_llms_full_cache", None)
    if clear is not None:
        clear()


@contextmanager
def _minio(store: dict):
    """MinIO jako slownik minio_path -> tresc (lub wyjatek do rzucenia); brak wpisu = pusta tresc."""

    def _get(path):
        value = store.get(path, "")
        if isinstance(value, Exception):
            raise value
        return value

    with patch("monolynx.services.wiki.get_markdown", side_effect=_get):
        yield


class _Env:
    """Projekt + uzytkownik + sklad MinIO jednego testu."""

    def __init__(self, db, project, user):
        self.db = db
        self.project = project
        self.user = user
        self.store: dict = {}

    async def post(self, name, content="Tresc wpisu.", *, title=None, public=True, **fields):
        """Tworzy wpis; zwraca (page, slug). Publiczny slug jest globalnie unikalny, wiec zawiera uid."""
        uid = _uid()
        slug = f"ai-{name}-{uid}"
        page = await _create_wiki_page(
            self.db,
            self.project,
            self.user,
            title if title is not None else f"Wpis AI {name} {uid}",
            is_public=public,
            with_post=True,
            post_slug=slug,
            **fields,
        )
        self.store[page.minio_path] = content
        return page, slug


async def _env(db_session, tag):
    uid = _uid()
    project = await _create_project(db_session, f"blog-ai-{tag}-{uid}")
    user = await _create_user(db_session, f"blog-ai-{tag}-{uid}@test.com")
    await _add_owner(db_session, project, user)
    return _Env(db_session, project, user)


def _split(text: str):
    """Dokument `.md` -> (frontmatter jako dict z yaml.safe_load, tresc po frontmatter)."""
    assert text.startswith("---\n")
    end = text.index("\n---\n", 4)
    front = yaml.safe_load(text[4:end])
    assert isinstance(front, dict)
    return front, text[end + len("\n---\n") :]


def _vary(resp) -> list[str]:
    return [v.strip().lower() for v in resp.headers.get("vary", "").split(",") if v.strip()]


def _ld_blocks(html: str) -> list[str]:
    return _LD_BLOCK.findall(html)


# ---------------------------------------------------------------------------
# Kryterium 791a6570: typ odpowiedzi i frontmatter
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMarkdownTwinFrontmatter:
    async def test_returns_markdown_content_type(self, client, db_session):
        env = await _env(db_session, "ct")
        _page, slug = await env.post("ct")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == MD_TYPE

    async def test_frontmatter_has_exactly_the_spec_keys_in_order(self, client, db_session):
        env = await _env(db_session, "keys")
        _page, slug = await env.post("keys")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert list(front) == FRONTMATTER_KEYS

    async def test_frontmatter_values_match_the_post(self, client, db_session):
        env = await _env(db_session, "vals")
        page, slug = await env.post(
            "vals",
            "Pierwsza linia tresci.",
            title="Tytul wartosci",
            description="Opis z pola wpisu.",
            tags=["alfa", "beta"],
            lang="en",
            author_name="Jan Kowalski",
            published_at=PUBLISHED,
            last_verified_at=date(2026, 2, 15),
            updated_at=UPDATED,
        )

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert front["title"] == "Tytul wartosci"
        assert front["description"] == "Opis z pola wpisu."
        assert front["url"] == f"{BASE}/blog/{slug}"
        assert front["lang"] == "en"
        assert front["author"] == "Jan Kowalski"
        assert front["published"] == "2026-03-01T12:00:00+00:00"
        assert front["modified"] == "2026-03-02T08:30:00+00:00"
        assert front["last_verified"] == "2026-02-15"
        assert front["tags"] == ["alfa", "beta"]
        assert page.slug != slug  # url bierze stabilny BlogPost.slug, nie slug strony wiki

    async def test_dates_are_strings_not_yaml_datetimes(self, client, db_session):
        env = await _env(db_session, "types")
        _page, slug = await env.post("types", published_at=PUBLISHED, last_verified_at=date(2026, 2, 15), updated_at=UPDATED)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert isinstance(front["published"], str)
        assert isinstance(front["modified"], str)
        assert isinstance(front["last_verified"], str)
        assert datetime.fromisoformat(front["published"]) == PUBLISHED

    async def test_default_author_when_not_set(self, client, db_session):
        env = await _env(db_session, "author")
        _page, slug = await env.post("author")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert front["author"] == DEFAULT_AUTHOR_NAME

    async def test_published_falls_back_to_page_created_at(self, client, db_session):
        env = await _env(db_session, "pubfb")
        created = datetime(2026, 1, 5, 9, 15, tzinfo=UTC)
        _page, slug = await env.post("pubfb", published_at=None, created_at=created)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert front["published"] == "2026-01-05T09:15:00+00:00"

    async def test_optional_fields_are_null_or_empty_when_missing(self, client, db_session):
        env = await _env(db_session, "empty")
        _page, slug = await env.post("empty")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert front["last_verified"] is None
        assert front["tags"] == []
        assert front["translations"] == []

    async def test_description_field_wins_over_content(self, client, db_session):
        env = await _env(db_session, "descf")
        _page, slug = await env.post("descf", "Linia z tresci nie jest opisem.", description="Opis z pola.")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert front["description"] == "Opis z pola."

    async def test_description_falls_back_to_first_content_line(self, client, db_session):
        env = await _env(db_session, "descfb")
        _page, slug = await env.post("descfb", "# Naglowek\n\nPierwsza linia opisu MON258.\n\nDruga linia.")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert front["description"] == "Pierwsza linia opisu MON258."

    async def test_description_fallback_skips_alert_marker(self, client, db_session):
        env = await _env(db_session, "descal")
        _page, slug = await env.post("descal", "> [!TLDR]\n> Streszczenie wpisu.\n\nReszta.")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert front["description"] == "> Streszczenie wpisu."

    @pytest.mark.parametrize(
        ("words", "expected_minutes"),
        [(0, 1), (1, 1), (200, 1), (201, 2), (400, 2), (401, 3)],
    )
    async def test_word_count_and_reading_time(self, words, expected_minutes, client, db_session):
        env = await _env(db_session, f"wc{words}")
        _page, slug = await env.post(f"wc{words}", " ".join(["slowo"] * words))

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert front["word_count"] == words
        assert front["reading_time_minutes"] == expected_minutes
        assert isinstance(front["word_count"], int)
        assert isinstance(front["reading_time_minutes"], int)

    async def test_word_count_is_computed_after_container_markers_are_removed(self, client, db_session):
        env = await _env(db_session, "wcc")
        _page, slug = await env.post("wcc", ":::foo\nalfa beta\n:::")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert front["word_count"] == 2

    async def test_translations_list_other_public_versions_only(self, client, db_session):
        env = await _env(db_session, "tr")
        root, root_slug = await env.post("trroot", lang="pl")
        _en_page, en_slug = await env.post("tren", lang="en", translation_of_id=root.id)
        _hidden_page, hidden_slug = await env.post("trhidden", public=False, lang="en", translation_of_id=root.id)

        with _minio(env.store):
            root_resp = await client.get(f"/blog/{root_slug}.md")
            en_resp = await client.get(f"/blog/{en_slug}.md")

        root_front, _body = _split(root_resp.text)
        en_front, _body = _split(en_resp.text)
        assert root_front["translations"] == [{"lang": "en", "url": f"{BASE}/blog/{en_slug}"}]
        assert en_front["translations"] == [{"lang": "pl", "url": f"{BASE}/blog/{root_slug}"}]
        assert hidden_slug not in root_resp.text
        assert hidden_slug not in en_resp.text


# ---------------------------------------------------------------------------
# Kryterium 6946c8d5: bezpieczna serializacja YAML
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFrontmatterYamlSafety:
    @pytest.mark.parametrize(
        "title",
        [
            'Say "hi": it\'s a #1 - test\nline two',
            "- wiodacy myslnik",
            "# zaczyna sie od hasha",
            "klucz: wartosc",
            "'pojedyncze cudzyslowy'",
            "konczy sie backslashem \\",
            "null",
            "true",
            "2026-01-01",
            "123",
        ],
    )
    async def test_title_round_trips_through_yaml(self, title, client, db_session):
        env = await _env(db_session, "yt")
        _page, slug = await env.post("yt", title=title)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        assert resp.status_code == 200
        front, _body = _split(resp.text)
        assert front["title"] == title
        assert list(front) == FRONTMATTER_KEYS

    async def test_description_and_author_round_trip_through_yaml(self, client, db_session):
        env = await _env(db_session, "yda")
        description = 'He said "x": y\nsecond line # not a comment'
        author = 'O"Neil: #1'
        _page, slug = await env.post("yda", description=description, author_name=author)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert front["description"] == description
        assert front["author"] == author

    async def test_tags_stay_a_list_of_strings(self, client, db_session):
        env = await _env(db_session, "ytag")
        _page, slug = await env.post("ytag", tags=["true", "123", "null"])

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert front["tags"] == ["true", "123", "null"]


# ---------------------------------------------------------------------------
# Kryterium 5c5d06fa: tresc .md bez HTML i bez :::, adresy bezwzgledne
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMarkdownTwinBody:
    async def test_container_markers_are_removed_but_inner_text_stays(self, client, db_session):
        env = await _env(db_session, "cont")
        _page, slug = await env.post("cont", 'Przed.\n\n:::faq title="Pytania"\nwnetrze kontenera\n:::\n\nPo.')

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        _front, body = _split(resp.text)
        assert not [line for line in body.splitlines() if line.startswith(":::")]
        assert "wnetrze kontenera" in body
        assert "Przed." in body
        assert "Po." in body

    async def test_body_is_markdown_without_server_added_html(self, client, db_session):
        env = await _env(db_session, "nohtml")
        content = "Akapit **pogrubiony** i `kod`.\n\n> [!NOTE]\n> Uwaga.\n\n![opis](/blog/x/attachments/a.png)"
        _page, slug = await env.post("nohtml", content)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        _front, body = _split(resp.text)
        for tag in ("<p>", "<div", "<figure", "<blockquote", "<strong", "<img", "<aside"):
            assert tag not in body
        assert "**pogrubiony**" in body
        assert "> [!NOTE]" in body

    async def test_relative_image_and_link_become_absolute(self, client, db_session):
        env = await _env(db_session, "abs")
        _page, slug = await env.post("abs", "![x](/blog/slug-a/attachments/a.png)\n\nZobacz [y](/blog/inny).")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        _front, body = _split(resp.text)
        assert f"![x]({BASE}/blog/slug-a/attachments/a.png)" in body
        assert f"[y]({BASE}/blog/inny)" in body
        assert f"{BASE}{BASE}" not in body

    async def test_other_targets_and_code_stay_untouched(self, client, db_session):
        env = await _env(db_session, "keep")
        content = (
            "[a](https://example.com/p)\n"
            "[m](mailto:ktos@example.com)\n"
            "[h](#kotwica)\n"
            "[c](//cdn.example.com/x.js)\n"
            "[r](wzgledny/sciezka)\n"
            "\n"
            "```\n"
            "[z](/w-kodzie)\n"
            "```\n"
            "\n"
            "Inline `[q](/inline)` koniec."
        )
        _page, slug = await env.post("keep", content)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        _front, body = _split(resp.text)
        lines = body.splitlines()
        for expected in (
            "[a](https://example.com/p)",
            "[m](mailto:ktos@example.com)",
            "[h](#kotwica)",
            "[c](//cdn.example.com/x.js)",
            "[r](wzgledny/sciezka)",
            "[z](/w-kodzie)",
        ):
            assert expected in lines
        assert "Inline `[q](/inline)` koniec." in lines
        assert f"{BASE}/w-kodzie" not in body
        assert f"{BASE}/inline" not in body


@pytest.mark.unit
class TestAbsolutizeMarkdownUrls:
    base = "https://example.com"

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("![x](/blog/s/attachments/a.png)", "![x](https://example.com/blog/s/attachments/a.png)"),
            ("[y](/blog/inny)", "[y](https://example.com/blog/inny)"),
            ("[home](/)", "[home](https://example.com/)"),
            ("[a](/x) i [b](/y)", "[a](https://example.com/x) i [b](https://example.com/y)"),
            ("`[a](/in)` oraz [b](/out)", "`[a](/in)` oraz [b](https://example.com/out)"),
        ],
    )
    def test_rewrites_root_relative_targets(self, source, expected):
        assert absolutize_markdown_urls(source, self.base) == expected

    @pytest.mark.parametrize(
        "source",
        [
            "[a](https://example.org/p)",
            "[a](http://example.org/p)",
            "[m](mailto:a@example.org)",
            "[h](#kotwica)",
            "[c](//cdn.example.org/x.js)",
            "[r](wzgledny/sciezka)",
            "tekst bez linkow",
            "",
        ],
    )
    def test_leaves_other_targets_unchanged(self, source):
        assert absolutize_markdown_urls(source, self.base) == source

    @pytest.mark.parametrize("marker", ["```", "~~~"])
    def test_fenced_code_is_untouched_and_text_after_it_is_rewritten(self, marker):
        source = f"[przed](/a)\n{marker}\n[kod](/b)\n{marker}\n[po](/c)"

        result = absolutize_markdown_urls(source, self.base)

        assert result == f"[przed](https://example.com/a)\n{marker}\n[kod](/b)\n{marker}\n[po](https://example.com/c)"

    def test_trailing_slash_in_base_url_does_not_double(self):
        assert absolutize_markdown_urls("[a](/x)", "https://example.com/") == "[a](https://example.com/x)"


# ---------------------------------------------------------------------------
# Kryterium ed855b7e: 404 dla prywatnych, nieistniejacych i bledu MinIO; stary slug -> 301
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMarkdownTwinNotFound:
    async def test_private_post_returns_404(self, client, db_session):
        env = await _env(db_session, "priv")
        _page, slug = await env.post("priv", "TAJNA-TRESC-MON258", title="Tajny tytul MON258", public=False)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        assert resp.status_code == 404
        assert "TAJNA-TRESC-MON258" not in resp.text
        assert "Tajny tytul MON258" not in resp.text

    async def test_private_page_without_post_row_returns_404(self, client, db_session):
        env = await _env(db_session, "nopost")
        page = await _create_wiki_page(db_session, env.project, env.user, f"Bez wpisu {_uid()}", is_public=False)

        resp = await client.get(f"/blog/{page.slug}.md")

        assert resp.status_code == 404

    async def test_nonexistent_slug_returns_404(self, client, db_session):
        resp = await client.get(f"/blog/nie-ma-takiego-wpisu-{_uid()}.md")

        assert resp.status_code == 404

    async def test_minio_error_returns_404_not_500(self, client, db_session):
        env = await _env(db_session, "minioerr")
        page, slug = await env.post("minioerr")
        env.store[page.minio_path] = RuntimeError("MinIO niedostepne")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        assert resp.status_code == 404

    async def test_double_md_suffix_returns_404(self, client, db_session):
        env = await _env(db_session, "dbl")
        _page, slug = await env.post("dbl")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md.md")

        assert resp.status_code == 404

    async def test_old_slug_redirects_301_to_new_md_address(self, client, db_session):
        env = await _env(db_session, "redir")
        page, old_slug = await env.post("redir")
        new_slug = f"nowy-adres-md-{_uid()}"
        await blog_service.set_post_meta(db_session, env.user, env.project, page.id, slug=new_slug)

        resp = await client.get(f"/blog/{old_slug}.md")

        assert resp.status_code == 301
        assert urlsplit(resp.headers["location"]).path == f"/blog/{new_slug}.md"

    async def test_old_slug_of_unpublished_post_returns_404_not_redirect(self, client, db_session):
        env = await _env(db_session, "redirpriv")
        page, old_slug = await env.post("redirpriv")
        await blog_service.set_post_meta(db_session, env.user, env.project, page.id, slug=f"nowy-adres-priv-{_uid()}")
        page.is_public = False
        await db_session.flush()

        resp = await client.get(f"/blog/{old_slug}.md")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Kryterium 99b2ae41: negocjacja tresci /blog/{slug}, Vary, Link, <link rel="alternate">
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestContentNegotiation:
    async def test_accept_markdown_returns_the_twin_with_vary(self, client, db_session):
        env = await _env(db_session, "neg")
        _page, slug = await env.post("neg", "Tresc negocjacji MON258.", title="Tytul negocjacji")

        with _minio(env.store):
            twin = await client.get(f"/blog/{slug}.md")
            resp = await client.get(f"/blog/{slug}", headers={"Accept": "text/markdown"})

        assert resp.status_code == 200
        assert resp.headers["content-type"] == MD_TYPE
        assert "accept" in _vary(resp)
        assert resp.text == twin.text
        front, _body = _split(resp.text)
        assert front["title"] == "Tytul negocjacji"

    async def test_markdown_in_accept_list_triggers_the_twin(self, client, db_session):
        env = await _env(db_session, "neglist")
        _page, slug = await env.post("neglist")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}", headers={"Accept": "application/json, text/markdown;q=0.9"})

        assert resp.headers["content-type"] == MD_TYPE

    @pytest.mark.parametrize("accept", [None, "*/*", "text/html,*/*", "text/*", "text/html"])
    async def test_without_markdown_accept_returns_html_with_link_header(self, accept, client, db_session):
        env = await _env(db_session, "html")
        _page, slug = await env.post("html", "Tresc html MON258.")
        headers = {"Accept": accept} if accept is not None else {}

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}", headers=headers)

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert resp.headers["link"] == f'<{BASE}/blog/{slug}.md>; rel="alternate"; type="text/markdown"'
        assert "accept" in _vary(resp)
        assert f'<link rel="alternate" type="text/markdown" href="{BASE}/blog/{slug}.md">' in resp.text

    async def test_not_found_html_carries_no_link_header(self, client, db_session):
        resp = await client.get(f"/blog/nie-ma-wpisu-{_uid()}")

        assert resp.status_code == 404
        assert "link" not in resp.headers

    async def test_private_post_with_accept_markdown_returns_404(self, client, db_session):
        env = await _env(db_session, "negpriv")
        _page, slug = await env.post("negpriv", "TAJNA-TRESC-NEG-MON258", public=False)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}", headers={"Accept": "text/markdown"})

        assert resp.status_code == 404
        assert "TAJNA-TRESC-NEG-MON258" not in resp.text

    async def test_old_slug_with_accept_markdown_redirects_with_vary(self, client, db_session):
        env = await _env(db_session, "negredir")
        page, old_slug = await env.post("negredir")
        new_slug = f"nowy-adres-neg-{_uid()}"
        await blog_service.set_post_meta(db_session, env.user, env.project, page.id, slug=new_slug)

        resp = await client.get(f"/blog/{old_slug}", headers={"Accept": "text/markdown"})

        assert resp.status_code == 301
        assert urlsplit(resp.headers["location"]).path == f"/blog/{new_slug}"
        assert "accept" in _vary(resp)


# ---------------------------------------------------------------------------
# Kryterium 59dfcb1e: JSON-LD
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestJsonLd:
    async def test_post_page_has_blogposting_and_breadcrumb(self, client, db_session):
        env = await _env(db_session, "ld")
        _page, slug = await env.post(
            "ld",
            "Tresc JSON-LD.",
            title="Tytul JSON-LD",
            description="Opis JSON-LD.",
            tags=["alfa", "beta"],
            lang="en",
            author_name="Jan Kowalski",
            published_at=PUBLISHED,
            updated_at=UPDATED,
        )

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}")

        blocks = _ld_blocks(resp.text)
        assert len(blocks) == 1
        data = json.loads(blocks[0])
        assert data["@context"] == "https://schema.org"
        graph = {node["@type"]: node for node in data["@graph"]}
        posting = graph["BlogPosting"]
        assert posting["headline"] == "Tytul JSON-LD"
        assert posting["description"] == "Opis JSON-LD."
        assert posting["datePublished"] == "2026-03-01T12:00:00+00:00"
        assert posting["dateModified"] == "2026-03-02T08:30:00+00:00"
        assert posting["author"] == {"@type": "Person", "name": "Jan Kowalski"}
        assert posting["inLanguage"] == "en"
        assert posting["keywords"] == ["alfa", "beta"]
        assert posting["mainEntityOfPage"] == {"@type": "WebPage", "@id": f"{BASE}/blog/{slug}"}
        crumbs = graph["BreadcrumbList"]["itemListElement"]
        assert [c["name"] for c in crumbs] == ["Blog", "Tytul JSON-LD"]
        assert [c["item"] for c in crumbs] == [f"{BASE}/blog/", f"{BASE}/blog/{slug}"]

    async def test_title_with_closing_script_tag_does_not_break_out_of_the_block(self, client, db_session):
        env = await _env(db_session, "ldxss")
        title = "Atak </script><script>alert(1)</script> koniec"
        _page, slug = await env.post("ldxss", title=title)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}")

        assert resp.status_code == 200
        assert "<script>alert(1)</script>" not in resp.text
        blocks = _ld_blocks(resp.text)
        assert len(blocks) == 1
        assert "</" not in blocks[0]
        posting = next(n for n in json.loads(blocks[0])["@graph"] if n["@type"] == "BlogPosting")
        assert posting["headline"] == title

    async def test_index_page_has_blog_jsonld_with_public_posts_only_newest_first(self, client, db_session):
        env = await _env(db_session, "ldidx")
        _old, old_slug = await env.post("ldold", description="Stary.", published_at=PUBLISHED - timedelta(days=10))
        _new, new_slug = await env.post("ldnew", description="Nowy.", published_at=PUBLISHED)
        _hidden, hidden_slug = await env.post("ldhid", description="Ukryty.", public=False)

        with _minio(env.store):
            resp = await client.get("/blog/")

        blocks = _ld_blocks(resp.text)
        assert len(blocks) == 1
        data = json.loads(blocks[0])
        assert data["@context"] == "https://schema.org"
        assert data["@type"] == "Blog"
        assert data["url"] == f"{BASE}/blog/"
        urls = [p["url"] for p in data["blogPost"]]
        assert hidden_slug not in " ".join(urls)
        assert urls.index(f"{BASE}/blog/{new_slug}") < urls.index(f"{BASE}/blog/{old_slug}")
        assert all(p["@type"] == "BlogPosting" for p in data["blogPost"])


# ---------------------------------------------------------------------------
# Kryterium 19baa9c7: ETag, Last-Modified, 304
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestConditionalRequests:
    async def _etag(self, client, url, **kwargs):
        resp = await client.get(url, **kwargs)
        assert resp.status_code == 200
        return resp.headers["etag"], resp

    async def test_first_response_carries_etag_and_last_modified(self, client, db_session):
        env = await _env(db_session, "etag1")
        _page, slug = await env.post("etag1", updated_at=UPDATED)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        assert re.fullmatch(r'"[0-9a-f]{32}"', resp.headers["etag"])
        assert resp.headers["last-modified"] == format_datetime(UPDATED, usegmt=True)

    async def test_same_content_gives_the_same_etag(self, client, db_session):
        env = await _env(db_session, "etagst")
        _page, slug = await env.post("etagst")

        with _minio(env.store):
            first, _resp = await self._etag(client, f"/blog/{slug}.md")
            second, _resp = await self._etag(client, f"/blog/{slug}.md")

        assert first == second

    async def test_matching_if_none_match_returns_304_with_empty_body(self, client, db_session):
        env = await _env(db_session, "etag304")
        _page, slug = await env.post("etag304", updated_at=UPDATED)

        with _minio(env.store):
            etag, first = await self._etag(client, f"/blog/{slug}.md")
            resp = await client.get(f"/blog/{slug}.md", headers={"If-None-Match": etag})

        assert resp.status_code == 304
        assert resp.content == b""
        assert resp.headers["etag"] == etag
        assert resp.headers["last-modified"] == first.headers["last-modified"]

    @pytest.mark.parametrize(
        "template",
        ["W/{etag}", '"inny-etag", {etag}', '{etag}, "inny-etag"', "*"],
    )
    async def test_if_none_match_variants_return_304(self, template, client, db_session):
        env = await _env(db_session, "etagvar")
        _page, slug = await env.post("etagvar")

        with _minio(env.store):
            etag, _first = await self._etag(client, f"/blog/{slug}.md")
            resp = await client.get(f"/blog/{slug}.md", headers={"If-None-Match": template.format(etag=etag)})

        assert resp.status_code == 304
        assert resp.content == b""

    @pytest.mark.parametrize("value", ['"inny-etag"', 'W/"inny-etag"', "nie-etag"])
    async def test_different_if_none_match_returns_full_200(self, value, client, db_session):
        env = await _env(db_session, "etagdiff")
        _page, slug = await env.post("etagdiff", "Tresc pelna MON258.")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md", headers={"If-None-Match": value})

        assert resp.status_code == 200
        assert "Tresc pelna MON258." in resp.text

    async def test_etag_changes_after_content_edit(self, client, db_session):
        env = await _env(db_session, "etagedit")
        page, slug = await env.post("etagedit", "Tresc przed edycja.")

        with _minio(env.store):
            old_etag, _first = await self._etag(client, f"/blog/{slug}.md")
            env.store[page.minio_path] = "Tresc po edycji."
            resp = await client.get(f"/blog/{slug}.md", headers={"If-None-Match": old_etag})

        assert resp.status_code == 200
        assert "Tresc po edycji." in resp.text
        assert resp.headers["etag"] != old_etag

    async def test_etag_changes_when_page_updated_at_changes(self, client, db_session):
        env = await _env(db_session, "etagupd")
        page, slug = await env.post("etagupd", updated_at=UPDATED)

        with _minio(env.store):
            old_etag, _first = await self._etag(client, f"/blog/{slug}.md")
            page.updated_at = UPDATED + timedelta(hours=1)
            await db_session.flush()
            resp = await client.get(f"/blog/{slug}.md", headers={"If-None-Match": old_etag})

        assert resp.status_code == 200
        assert resp.headers["etag"] != old_etag
        assert resp.headers["last-modified"] == format_datetime(UPDATED + timedelta(hours=1), usegmt=True)

    async def test_accept_markdown_on_html_route_shares_etag_and_supports_304(self, client, db_session):
        env = await _env(db_session, "etagneg")
        _page, slug = await env.post("etagneg")

        with _minio(env.store):
            twin_etag, _twin = await self._etag(client, f"/blog/{slug}.md")
            etag, _first = await self._etag(client, f"/blog/{slug}", headers={"Accept": "text/markdown"})
            resp = await client.get(f"/blog/{slug}", headers={"Accept": "text/markdown", "If-None-Match": etag})

        assert etag == twin_etag
        assert resp.status_code == 304
        assert resp.content == b""
        assert "accept" in _vary(resp)

    async def test_index_md_supports_304(self, client, db_session):
        env = await _env(db_session, "etagidx")
        _page, slug = await env.post("etagidx", tags=[f"t-{_uid()}"])

        with _minio(env.store):
            post_etag, _post = await self._etag(client, f"/blog/{slug}.md")
            etag, _first = await self._etag(client, "/blog/index.md")
            resp = await client.get("/blog/index.md", headers={"If-None-Match": etag})

        assert resp.status_code == 304
        assert resp.content == b""
        assert resp.headers["etag"] == etag
        assert etag != post_etag


@pytest.mark.unit
class TestConditionalResponse:
    last_modified = datetime(2026, 3, 2, 8, 30, tzinfo=UTC)

    @staticmethod
    def _request(if_none_match=None):
        headers = [(b"if-none-match", if_none_match.encode())] if if_none_match is not None else []
        return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": headers})

    def _expected_etag(self, body):
        digest = hashlib.sha256((self.last_modified.isoformat() + "\n" + body).encode()).hexdigest()
        return f'"{digest[:32]}"'

    def test_builds_200_with_etag_last_modified_media_type_and_headers(self):
        resp = conditional_response(self._request(), "tresc", last_modified=self.last_modified, headers={"Vary": "Accept"})

        assert resp.status_code == 200
        assert resp.body == b"tresc"
        assert resp.headers["etag"] == self._expected_etag("tresc")
        assert resp.headers["last-modified"] == "Mon, 02 Mar 2026 08:30:00 GMT"
        assert resp.headers["content-type"] == MD_TYPE
        assert resp.headers["vary"] == "Accept"

    def test_custom_media_type_is_used(self):
        resp = conditional_response(self._request(), "x", last_modified=self.last_modified, media_type="text/plain; charset=utf-8")

        assert resp.headers["content-type"] == "text/plain; charset=utf-8"

    def test_etag_depends_on_body_and_on_last_modified(self):
        base = conditional_response(self._request(), "a", last_modified=self.last_modified)
        other_body = conditional_response(self._request(), "b", last_modified=self.last_modified)
        other_time = conditional_response(self._request(), "a", last_modified=self.last_modified + timedelta(seconds=1))

        assert len({base.headers["etag"], other_body.headers["etag"], other_time.headers["etag"]}) == 3

    @pytest.mark.parametrize("template", ["{etag}", "W/{etag}", '"x", {etag}', '{etag} , "x"', "*"])
    def test_matching_header_returns_304_with_empty_body_and_headers(self, template):
        etag = self._expected_etag("tresc")

        resp = conditional_response(self._request(template.format(etag=etag)), "tresc", last_modified=self.last_modified, headers={"Vary": "Accept"})

        assert resp.status_code == 304
        assert resp.body == b""
        assert resp.headers["etag"] == etag
        assert resp.headers["last-modified"] == "Mon, 02 Mar 2026 08:30:00 GMT"
        assert resp.headers["vary"] == "Accept"

    @pytest.mark.parametrize("value", ['"x"', "", 'W/"x"', "abc"])
    def test_non_matching_header_returns_200(self, value):
        resp = conditional_response(self._request(value), "tresc", last_modified=self.last_modified)

        assert resp.status_code == 200
        assert resp.body == b"tresc"


# ---------------------------------------------------------------------------
# Kryterium cbb0b4a1: /blog/index.md
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIndexMarkdown:
    async def test_content_type_and_heading_not_html_not_404(self, client, db_session):
        env = await _env(db_session, "idx")
        _page, _slug = await env.post("idx")

        with _minio(env.store):
            resp = await client.get("/blog/index.md")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == MD_TYPE
        assert resp.text.startswith("# Blog")
        assert "<html" not in resp.text.lower()

    async def test_lists_public_posts_with_md_links_date_description_and_tags(self, client, db_session):
        env = await _env(db_session, "idxline")
        _p1, slug1 = await env.post(
            "idxfull",
            title="Pelny wpis",
            description="Opis pelnego wpisu.",
            tags=["alfa", "beta"],
            published_at=PUBLISHED,
        )
        _p2, slug2 = await env.post("idxbare", title="Wpis bez tagow", description="Tylko opis.", published_at=PUBLISHED - timedelta(days=1))
        _p3, slug3 = await env.post(
            "idxnodesc", "Tresc-ktora-nie-moze-trafic-do-indeksu", title="Wpis bez opisu", published_at=PUBLISHED - timedelta(days=2)
        )

        with _minio(env.store):
            resp = await client.get("/blog/index.md")

        lines = resp.text.splitlines()
        assert f"- [Pelny wpis]({BASE}/blog/{slug1}.md) - 2026-03-01 - Opis pelnego wpisu. (tagi: alfa, beta)" in lines
        assert f"- [Wpis bez tagow]({BASE}/blog/{slug2}.md) - 2026-02-28 - Tylko opis." in lines
        assert f"- [Wpis bez opisu]({BASE}/blog/{slug3}.md) - 2026-02-27" in lines
        assert "Tresc-ktora-nie-moze-trafic-do-indeksu" not in resp.text

    async def test_private_post_is_absent(self, client, db_session):
        env = await _env(db_session, "idxpriv")
        _pub, pub_slug = await env.post("idxpub", title="Publiczny wpis indeksu")
        _priv, priv_slug = await env.post("idxprv", title="Prywatny wpis indeksu", public=False)

        with _minio(env.store):
            resp = await client.get("/blog/index.md")

        assert pub_slug in resp.text
        assert priv_slug not in resp.text
        assert "Prywatny wpis indeksu" not in resp.text

    async def test_newest_post_comes_first(self, client, db_session):
        env = await _env(db_session, "idxord")
        _old, old_slug = await env.post("idxold", published_at=PUBLISHED - timedelta(days=30))
        _new, new_slug = await env.post("idxnew", published_at=PUBLISHED)

        with _minio(env.store):
            resp = await client.get("/blog/index.md")

        assert resp.text.index(f"/blog/{new_slug}.md") < resp.text.index(f"/blog/{old_slug}.md")

    async def test_title_whitespace_is_collapsed_and_brackets_escaped(self, client, db_session):
        env = await _env(db_session, "idxesc")
        _page, slug = await env.post("idxesc", title="A [b]   c")

        with _minio(env.store):
            resp = await client.get("/blog/index.md")

        assert f"- [A \\[b\\] c]({BASE}/blog/{slug}.md) - " in resp.text

    async def test_lang_filter_narrows_the_list(self, client, db_session):
        env = await _env(db_session, "idxlang")
        _pl, pl_slug = await env.post("idxpl", lang="pl")
        _en, en_slug = await env.post("idxen", lang="en")

        with _minio(env.store):
            pl_resp = await client.get("/blog/index.md?lang=pl")
            en_resp = await client.get("/blog/index.md?lang=en")

        assert pl_slug in pl_resp.text
        assert en_slug not in pl_resp.text
        assert en_slug in en_resp.text
        assert pl_slug not in en_resp.text

    async def test_tag_filter_narrows_the_list(self, client, db_session):
        env = await _env(db_session, "idxtag")
        tag = f"tag-{_uid()}"
        _hit, hit_slug = await env.post("idxhit", tags=[tag, "inny"])
        _miss, miss_slug = await env.post("idxmiss", tags=["inny"])
        _none, none_slug = await env.post("idxnone")

        with _minio(env.store):
            resp = await client.get(f"/blog/index.md?tag={tag}")

        assert hit_slug in resp.text
        assert miss_slug not in resp.text
        assert none_slug not in resp.text

    async def test_empty_list_says_no_posts_and_uses_epoch_last_modified(self, client, db_session):
        resp = await client.get(f"/blog/index.md?tag=brak-takiego-tagu-{_uid()}")

        assert resp.status_code == 200
        assert resp.text.startswith("# Blog")
        assert "Brak wpisów." in resp.text
        assert "- [" not in resp.text
        assert resp.headers["last-modified"] == "Thu, 01 Jan 1970 00:00:00 GMT"

    async def test_last_modified_is_the_max_updated_at_of_the_listed_posts(self, client, db_session):
        env = await _env(db_session, "idxlm")
        tag = f"lm-{_uid()}"
        newest = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
        # Kolejnosc wstawiania, kolejnosc listy (published_at malejaco) i kolejnosc updated_at sa rozne:
        # lista to x, z, y; najnowszy updated_at ma srodkowy z, wiec ani pierwszy, ani ostatni, ani min nie przejda.
        await env.post("idxlmy", tags=[tag], published_at=PUBLISHED - timedelta(days=2), updated_at=datetime(2026, 3, 5, 9, 0, tzinfo=UTC))
        await env.post("idxlmz", tags=[tag], published_at=PUBLISHED - timedelta(days=1), updated_at=newest)
        await env.post("idxlmx", tags=[tag], published_at=PUBLISHED, updated_at=datetime(2026, 3, 4, 10, 0, tzinfo=UTC))

        with _minio(env.store):
            resp = await client.get(f"/blog/index.md?tag={tag}")

        assert resp.headers["last-modified"] == format_datetime(newest, usegmt=True)

    async def test_title_backslashes_are_escaped_in_link_text(self, client, db_session):
        env = await _env(db_session, "idxbs")
        _p1, slug1 = await env.post("idxbs1", title=r"Back\slash")
        _p2, slug2 = await env.post("idxbs2", title="Konczy\\")
        _p3, slug3 = await env.post("idxbs3", title=r"x\[y")

        with _minio(env.store):
            resp = await client.get("/blog/index.md")

        lines = resp.text.splitlines()
        assert any(line.startswith(rf"- [Back\\slash]({BASE}/blog/{slug1}.md) - ") for line in lines)
        assert any(line.startswith(rf"- [Konczy\\]({BASE}/blog/{slug2}.md) - ") for line in lines)
        assert any(line.startswith(rf"- [x\\\[y]({BASE}/blog/{slug3}.md) - ") for line in lines)

    async def test_multiline_description_is_collapsed_to_one_line(self, client, db_session):
        env = await _env(db_session, "idxdesc")
        _page, slug = await env.post("idxdesc", title="Opis wielu linii", description="Linia1\nLinia2   z   spacjami\t i tab", published_at=PUBLISHED)

        with _minio(env.store):
            resp = await client.get("/blog/index.md")

        assert f"- [Opis wielu linii]({BASE}/blog/{slug}.md) - 2026-03-01 - Linia1 Linia2 z spacjami i tab" in resp.text.splitlines()

    @pytest.mark.parametrize(
        "query", [{"lang": "xx"}, {"lang": "\x00"}, {"tag": "\x00"}, {"tag": "Zly Tag"}, {"tag": "Abc"}, {"tag": "t" * 51}, {"tag": "tag\n"}]
    )
    async def test_invalid_filter_values_give_empty_list_without_querying(self, query, client, db_session):
        env = await _env(db_session, "idxbad")
        # CHECK constraint dopuszcza w DB tylko pl/en, wiec nieprawidlowego jezyka nie da sie zaseedowac:
        # dowodem "bez zapytania" jest spy. Tagi (JSONB bez constraintu) seedujemy, zeby bez walidacji
        # kształtu trafily na liste.
        _pl, pl_slug = await env.post("idxbadpl", lang="pl", tags=["Zly Tag", "Abc", "t" * 51, "tag\n"])
        _en, en_slug = await env.post("idxbaden", lang="en")
        spy = AsyncMock(side_effect=real_list_public_posts)

        with _minio(env.store), patch("monolynx.blog_ai.list_public_posts", spy):
            resp = await client.get("/blog/index.md", params=query)

        assert resp.status_code == 200
        assert resp.text.startswith("# Blog")
        assert "Brak wpisów." in resp.text
        assert pl_slug not in resp.text
        assert en_slug not in resp.text
        spy.assert_not_awaited()

    async def test_valid_lang_filter_queries_with_that_lang(self, client, db_session):
        env = await _env(db_session, "idxgoodlang")
        _pl, pl_slug = await env.post("idxgoodpl", lang="pl")
        _en, en_slug = await env.post("idxgooden", lang="en")
        spy = AsyncMock(side_effect=real_list_public_posts)

        with _minio(env.store), patch("monolynx.blog_ai.list_public_posts", spy):
            resp = await client.get("/blog/index.md", params={"lang": "pl"})

        spy.assert_awaited_once()
        assert spy.await_args.kwargs["lang"] == "pl"
        assert spy.await_args.kwargs["tag"] is None
        assert pl_slug in resp.text
        assert en_slug not in resp.text

    async def test_valid_tag_filter_queries_with_that_tag(self, client, db_session):
        env = await _env(db_session, "idxgoodtag")
        tag = f"dobry-{_uid()}"
        _hit, hit_slug = await env.post("idxgoodhit", tags=[tag])
        spy = AsyncMock(side_effect=real_list_public_posts)

        with _minio(env.store), patch("monolynx.blog_ai.list_public_posts", spy):
            resp = await client.get("/blog/index.md", params={"tag": tag})

        spy.assert_awaited_once()
        assert spy.await_args.kwargs["tag"] == tag
        assert spy.await_args.kwargs["lang"] is None
        assert hit_slug in resp.text

    async def test_tag_of_max_length_is_a_valid_filter(self, client, db_session):
        env = await _env(db_session, "idxlen")
        tag = "t" * 50
        _page, slug = await env.post("idxlen", tags=[tag])

        with _minio(env.store):
            resp = await client.get("/blog/index.md", params={"tag": tag})

        assert slug in resp.text

    async def test_empty_filter_values_mean_no_filter(self, client, db_session):
        env = await _env(db_session, "idxempty")
        _page, slug = await env.post("idxempty", lang="en", tags=["jakis-tag"])

        with _minio(env.store):
            both = await client.get("/blog/index.md?lang=&tag=")
            lang_only = await client.get("/blog/index.md?lang=")
            tag_only = await client.get("/blog/index.md?tag=")

        for resp in (both, lang_only, tag_only):
            assert resp.status_code == 200
            assert slug in resp.text


@pytest.mark.integration
class TestMarkdownTwinRedirectQuery:
    async def test_old_slug_redirect_keeps_the_query_string(self, client, db_session):
        env = await _env(db_session, "redirq")
        page, old_slug = await env.post("redirq")
        new_slug = f"nowy-adres-q-{_uid()}"
        await blog_service.set_post_meta(db_session, env.user, env.project, page.id, slug=new_slug)

        resp = await client.get(f"/blog/{old_slug}.md?lang=pl&x=1")

        assert resp.status_code == 301
        assert resp.headers["location"].endswith(f"/blog/{new_slug}.md?lang=pl&x=1")


@pytest.mark.integration
class TestAcceptQuality:
    async def test_q_zero_returns_html(self, client, db_session):
        env = await _env(db_session, "q0")
        _page, slug = await env.post("q0")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}", headers={"Accept": "text/markdown;q=0"})

        assert resp.headers["content-type"].startswith("text/html")

    async def test_unparsable_q_returns_html(self, client, db_session):
        env = await _env(db_session, "qabc")
        _page, slug = await env.post("qabc")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}", headers={"Accept": "text/markdown;q=abc"})

        assert resp.headers["content-type"].startswith("text/html")

    async def test_media_type_is_case_insensitive(self, client, db_session):
        env = await _env(db_session, "qcase")
        _page, slug = await env.post("qcase")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}", headers={"Accept": "Text/Markdown"})

        assert resp.headers["content-type"] == MD_TYPE


@pytest.mark.unit
class TestWantsMarkdown:
    @pytest.mark.parametrize(
        "accept",
        [
            "text/markdown",
            "Text/Markdown",
            "TEXT/MARKDOWN",
            "text/markdown;q=1.0",
            "text/markdown; q=0.5",
            "text/markdown;q=0.001",
            "text/markdown;charset=utf-8",
            "text/html, text/markdown",
            "text/markdown;q=0, text/markdown",
        ],
    )
    def test_true_when_markdown_is_listed_with_positive_quality(self, accept):
        assert wants_markdown(accept) is True

    @pytest.mark.parametrize(
        "accept",
        [
            None,
            "",
            "*/*",
            "text/*",
            "text/html",
            "text/markdownx",
            "xtext/markdown",
            "text/markdown;q=0",
            "text/markdown;Q=0",
            "text/markdown; q=0.0",
            "text/markdown;q=abc",
            "text/markdown;q=",
            "text/html, text/markdown;q=0",
        ],
    )
    def test_false_otherwise(self, accept):
        assert wants_markdown(accept) is False


@pytest.mark.unit
class TestAbsolutizeCodeBoundaries:
    base = "https://example.com"

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                "~~~\n```\n[a](/x)\n```\n~~~\n[b](/y)",
                "~~~\n```\n[a](/x)\n```\n~~~\n[b](https://example.com/y)",
            ),
            (
                "```\n~~~\n[a](/x)\n~~~\n```\n[b](/y)",
                "```\n~~~\n[a](/x)\n~~~\n```\n[b](https://example.com/y)",
            ),
            (
                "````\n```\n[a](/x)\n```\n````\n[b](/y)",
                "````\n```\n[a](/x)\n```\n````\n[b](https://example.com/y)",
            ),
            (
                "```python\n[a](/x)\n```\n[b](/y)",
                "```python\n[a](/x)\n```\n[b](https://example.com/y)",
            ),
            (
                "```\n```python\n[a](/x)\n```\n[b](/y)",
                "```\n```python\n[a](/x)\n```\n[b](https://example.com/y)",
            ),
            (
                "   ```\n[a](/x)\n   ```\n[b](/y)",
                "   ```\n[a](/x)\n   ```\n[b](https://example.com/y)",
            ),
            (
                "```\n[a](/x)\n```  \n[b](/y)",
                "```\n[a](/x)\n```  \n[b](https://example.com/y)",
            ),
            (
                "```\n[a](/x)\n`````\n[b](/y)",
                "```\n[a](/x)\n`````\n[b](https://example.com/y)",
            ),
            (
                "``[a](/x)`` i [b](/y)",
                "``[a](/x)`` i [b](https://example.com/y)",
            ),
            (
                "`` a ` b [c](/x) `` [d](/y)",
                "`` a ` b [c](/x) `` [d](https://example.com/y)",
            ),
        ],
    )
    def test_code_stays_and_text_after_it_is_rewritten(self, source, expected):
        assert absolutize_markdown_urls(source, self.base) == expected

    def test_unclosed_fence_keeps_the_rest_of_the_document_untouched(self):
        source = "[a](/x)\n```\n[b](/y)\n[c](/z)"

        assert absolutize_markdown_urls(source, self.base) == "[a](https://example.com/x)\n```\n[b](/y)\n[c](/z)"


@pytest.mark.integration
class TestFrontmatterControlCharacters:
    @pytest.mark.parametrize("codepoint", [0x85, 0x2028, 0x2029, 0x7F, 0x90, 0x9F])
    async def test_title_with_line_or_control_character_round_trips(self, codepoint, client, db_session):
        env = await _env(db_session, f"ctl{codepoint:x}")
        title = f"przed{chr(codepoint)}po"
        _page, slug = await env.post("ctl", title=title)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        assert resp.status_code == 200
        front, _body = _split(resp.text)
        assert front["title"] == title
        assert list(front) == FRONTMATTER_KEYS


@pytest.mark.integration
class TestJsonLdWordCountAndImage:
    async def test_blogposting_carries_exact_word_count_after_marker_removal(self, client, db_session):
        env = await _env(db_session, "ldwc")
        _page, slug = await env.post("ldwc", "Jeden dwa trzy.\n\n:::foo\nczte ry\n:::")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}")

        posting = next(n for n in json.loads(_ld_blocks(resp.text)[0])["@graph"] if n["@type"] == "BlogPosting")
        assert posting["wordCount"] == 5
        assert "image" not in posting

    async def test_blogposting_image_is_the_cover_attachment_url(self, client, db_session):
        env = await _env(db_session, "ldimg")
        page, slug = await env.post("ldimg")
        attachment = await _create_attachment(db_session, page, filename="okladka wpisu.png", mime_type="image/png")
        await blog_service.set_post_meta(db_session, env.user, env.project, page.id, cover_attachment_id=attachment.id)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}")

        posting = next(n for n in json.loads(_ld_blocks(resp.text)[0])["@graph"] if n["@type"] == "BlogPosting")
        assert posting["image"] == f"{BASE}/blog/{slug}/attachments/{attachment.id}/{quote('okladka wpisu.png')}"


# ---------------------------------------------------------------------------
# MON-259: feedy Atom / RSS / JSON Feed (kryteria 1, 2, 3)
# ---------------------------------------------------------------------------

# Daty z 2099, zeby wpisy testu byly zawsze najnowsze na listach globalnych (feed bierze N najnowszych z calej bazy).
_FUTURE = datetime(2099, 1, 1, 12, 0, tzinfo=UTC)
_ATOM = "{http://www.w3.org/2005/Atom}"
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
_FEEDS = {
    "atom": ("/blog/feed.xml", "application/atom+xml; charset=utf-8"),
    "rss": ("/blog/rss.xml", "application/rss+xml; charset=utf-8"),
    "json": ("/blog/feed.json", "application/feed+json; charset=utf-8"),
}
_ALL_KINDS = pytest.mark.parametrize("kind", ["atom", "rss", "json"])
_EPOCH_HEADER = "Thu, 01 Jan 1970 00:00:00 GMT"
_END_MARKER = "KONIEC-WPISU-MON259"


def _at(days: int) -> datetime:
    return _FUTURE + timedelta(days=days)


def _long_content(end_marker: str = _END_MARKER) -> str:
    """Dluga tresc z **pogrubieniem**; marker na koncu nie moze trafic do zajawki."""
    return "Akapit tresci wpisu. " * 150 + f"\n\nTo jest **pogrubiony** fragment.\n\n{end_marker}"


def _parse_feed(kind: str, body: bytes) -> list[dict]:
    """Feed -> lista wpisow w kolejnosci dokumentu: {url, title, content, summary} (ten sam ksztalt dla 3 formatow)."""
    if kind == "atom":
        root = ET.fromstring(body)
        return [
            {
                "url": e.findtext(f"{_ATOM}id"),
                "title": e.findtext(f"{_ATOM}title"),
                "content": e.findtext(f"{_ATOM}content"),
                "summary": e.findtext(f"{_ATOM}summary"),
            }
            for e in root.findall(f"{_ATOM}entry")
        ]
    if kind == "rss":
        root = ET.fromstring(body)
        return [
            {
                "url": i.findtext("guid"),
                "title": i.findtext("title"),
                "content": i.findtext(f"{_CONTENT_NS}encoded"),
                "summary": i.findtext("description"),
            }
            for i in root.findall("channel/item")
        ]
    data = json.loads(body)
    return [{"url": i["id"], "title": i["title"], "content": i.get("content_text"), "summary": i.get("summary")} for i in data["items"]]


async def _get_feed(client, env, kind: str, query: str = ""):
    with _minio(env.store):
        return await client.get(_FEEDS[kind][0] + query)


def _url(slug: str) -> str:
    return f"{BASE}/blog/{slug}"


@pytest.mark.integration
class TestFeedsCommon:
    @_ALL_KINDS
    async def test_returns_200_with_the_feed_media_type(self, kind, client, db_session):
        env = await _env(db_session, "fct")
        await env.post("fct", published_at=_at(1))

        resp = await _get_feed(client, env, kind)

        assert resp.status_code == 200
        assert resp.headers["content-type"] == _FEEDS[kind][1]

    @_ALL_KINDS
    async def test_entry_has_full_content_not_just_the_teaser(self, kind, client, db_session):
        env = await _env(db_session, "ffull")
        _page, slug = await env.post("ffull", _long_content(), description="Krotka zajawka wpisu.", published_at=_at(1))

        resp = await _get_feed(client, env, kind)

        item = next(i for i in _parse_feed(kind, resp.content) if i["url"] == _url(slug))
        assert _END_MARKER in item["content"]
        assert item["summary"] == "Krotka zajawka wpisu."
        assert _END_MARKER not in item["summary"]

    @pytest.mark.parametrize("kind", ["atom", "rss"])
    async def test_xml_feeds_carry_rendered_html_content(self, kind, client, db_session):
        env = await _env(db_session, "fhtml")
        _page, slug = await env.post("fhtml", _long_content(), published_at=_at(1))

        resp = await _get_feed(client, env, kind)

        item = next(i for i in _parse_feed(kind, resp.content) if i["url"] == _url(slug))
        assert "<strong>pogrubiony</strong>" in item["content"]
        assert "**pogrubiony**" not in item["content"]

    async def test_json_feed_carries_the_markdown_source(self, client, db_session):
        env = await _env(db_session, "fmd")
        _page, slug = await env.post("fmd", _long_content(), published_at=_at(1))

        resp = await _get_feed(client, env, "json")

        item = next(i for i in _parse_feed("json", resp.content) if i["url"] == _url(slug))
        assert "**pogrubiony**" in item["content"]
        assert "<strong>" not in item["content"]
        assert not item["content"].startswith("---")

    @_ALL_KINDS
    async def test_title_with_xml_special_characters_round_trips(self, kind, client, db_session):
        env = await _env(db_session, "ftitle")
        title = 'Fish & <Chips> ]]> "cytat" koniec'
        _page, slug = await env.post("ftitle", f"Tresc & <b> ]]> {_END_MARKER}", title=title, published_at=_at(1))

        resp = await _get_feed(client, env, kind)

        assert resp.status_code == 200
        item = next(i for i in _parse_feed(kind, resp.content) if i["url"] == _url(slug))
        assert item["title"] == title
        assert _END_MARKER in item["content"]

    @pytest.mark.parametrize("kind", ["atom", "rss"])
    async def test_control_characters_illegal_in_xml_are_removed(self, kind, client, db_session):
        env = await _env(db_session, "fctl")
        # \x08, \x0e, \x1f lezą na krawedziach zakresow sterujacych; U+FFFE i U+FFFF to niedozwolone znaki nie-sterujace.
        title = "Ster\x01ujacy\x0b \x08tytul\x0e\x1f\ufffe\uffff"
        _page, slug = await env.post("fctl", f"tresc\x0b\x0c\ufffe\uffff {_END_MARKER}", title=title, published_at=_at(1))

        resp = await _get_feed(client, env, kind)

        assert resp.status_code == 200
        item = next(i for i in _parse_feed(kind, resp.content) if i["url"] == _url(slug))
        assert item["title"] == "Sterujacy tytul"
        assert "\ufffe" not in item["content"]
        assert "\uffff" not in item["content"]
        assert _END_MARKER in item["content"]

    @_ALL_KINDS
    async def test_private_post_is_absent(self, kind, client, db_session):
        env = await _env(db_session, "fpriv")
        _pub, pub_slug = await env.post("fpub", published_at=_at(2))
        _priv, priv_slug = await env.post("fprv", "TAJNA-TRESC-FEED-MON259", title="Prywatny wpis feedu MON259", public=False, published_at=_at(3))

        resp = await _get_feed(client, env, kind)

        urls = [i["url"] for i in _parse_feed(kind, resp.content)]
        assert _url(pub_slug) in urls
        assert _url(priv_slug) not in urls
        assert priv_slug not in resp.text
        assert "TAJNA-TRESC-FEED-MON259" not in resp.text
        assert "Prywatny wpis feedu MON259" not in resp.text

    @_ALL_KINDS
    async def test_lang_filter_narrows_the_feed(self, kind, client, db_session):
        env = await _env(db_session, "flang")
        _pl, pl_slug = await env.post("flangpl", lang="pl", published_at=_at(1))
        _en, en_slug = await env.post("flangen", lang="en", published_at=_at(2))

        pl_resp = await _get_feed(client, env, kind, "?lang=pl")
        en_resp = await _get_feed(client, env, kind, "?lang=en")

        pl_urls = [i["url"] for i in _parse_feed(kind, pl_resp.content)]
        en_urls = [i["url"] for i in _parse_feed(kind, en_resp.content)]
        assert _url(pl_slug) in pl_urls
        assert _url(en_slug) not in pl_urls
        assert _url(en_slug) in en_urls
        assert _url(pl_slug) not in en_urls

    @_ALL_KINDS
    @pytest.mark.parametrize("bad_lang", ["xx", "\x00"])
    async def test_invalid_lang_gives_an_empty_feed_like_index_md(self, kind, bad_lang, client, db_session):
        env = await _env(db_session, "fbadlang")
        await env.post("fbadlang", published_at=_at(1))

        resp = await _get_feed(client, env, kind, f"?lang={quote(bad_lang)}")

        assert resp.status_code == 200
        assert _parse_feed(kind, resp.content) == []
        assert resp.headers["last-modified"] == _EPOCH_HEADER

    @_ALL_KINDS
    async def test_entries_are_newest_first(self, kind, client, db_session):
        env = await _env(db_session, "ford")
        # Kolejnosc wstawiania (5, 9, 1, 7) rozna od oczekiwanej (9, 7, 5, 1).
        slugs = {}
        for days in (5, 9, 1, 7):
            _page, slugs[days] = await env.post(f"ford{days}", published_at=_at(days))

        resp = await _get_feed(client, env, kind)

        urls = [i["url"] for i in _parse_feed(kind, resp.content)]
        assert urls[:4] == [_url(slugs[d]) for d in (9, 7, 5, 1)]

    @_ALL_KINDS
    async def test_feed_is_limited_to_feed_limit_newest_posts(self, kind, client, db_session):
        env = await _env(db_session, "flimit")
        slugs = {}
        for days in (3, 1, 2):
            _page, slugs[days] = await env.post(f"flimit{days}", published_at=_at(days))

        with patch("monolynx.blog_ai.FEED_LIMIT", 2):
            resp = await _get_feed(client, env, kind)

        urls = [i["url"] for i in _parse_feed(kind, resp.content)]
        assert urls == [_url(slugs[3]), _url(slugs[2])]

    async def test_json_feed_with_21_posts_lists_exactly_the_20_newest(self, client, db_session):
        env = await _env(db_session, "f21")
        slugs = {}
        for days in range(21):
            _page, slugs[days] = await env.post(f"f21-{days}", published_at=_at(days))

        resp = await _get_feed(client, env, "json")

        urls = [i["url"] for i in _parse_feed("json", resp.content)]
        assert urls == [_url(slugs[d]) for d in range(20, 0, -1)]
        assert _url(slugs[0]) not in urls

    @_ALL_KINDS
    async def test_storage_error_of_one_post_skips_only_that_post(self, kind, client, db_session):
        env = await _env(db_session, "fminio")
        _good_page, good_slug = await env.post("fgood", f"Dobra tresc {_END_MARKER}", published_at=_at(2))
        bad_page, bad_slug = await env.post("fbad", published_at=_at(3))
        env.store[bad_page.minio_path] = RuntimeError("MinIO niedostepne")

        resp = await _get_feed(client, env, kind)

        assert resp.status_code == 200
        items = _parse_feed(kind, resp.content)
        urls = [i["url"] for i in items]
        assert _url(good_slug) in urls
        assert _url(bad_slug) not in urls
        assert _END_MARKER in next(i for i in items if i["url"] == _url(good_slug))["content"]

    @_ALL_KINDS
    async def test_last_modified_is_the_max_updated_at_of_the_listed_posts(self, kind, client, db_session):
        env = await _env(db_session, "flm")
        newest = _at(400)
        # Kolejnosc listy (published_at malejaco): x, z, y. Najnowszy updated_at ma srodkowy z.
        await env.post("flmy", published_at=_at(11), updated_at=_at(300))
        await env.post("flmz", published_at=_at(12), updated_at=newest)
        await env.post("flmx", published_at=_at(13), updated_at=_at(200))

        resp = await _get_feed(client, env, kind)

        assert resp.headers["last-modified"] == format_datetime(newest, usegmt=True)


@pytest.mark.integration
class TestFeedsConditionalRequests:
    @_ALL_KINDS
    async def test_matching_if_none_match_returns_304_with_empty_body(self, kind, client, db_session):
        env = await _env(db_session, "fetag")
        await env.post("fetag", published_at=_at(1))

        first = await _get_feed(client, env, kind)
        with _minio(env.store):
            resp = await client.get(_FEEDS[kind][0], headers={"If-None-Match": first.headers["etag"]})

        assert first.status_code == 200
        assert re.fullmatch(r'"[0-9a-f]{32}"', first.headers["etag"])
        assert resp.status_code == 304
        assert resp.content == b""
        assert resp.headers["etag"] == first.headers["etag"]

    @_ALL_KINDS
    async def test_etag_is_stable_between_requests(self, kind, client, db_session):
        env = await _env(db_session, "fstable")
        await env.post("fstable", published_at=_at(1))

        first = await _get_feed(client, env, kind)
        second = await _get_feed(client, env, kind)

        assert first.content == second.content
        assert first.headers["etag"] == second.headers["etag"]

    @_ALL_KINDS
    async def test_etag_changes_after_content_edit(self, kind, client, db_session):
        env = await _env(db_session, "fedit")
        page, _slug = await env.post("fedit", "Tresc przed edycja.", published_at=_at(1))

        first = await _get_feed(client, env, kind)
        env.store[page.minio_path] = "Tresc po edycji."
        with _minio(env.store):
            resp = await client.get(_FEEDS[kind][0], headers={"If-None-Match": first.headers["etag"]})

        assert resp.status_code == 200
        assert resp.headers["etag"] != first.headers["etag"]
        assert "Tresc po edycji." in resp.text


@pytest.mark.integration
class TestFeedStructure:
    async def test_atom_feed_level_elements(self, client, db_session):
        env = await _env(db_session, "atomroot")
        await env.post("atomroot", published_at=_at(1))

        resp = await _get_feed(client, env, "atom")

        root = ET.fromstring(resp.content)
        assert root.tag == f"{_ATOM}feed"
        assert root.findtext(f"{_ATOM}title")
        assert root.findtext(f"{_ATOM}id")
        assert root.findtext(f"{_ATOM}updated")
        links = {link.get("rel"): link.get("href") for link in root.findall(f"{_ATOM}link")}
        assert links["self"] == f"{BASE}/blog/feed.xml"
        assert links["alternate"] == f"{BASE}/blog/"
        assert resp.content.startswith(b"<?xml")

    async def test_atom_entry_elements(self, client, db_session):
        env = await _env(db_session, "atomentry")
        _page, slug = await env.post(
            "atomentry",
            "Tresc atom.",
            description="Opis atom.",
            author_name="Jan Kowalski",
            published_at=PUBLISHED,
            updated_at=UPDATED,
        )

        resp = await _get_feed(client, env, "atom")

        entry = next(e for e in ET.fromstring(resp.content).findall(f"{_ATOM}entry") if e.findtext(f"{_ATOM}id") == _url(slug))
        assert entry.find(f"{_ATOM}link[@rel='alternate']").get("href") == _url(slug)
        assert datetime.fromisoformat(entry.findtext(f"{_ATOM}published")) == PUBLISHED
        assert datetime.fromisoformat(entry.findtext(f"{_ATOM}updated")) == UPDATED
        assert entry.findtext(f"{_ATOM}author/{_ATOM}name") == "Jan Kowalski"
        assert entry.findtext(f"{_ATOM}summary") == "Opis atom."
        assert entry.find(f"{_ATOM}content").get("type") == "html"

    async def test_atom_entry_without_published_at_has_no_published_element(self, client, db_session):
        env = await _env(db_session, "atomnopub")
        # Wpis bez published_at sortuje sie na koncu (NULLS LAST), wiec duzy limit gwarantuje, ze trafi do feedu.
        _page, slug = await env.post("atomnopub", published_at=None, created_at=_at(1))

        with patch("monolynx.blog_ai.FEED_LIMIT", 10_000):
            resp = await _get_feed(client, env, "atom")

        entry = next(e for e in ET.fromstring(resp.content).findall(f"{_ATOM}entry") if e.findtext(f"{_ATOM}id") == _url(slug))
        assert entry.find(f"{_ATOM}published") is None
        assert entry.findtext(f"{_ATOM}updated")

    async def test_rss_channel_and_item_elements(self, client, db_session):
        env = await _env(db_session, "rss")
        _page, slug = await env.post("rss", "Tresc rss.", description="Opis rss.", published_at=PUBLISHED)

        resp = await _get_feed(client, env, "rss")

        root = ET.fromstring(resp.content)
        assert root.tag == "rss"
        assert root.get("version") == "2.0"
        assert root.findtext("channel/title")
        assert root.findtext("channel/link")
        assert root.findtext("channel/description")
        item = next(i for i in root.findall("channel/item") if i.findtext("guid") == _url(slug))
        assert item.find("guid").get("isPermaLink") == "true"
        assert item.findtext("link") == _url(slug)
        assert parsedate_to_datetime(item.findtext("pubDate")) == PUBLISHED
        assert item.findtext("description") == "Opis rss."
        assert resp.content.startswith(b"<?xml")

    async def test_rss_item_without_published_at_has_no_pubdate(self, client, db_session):
        env = await _env(db_session, "rssnopub")
        _page, slug = await env.post("rssnopub", published_at=None, created_at=_at(1))

        with patch("monolynx.blog_ai.FEED_LIMIT", 10_000):
            resp = await _get_feed(client, env, "rss")

        item = next(i for i in ET.fromstring(resp.content).findall("channel/item") if i.findtext("guid") == _url(slug))
        assert item.find("pubDate") is None

    async def test_json_feed_level_fields(self, client, db_session):
        env = await _env(db_session, "jroot")
        await env.post("jroot", published_at=_at(1))

        resp = await _get_feed(client, env, "json")

        data = json.loads(resp.content)
        assert data["version"] == "https://jsonfeed.org/version/1.1"
        assert data["title"]
        assert data["home_page_url"] == f"{BASE}/blog/"
        assert data["feed_url"] == f"{BASE}/blog/feed.json"

    async def test_json_feed_item_fields(self, client, db_session):
        env = await _env(db_session, "jitem")
        _page, slug = await env.post(
            "jitem",
            "Tresc json.",
            title="Tytul json",
            description="Opis json.",
            tags=["alfa", "beta"],
            lang="en",
            published_at=PUBLISHED,
            updated_at=UPDATED,
        )

        resp = await _get_feed(client, env, "json")

        item = next(i for i in json.loads(resp.content)["items"] if i["id"] == _url(slug))
        assert item["url"] == _url(slug)
        assert item["title"] == "Tytul json"
        assert item["summary"] == "Opis json."
        assert datetime.fromisoformat(item["date_published"]) == PUBLISHED
        assert datetime.fromisoformat(item["date_modified"]) == UPDATED
        assert item["tags"] == ["alfa", "beta"]
        assert item["language"] == "en"

    async def test_json_feed_item_without_published_at_tags_or_description_omits_those_fields(self, client, db_session):
        env = await _env(db_session, "jbare")
        _page, slug = await env.post("jbare", published_at=None, created_at=_at(1))

        with patch("monolynx.blog_ai.FEED_LIMIT", 10_000):
            resp = await _get_feed(client, env, "json")

        item = next(i for i in json.loads(resp.content)["items"] if i["id"] == _url(slug))
        assert "date_published" not in item
        assert "tags" not in item
        assert "summary" not in item


# ---------------------------------------------------------------------------
# MON-259: dynamiczny /llms.txt i /llms-full.txt (kryteria 3, 4)
# ---------------------------------------------------------------------------


def _static_llms() -> str:
    return (Path(blog_ai.__file__).parent / "static" / "llms.txt").read_text(encoding="utf-8")


@pytest.mark.integration
class TestLlmsTxtBlogSection:
    async def test_section_lists_public_posts_with_md_addresses(self, client, db_session):
        env = await _env(db_session, "llms")
        _p1, slug1 = await env.post("llmsdesc", title="Wpis z opisem", description="Opis wpisu llms.", published_at=_at(2))
        _p2, slug2 = await env.post("llmsbare", title="Wpis bez opisu", published_at=_at(1))

        resp = await client.get("/llms.txt")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"
        lines = resp.text.splitlines()
        assert "## Blog" in lines
        assert f"- [Wpis z opisem]({BASE}/blog/{slug1}.md): Opis wpisu llms." in lines
        assert f"- [Wpis bez opisu]({BASE}/blog/{slug2}.md)" in lines
        assert f"{BASE}/blog/index.md" in resp.text
        assert f"{BASE}/blog/feed.xml" in resp.text

    async def test_static_content_comes_first_then_a_blank_line_then_the_section(self, client, db_session):
        env = await _env(db_session, "llmsorder")
        await env.post("llmsorder", published_at=_at(1))

        resp = await client.get("/llms.txt")

        static = _static_llms()
        assert resp.text.startswith(static.rstrip())
        rest = resp.text[len(static.rstrip()) :]
        assert rest.lstrip("\n").startswith("## Blog")
        assert "\n\n## Blog" in resp.text

    async def test_private_post_is_absent(self, client, db_session):
        env = await _env(db_session, "llmspriv")
        _pub, pub_slug = await env.post("llmspub", published_at=_at(2))
        _priv, priv_slug = await env.post("llmsprv", title="Prywatny wpis llms MON259", public=False, published_at=_at(3))

        resp = await client.get("/llms.txt")

        assert pub_slug in resp.text
        assert priv_slug not in resp.text
        assert "Prywatny wpis llms MON259" not in resp.text

    async def test_section_has_only_the_10_newest_posts_newest_first(self, client, db_session):
        env = await _env(db_session, "llms10")
        slugs = {}
        for days in range(11):
            _page, slugs[days] = await env.post(f"llms10-{days}", published_at=_at(days))

        resp = await client.get("/llms.txt")

        positions = [resp.text.find(f"/blog/{slugs[d]}.md") for d in range(10, 0, -1)]
        assert all(pos >= 0 for pos in positions)
        assert positions == sorted(positions)
        assert f"/blog/{slugs[0]}.md" not in resp.text

    async def test_database_error_gives_200_with_static_content_only(self, client):
        with patch("monolynx.blog_ai.list_public_posts", AsyncMock(side_effect=Exception("baza niedostepna"))):
            resp = await client.get("/llms.txt")

        assert resp.status_code == 200
        assert resp.text == _static_llms()
        assert "## Blog" not in resp.text

    async def test_no_public_posts_omits_the_section(self, client):
        with patch("monolynx.blog_ai.list_public_posts", AsyncMock(return_value=SimpleNamespace(items=[]))):
            resp = await client.get("/llms.txt")

        assert resp.status_code == 200
        assert resp.text == _static_llms()
        assert "## Blog" not in resp.text


@pytest.mark.integration
class TestLlmsFull:
    async def _full(self, client, env):
        _clear_llms_cache()
        with _minio(env.store):
            return await client.get("/llms-full.txt")

    async def test_returns_text_plain_with_full_documents_of_public_posts(self, client, db_session):
        env = await _env(db_session, "full")
        _page, slug = await env.post("full", _long_content(), title="Pelny wpis llms", published_at=_at(1))

        resp = await self._full(client, env)

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"
        assert _END_MARKER in resp.text
        assert f"/blog/{slug}" in resp.text
        for key in FRONTMATTER_KEYS:
            assert f"\n{key}: " in resp.text

    async def test_posts_are_separated_by_the_separator_newest_first(self, client, db_session):
        env = await _env(db_session, "fullsep")
        _old, old_slug = await env.post("fullold", "Tresc starego MON259-OLD.", published_at=_at(1))
        _new, new_slug = await env.post("fullnew", "Tresc nowego MON259-NEW.", published_at=_at(2))

        resp = await self._full(client, env)

        parts = resp.text.split(blog_ai.LLMS_FULL_SEPARATOR)
        new_idx = next(i for i, p in enumerate(parts) if "MON259-NEW" in p)
        old_idx = next(i for i, p in enumerate(parts) if "MON259-OLD" in p)
        assert new_idx < old_idx
        assert f"/blog/{new_slug}" in parts[new_idx]
        assert "MON259-OLD" not in parts[new_idx]
        assert f"/blog/{old_slug}" in parts[old_idx]
        assert "MON259-NEW" not in parts[old_idx]
        front, body = _split(parts[old_idx])
        assert list(front) == FRONTMATTER_KEYS
        assert "MON259-OLD" in body

    async def test_private_post_is_absent(self, client, db_session):
        env = await _env(db_session, "fullpriv")
        await env.post("fullpub", "Publiczna tresc MON259.", published_at=_at(2))
        _priv, priv_slug = await env.post("fullprv", "TAJNA-TRESC-FULL-MON259", title="Prywatny wpis full MON259", public=False, published_at=_at(3))

        resp = await self._full(client, env)

        assert "Publiczna tresc MON259." in resp.text
        assert "TAJNA-TRESC-FULL-MON259" not in resp.text
        assert "Prywatny wpis full MON259" not in resp.text
        assert priv_slug not in resp.text

    async def test_storage_error_of_one_post_skips_only_that_post(self, client, db_session):
        env = await _env(db_session, "fullminio")
        await env.post("fullgood", "Dobra tresc MON259-GOOD.", published_at=_at(1))
        bad_page, bad_slug = await env.post("fullbad", published_at=_at(2))
        env.store[bad_page.minio_path] = RuntimeError("MinIO niedostepne")

        resp = await self._full(client, env)

        assert resp.status_code == 200
        assert "MON259-GOOD" in resp.text
        assert f"/blog/{bad_slug}" not in resp.text

    async def test_no_truncation_line_when_everything_fits(self, client, db_session):
        env = await _env(db_session, "fullfit")
        await env.post("fullfit", published_at=_at(1))

        resp = await self._full(client, env)

        assert "> Truncated:" not in resp.text

    async def test_posts_over_the_size_limit_are_omitted_whole_with_a_correct_truncated_line(self, client, db_session):
        env = await _env(db_session, "fulltrunc")
        for days, marker in ((3, "END-A-MON259"), (2, "END-B-MON259"), (1, "END-C-MON259")):
            await env.post(f"fulltrunc{days}", _long_content(marker), published_at=_at(days))
        full = await self._full(client, env)
        parts = full.text.split(blog_ai.LLMS_FULL_SEPARATOR)
        idx_b = next(i for i, p in enumerate(parts) if "END-B-MON259" in p)
        # Limit miesci wpisy A i B (z zapasem mniejszym niz dokument C), ale nie C.
        limit = len(blog_ai.LLMS_FULL_SEPARATOR.join(parts[: idx_b + 1]).encode()) + 300
        total = len(re.findall(r"^word_count: \d+$", full.text, re.MULTILINE))

        with patch("monolynx.blog_ai.LLMS_FULL_MAX_BYTES", limit):
            resp = await self._full(client, env)

        kept = len(re.findall(r"^word_count: \d+$", resp.text, re.MULTILINE))
        assert resp.status_code == 200
        assert "END-A-MON259" in resp.text
        assert "END-B-MON259" in resp.text
        assert "END-C-MON259" not in resp.text
        assert 0 < kept < total
        expected = f"> Truncated: {total - kept} of {total} posts omitted (size limit {limit} bytes). Full list: {BASE}/blog/index.md"
        assert resp.text.rstrip("\n").splitlines()[-1] == expected
        assert resp.text.count("> Truncated:") == 1


# ---------------------------------------------------------------------------
# MON-259: JSON-LD z blokow (kryterium 7)
# ---------------------------------------------------------------------------

_FAQ_BLOCK = ":::faq\n### Jak zaczac?\nZaloz konto.\n\n### Co z danymi?\nSa szyfrowane.\n\n### Ile to kosztuje?\nPierwszy projekt jest darmowy.\n:::"
_STEPS_BLOCK = ":::steps\n1. **Pierwszy** Opis a.\n2. **Drugi** Opis b.\n3. **Trzeci** Opis c.\n4. **Czwarty** Opis d.\n:::"
_VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
_VIDEO_BLOCK = f":::video\n{_VIDEO_URL}\n\nOpis wideo.\n:::"
_STRUCTURED_TYPES = {"FAQPage", "HowTo", "VideoObject"}


def _graph_by_type(html: str) -> dict[str, list[dict]]:
    blocks = _ld_blocks(html)
    assert len(blocks) == 1
    result: dict[str, list[dict]] = {}
    for node in json.loads(blocks[0])["@graph"]:
        result.setdefault(node["@type"], []).append(node)
    return result


@pytest.mark.integration
class TestJsonLdStructuredBlocks:
    @pytest.mark.parametrize(
        ("blocks", "expected"),
        [
            ([], set()),
            ([_FAQ_BLOCK], {"FAQPage"}),
            ([_STEPS_BLOCK], {"HowTo"}),
            ([_VIDEO_BLOCK], {"VideoObject"}),
            ([_FAQ_BLOCK, _STEPS_BLOCK, _VIDEO_BLOCK], _STRUCTURED_TYPES),
        ],
        ids=["none", "faq", "steps", "video", "all"],
    )
    async def test_graph_has_exactly_the_types_of_the_blocks_in_the_post(self, blocks, expected, client, db_session):
        env = await _env(db_session, "ldtypes")
        content = "Wstep.\n\n" + "\n\n".join(blocks)
        _page, slug = await env.post("ldtypes", content)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}")

        graph = _graph_by_type(resp.text)
        assert set(graph) & _STRUCTURED_TYPES == expected
        assert {"BlogPosting", "BreadcrumbList"} <= set(graph)
        for type_name in expected:
            assert len(graph[type_name]) == 1

    async def test_faq_page_has_one_question_per_faq_item(self, client, db_session):
        env = await _env(db_session, "ldfaq")
        _page, slug = await env.post("ldfaq", _FAQ_BLOCK)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}")

        faq = _graph_by_type(resp.text)["FAQPage"][0]
        assert len(faq["mainEntity"]) == 3
        assert [q["@type"] for q in faq["mainEntity"]] == ["Question"] * 3
        assert [q["name"] for q in faq["mainEntity"]] == ["Jak zaczac?", "Co z danymi?", "Ile to kosztuje?"]
        assert [q["acceptedAnswer"] for q in faq["mainEntity"]][1] == {"@type": "Answer", "text": "Sa szyfrowane."}

    async def test_how_to_has_a_numbered_step_per_item_named_after_the_post(self, client, db_session):
        env = await _env(db_session, "ldhowto")
        _page, slug = await env.post("ldhowto", _STEPS_BLOCK, title="Jak to zrobic krok po kroku")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}")

        how_to = _graph_by_type(resp.text)["HowTo"][0]
        assert how_to["name"] == "Jak to zrobic krok po kroku"
        assert [s["@type"] for s in how_to["step"]] == ["HowToStep"] * 4
        assert [s["position"] for s in how_to["step"]] == [1, 2, 3, 4]
        assert [s["name"] for s in how_to["step"]] == ["Pierwszy", "Drugi", "Trzeci", "Czwarty"]
        assert how_to["step"][2]["text"] == "Opis c."

    async def test_video_object_carries_url_description_and_upload_date(self, client, db_session):
        env = await _env(db_session, "ldvideo")
        _page, slug = await env.post("ldvideo", _VIDEO_BLOCK, title="Wpis z wideo", published_at=PUBLISHED)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}")

        video = _graph_by_type(resp.text)["VideoObject"][0]
        assert video["name"] == "Wpis z wideo"
        assert video["description"] == "Opis wideo."
        assert video["embedUrl"] == _VIDEO_URL
        assert datetime.fromisoformat(video["uploadDate"]) == PUBLISHED

    async def test_one_video_object_per_video(self, client, db_session):
        env = await _env(db_session, "ldvideos")
        second = ":::video\nhttps://vimeo.com/123456789\n\nDrugie wideo.\n:::"
        _page, slug = await env.post("ldvideos", f"{_VIDEO_BLOCK}\n\n{second}")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}")

        videos = _graph_by_type(resp.text)["VideoObject"]
        assert [v["embedUrl"] for v in videos] == [_VIDEO_URL, "https://vimeo.com/123456789"]
        assert [v["description"] for v in videos] == ["Opis wideo.", "Drugie wideo."]


# ---------------------------------------------------------------------------
# MON-259: <link rel="alternate"> do feedow w <head> bloga (kryterium 8) i /index.md
# ---------------------------------------------------------------------------

_FEED_LINKS = {
    ("application/atom+xml", "/blog/feed.xml"),
    ("application/rss+xml", "/blog/rss.xml"),
    ("application/feed+json", "/blog/feed.json"),
}


def _feed_alternate_links(html: str) -> set[tuple[str, str]]:
    """Pary (type, href) z tagow `<link rel="alternate">` wskazujacych na feedy bloga."""
    found = set()
    for tag in re.findall(r"<link\b[^>]*>", html):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', tag))
        if attrs.get("rel") == "alternate" and attrs.get("href", "").startswith("/blog/") and attrs["href"].rsplit(".", 1)[-1] in ("xml", "json"):
            found.add((attrs.get("type", ""), attrs["href"]))
    return found


@pytest.mark.integration
class TestFeedDiscoveryLinks:
    async def test_post_page_head_links_to_the_three_feeds(self, client, db_session):
        env = await _env(db_session, "headpost")
        _page, slug = await env.post("headpost")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}")

        head = resp.text.split("</head>")[0]
        assert _feed_alternate_links(head) == _FEED_LINKS

    async def test_blog_list_head_links_to_the_three_feeds(self, client, db_session):
        with _minio({}):
            resp = await client.get("/blog/")

        head = resp.text.split("</head>")[0]
        assert resp.status_code == 200
        assert _feed_alternate_links(head) == _FEED_LINKS

    async def test_page_outside_the_blog_has_no_feed_links(self, client, monkeypatch):
        # Domyślne SKIP_LANDING_PAGE=True (CI) przekierowuje "/" na logowanie.
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)

        resp = await client.get("/")

        assert resp.status_code == 200
        assert _feed_alternate_links(resp.text) == set()


@pytest.mark.integration
class TestLandingMarkdownLinksToBlog:
    # `_landing_markdown` buduje wszystkie adresy ze stalej bazy produkcyjnej, nie z APP_URL.
    @pytest.mark.parametrize(
        ("query", "module_name", "other_language_name"),
        [("", "Connections", "Połączenia"), ("?lang=en", "Connections", "Połączenia"), ("?lang=pl", "Połączenia", "Connections")],
    )
    async def test_index_md_links_to_the_blog_index_in_each_language(self, query, module_name, other_language_name, client):
        resp = await client.get(f"/index.md{query}")

        assert resp.status_code == 200
        assert f"[{module_name}](" in resp.text
        assert f"[{other_language_name}](" not in resp.text
        assert "(https://monolynx.com/blog/index.md)" in resp.text


# ---------------------------------------------------------------------------
# MON-259, poprawka 2: gałęzie kodu feedów, llms i JSON-LD z wejściem, które do nich dociera
# ---------------------------------------------------------------------------


def _only(*slugs: str):
    """Patch `list_public_posts`: realna lista zawężona do podanych slugów (deterministyczne liczby w `Truncated`)."""

    async def fake(db, page, per_page, **kwargs):
        real = await real_list_public_posts(db, page, per_page, **kwargs)
        return SimpleNamespace(items=[item for item in real.items if item.post.slug in slugs])

    return patch("monolynx.blog_ai.list_public_posts", fake)


@pytest.mark.integration
class TestFeedBranches:
    @_ALL_KINDS
    async def test_post_without_description_has_no_summary(self, kind, client, db_session):
        env = await _env(db_session, "fnodesc")
        _page, slug = await env.post("fnodesc", published_at=_at(1))

        resp = await _get_feed(client, env, kind)

        item = next(i for i in _parse_feed(kind, resp.content) if i["url"] == _url(slug))
        assert item["summary"] is None

    async def test_atom_entry_with_empty_author_name_has_no_author_element(self, client, db_session):
        env = await _env(db_session, "fnoauth")
        _page, slug = await env.post("fnoauth", author_name="", published_at=_at(1))

        resp = await _get_feed(client, env, "atom")

        entry = next(e for e in ET.fromstring(resp.content).findall(f"{_ATOM}entry") if e.findtext(f"{_ATOM}id") == _url(slug))
        assert entry.find(f"{_ATOM}author") is None

    async def test_json_feed_content_text_has_absolute_links(self, client, db_session):
        env = await _env(db_session, "fabs")
        _page, slug = await env.post("fabs", "Zobacz [inny wpis](/blog/inny-wpis).", published_at=_at(1))

        resp = await _get_feed(client, env, "json")

        item = next(i for i in _parse_feed("json", resp.content) if i["url"] == _url(slug))
        assert f"[inny wpis]({BASE}/blog/inny-wpis)" in item["content"]

    @_ALL_KINDS
    async def test_empty_lang_means_no_filter(self, kind, client, db_session):
        env = await _env(db_session, "femptylang")
        _pl, pl_slug = await env.post("femptypl", lang="pl", published_at=_at(1))
        _en, en_slug = await env.post("femptyen", lang="en", published_at=_at(2))

        resp = await _get_feed(client, env, kind, "?lang=")

        urls = [i["url"] for i in _parse_feed(kind, resp.content)]
        assert _url(pl_slug) in urls
        assert _url(en_slug) in urls


@pytest.mark.integration
class TestLlmsTxtDetails:
    async def test_whitespace_in_title_and_description_is_collapsed_to_one_line(self, client, db_session):
        env = await _env(db_session, "llmsws")
        _page, slug = await env.post("llmsws", title="Wpis\nz   nowa linia", description="Opis\nwielu   linii", published_at=_at(1))

        resp = await client.get("/llms.txt")

        assert f"- [Wpis z nowa linia]({BASE}/blog/{slug}.md): Opis wielu linii" in resp.text.splitlines()

    async def test_missing_static_file_returns_404_without_touching_the_blog(self, client, tmp_path):
        spy = AsyncMock(return_value="## Blog\n")

        with patch("monolynx.main._STATIC_DIR", tmp_path), patch("monolynx.blog_ai.build_llms_blog_section", spy):
            resp = await client.get("/llms.txt")

        assert resp.status_code == 404
        spy.assert_not_awaited()


@pytest.mark.integration
class TestLlmsFullLimits:
    async def _get(self, client, env, *slugs):
        # Każde wywołanie liczy dokument od zera: testy limitów zmieniają `LLMS_FULL_MAX_BYTES`, a cache
        # (odcisk z limitem) ma osobne testy w `TestLlmsFullCache`.
        _clear_llms_cache()
        with _minio(env.store), _only(*slugs):
            return await client.get("/llms-full.txt")

    async def test_no_public_posts_gives_200_with_the_header_only(self, client):
        with patch("monolynx.blog_ai.list_public_posts", AsyncMock(return_value=SimpleNamespace(items=[]))):
            resp = await client.get("/llms-full.txt")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"
        assert resp.text.startswith("# Monolynx Blog: full content")
        assert f"{BASE}/blog/index.md" in resp.text
        assert resp.text.rstrip("\n").endswith("No public posts yet.")
        assert blog_ai.LLMS_FULL_SEPARATOR not in resp.text
        assert "> Truncated:" not in resp.text

    async def test_posts_carry_their_translations_in_the_frontmatter(self, client, db_session):
        env = await _env(db_session, "fulltr")
        root, root_slug = await env.post("fulltrpl", "Tresc MON259-TRPL.", lang="pl", published_at=_at(2))
        _en, en_slug = await env.post("fulltren", "Tresc MON259-TREN.", lang="en", translation_of_id=root.id, published_at=_at(1))

        resp = await self._get(client, env, root_slug, en_slug)

        docs = {("pl" if "MON259-TRPL" in p else "en"): p for p in resp.text.split(blog_ai.LLMS_FULL_SEPARATOR)[1:]}
        assert _split(docs["pl"])[0]["translations"] == [{"lang": "en", "url": f"{BASE}/blog/{en_slug}"}]
        assert _split(docs["en"])[0]["translations"] == [{"lang": "pl", "url": f"{BASE}/blog/{root_slug}"}]

    async def test_oversized_post_ends_the_list_even_when_a_later_one_would_fit(self, client, db_session):
        env = await _env(db_session, "fullstop")
        _a, a_slug = await env.post("fullstopa", "Krotki A MON259-A.", published_at=_at(3))
        _c, c_slug = await env.post("fullstopc", "Duzy akapit. " * 600 + "\n\nMON259-C", published_at=_at(2))
        _d, d_slug = await env.post("fullstopd", "Krotki D MON259-D.", published_at=_at(1))
        full = await self._get(client, env, a_slug, c_slug, d_slug)
        parts = full.text.split(blog_ai.LLMS_FULL_SEPARATOR)
        doc_a = next(p for p in parts if "MON259-A" in p)
        doc_d = next(p for p in parts if "MON259-D" in p)
        # Miesci sie A i D (z zapasem na linie Truncated), ale nie duzy C pomiedzy nimi.
        limit = len(blog_ai.LLMS_FULL_SEPARATOR.join([parts[0], doc_a, doc_d]).encode()) + 400

        with patch("monolynx.blog_ai.LLMS_FULL_MAX_BYTES", limit):
            resp = await self._get(client, env, a_slug, c_slug, d_slug)

        assert "MON259-A" in resp.text
        assert "MON259-C" not in resp.text
        assert "MON259-D" not in resp.text
        expected = f"> Truncated: 2 of 3 posts omitted (size limit {limit} bytes). Full list: {BASE}/blog/index.md"
        assert resp.text.rstrip("\n").splitlines()[-1] == expected
        assert len(resp.content) <= limit

    async def test_limit_counts_utf8_bytes_and_separator_and_allows_exactly_the_limit(self, client, db_session):
        env = await _env(db_session, "fullbytes")
        _a, a_slug = await env.post("fullbytes", "Zażółć gęślą jaźń. " * 200 + "MON259-PL", published_at=_at(1))
        full = await self._get(client, env, a_slug)
        exact = len(full.content)

        with patch("monolynx.blog_ai.LLMS_FULL_MAX_BYTES", exact):
            at_limit = await self._get(client, env, a_slug)
        with patch("monolynx.blog_ai.LLMS_FULL_MAX_BYTES", exact - 1):
            below_limit = await self._get(client, env, a_slug)

        assert "> Truncated:" not in full.text
        assert at_limit.content == full.content
        assert "MON259-PL" not in below_limit.text
        expected = f"> Truncated: 1 of 1 posts omitted (size limit {exact - 1} bytes). Full list: {BASE}/blog/index.md"
        assert below_limit.text.rstrip("\n").splitlines()[-1] == expected
        assert len(below_limit.content) <= exact - 1

    async def test_truncated_response_never_exceeds_the_limit_including_the_truncated_line(self, client, db_session):
        env = await _env(db_session, "fullreserve")
        _a, a_slug = await env.post("fullresa", "Tresc A MON259-RA.", published_at=_at(2))
        _b, b_slug = await env.post("fullresb", "Tresc B MON259-RB.", published_at=_at(1))
        full = await self._get(client, env, a_slug, b_slug)
        parts = full.text.split(blog_ai.LLMS_FULL_SEPARATOR)
        # Limit miesci naglowek i wpis A, ale juz bez linii Truncated: A musi odpasc, zeby calosc zmiescila sie w limicie.
        limit = len(blog_ai.LLMS_FULL_SEPARATOR.join(parts[:2]).encode())

        with patch("monolynx.blog_ai.LLMS_FULL_MAX_BYTES", limit):
            resp = await self._get(client, env, a_slug, b_slug)

        assert "MON259-RA" not in resp.text
        assert "MON259-RB" not in resp.text
        assert f"> Truncated: 2 of 2 posts omitted (size limit {limit} bytes)" in resp.text
        assert len(resp.content) <= limit


@pytest.mark.integration
class TestJsonLdVideoBranches:
    async def test_video_without_description_falls_back_to_the_post_description(self, client, db_session):
        env = await _env(db_session, "ldvnodesc")
        _page, slug = await env.post("ldvnodesc", ":::video\nhttps://youtu.be/dQw4w9WgXcQ\n:::", description="Opis wpisu dla wideo.")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}")

        video = _graph_by_type(resp.text)["VideoObject"][0]
        assert video["description"] == "Opis wpisu dla wideo."
        assert video["embedUrl"] == "https://youtu.be/dQw4w9WgXcQ"

    async def test_video_object_has_no_upload_date_for_a_post_without_published_at(self, client, db_session):
        env = await _env(db_session, "ldvnopub")
        _page, slug = await env.post("ldvnopub", _VIDEO_BLOCK, published_at=None, created_at=_at(1))

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}")

        video = _graph_by_type(resp.text)["VideoObject"][0]
        assert "uploadDate" not in video


# ---------------------------------------------------------------------------
# MON-271 pkt 3: slug o złym kształcie na trasach warstwy AI
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMalformedSlugOnAiRoutes:
    async def test_markdown_twin_with_nul_slug_returns_404(self, client):
        resp = await client.get("/blog/%00.md")

        assert resp.status_code == 404

    async def test_accept_markdown_with_nul_slug_returns_404(self, client):
        resp = await client.get("/blog/%00", headers={"Accept": "text/markdown"})

        assert resp.status_code == 404

    async def test_nul_slug_markdown_twin_never_queries_the_database(self, client):
        with patch("sqlalchemy.ext.asyncio.AsyncSession.execute", new_callable=AsyncMock) as mock_execute:
            resp = await client.get("/blog/%00.md")

        assert resp.status_code == 404
        mock_execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# MON-271 pkt 4: adresy obrazów własnej strony w `.md`, feedach i llms-full bez `/dashboard/`
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDashboardImageAddressesAreRewritten:
    async def _post_with_dashboard_image(self, env, name):
        page, slug = await env.post(name, "placeholder", published_at=_at(1))
        attachment_id = uuid.uuid4()
        address = f"/dashboard/{env.project.slug}/wiki/pages/{page.id}/attachments/{attachment_id}/obraz.png"
        env.store[page.minio_path] = f"Wstep wpisu.\n\n![obraz]({address})\n\nKoniec wpisu."
        return slug, attachment_id

    async def test_markdown_twin_has_absolute_public_image_address_and_no_dashboard_path(self, client, db_session):
        env = await _env(db_session, "dashmd")
        slug, att = await self._post_with_dashboard_image(env, "dashmd")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        _front, body = _split(resp.text)
        assert "/dashboard/" not in resp.text
        assert f"![obraz]({BASE}/blog/{slug}/attachments/{att}/obraz.png)" in body

    async def test_accept_markdown_twin_has_the_same_public_image_address(self, client, db_session):
        env = await _env(db_session, "dashaccept")
        slug, att = await self._post_with_dashboard_image(env, "dashaccept")

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}", headers={"Accept": "text/markdown"})

        assert "/dashboard/" not in resp.text
        assert f"{BASE}/blog/{slug}/attachments/{att}/obraz.png" in resp.text

    @_ALL_KINDS
    async def test_feed_content_has_public_image_address_and_no_dashboard_path(self, kind, client, db_session):
        env = await _env(db_session, f"dashfeed{kind}")
        slug, att = await self._post_with_dashboard_image(env, f"dashfeed{kind}")

        resp = await _get_feed(client, env, kind)

        item = next(i for i in _parse_feed(kind, resp.content) if i["url"] == _url(slug))
        assert "/dashboard/" not in item["content"]
        assert f"/blog/{slug}/attachments/{att}/obraz.png" in item["content"]

    async def test_json_feed_image_address_is_absolute(self, client, db_session):
        env = await _env(db_session, "dashjsonabs")
        slug, att = await self._post_with_dashboard_image(env, "dashjsonabs")

        resp = await _get_feed(client, env, "json")

        item = next(i for i in _parse_feed("json", resp.content) if i["url"] == _url(slug))
        assert f"![obraz]({BASE}/blog/{slug}/attachments/{att}/obraz.png)" in item["content"]

    async def test_llms_full_has_absolute_public_image_address_and_no_dashboard_path(self, client, db_session):
        env = await _env(db_session, "dashfull")
        slug, att = await self._post_with_dashboard_image(env, "dashfull")
        _clear_llms_cache()

        with _minio(env.store), _only(slug):
            resp = await client.get("/llms-full.txt")

        assert "/dashboard/" not in resp.text
        assert f"![obraz]({BASE}/blog/{slug}/attachments/{att}/obraz.png)" in resp.text

    async def test_attachment_of_another_page_is_not_rewritten(self, client, db_session):
        env = await _env(db_session, "dashother")
        page, slug = await env.post("dashother", "placeholder", published_at=_at(1))
        foreign = f"/dashboard/{env.project.slug}/wiki/pages/{uuid.uuid4()}/attachments/{uuid.uuid4()}/cudzy.png"
        env.store[page.minio_path] = f"![cudzy]({foreign})"

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        assert f"![cudzy]({BASE}{foreign})" in resp.text
        assert f"/blog/{slug}/attachments/" not in resp.text


# ---------------------------------------------------------------------------
# MON-271 pkt 12 (para 1): czas czytania `.md` liczy `services.blog.reading_time_minutes`
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReadingTimeSharedWithService:
    """Test charakteryzujący: zachowanie nie zmienia się po usunięciu duplikatu `_WORDS_PER_MINUTE`."""

    @pytest.mark.parametrize(("words", "minutes"), [(200, 1), (201, 2), (400, 2), (401, 3)])
    async def test_frontmatter_reading_time_equals_the_service_function_on_the_same_text(self, words, minutes, client, db_session):
        env = await _env(db_session, f"rtshared{words}")
        content = " ".join(["slowo"] * words)
        _page, slug = await env.post(f"rtshared{words}", content)

        with _minio(env.store):
            resp = await client.get(f"/blog/{slug}.md")

        front, _body = _split(resp.text)
        assert front["reading_time_minutes"] == blog_service.reading_time_minutes(render_post_markdown(content))
        assert front["reading_time_minutes"] == minutes


# ---------------------------------------------------------------------------
# MON-271 pkt 9: cache `/llms-full.txt` pod odciskiem metadanych
# ---------------------------------------------------------------------------

_OLD_UPDATE = datetime(2020, 1, 1, 8, 0, tzinfo=UTC)

_METADATA_MUTATIONS = {
    "description": lambda page, post: setattr(post, "description", "Nowy opis MON271"),
    "author_name": lambda page, post: setattr(post, "author_name", "Nowy Autor MON271"),
    "tags": lambda page, post: setattr(post, "tags", ["nowy-tag-271"]),
    "last_verified_at": lambda page, post: setattr(post, "last_verified_at", date(2026, 6, 6)),
    "published_at": lambda page, post: setattr(post, "published_at", datetime(2026, 6, 6, 6, 0, tzinfo=UTC)),
    "lang": lambda page, post: setattr(post, "lang", "en"),
    "title": lambda page, post: setattr(page, "title", "Nowy Tytul MON271"),
}


@pytest.mark.integration
class TestLlmsFullCache:
    """Gotowy dokument leży w pamięci procesu pod odciskiem z metadanych (jedno zapytanie, bez MinIO) i limitu bajtów."""

    async def _get(self, client, env, *slugs):
        with _minio(env.store), _only(*slugs):
            return await client.get("/llms-full.txt")

    async def _two_posts(self, db_session, tag):
        env = await _env(db_session, tag)
        page_a, slug_a = await env.post(f"{tag}a", "Tresc A MON271-A.", published_at=_at(2), updated_at=_OLD_UPDATE)
        page_b, slug_b = await env.post(f"{tag}b", "Tresc B MON271-B.", published_at=_at(1), updated_at=_OLD_UPDATE)
        return env, (page_a, slug_a), (page_b, slug_b)

    async def test_unchanged_second_request_does_not_read_storage_again(self, client, db_session):
        env, (_pa, slug_a), (_pb, slug_b) = await self._two_posts(db_session, "cachehit")

        with patch("monolynx.blog_ai.get_page_content", wraps=real_get_page_content) as spy:
            first = await self._get(client, env, slug_a, slug_b)
            reads_after_first = spy.call_count
            second = await self._get(client, env, slug_a, slug_b)

        assert first.status_code == 200
        assert reads_after_first == 2
        assert spy.call_count == reads_after_first
        assert second.text == first.text
        assert "MON271-A" in second.text
        assert "MON271-B" in second.text

    async def test_clear_function_forces_a_fresh_read(self, client, db_session):
        env, (_pa, slug_a), (_pb, slug_b) = await self._two_posts(db_session, "cacheclear")

        with patch("monolynx.blog_ai.get_page_content", wraps=real_get_page_content) as spy:
            first = await self._get(client, env, slug_a, slug_b)
            reads_after_first = spy.call_count
            blog_ai.clear_llms_full_cache()
            second = await self._get(client, env, slug_a, slug_b)

        assert spy.call_count == reads_after_first * 2
        assert second.text == first.text

    async def test_document_with_a_storage_error_is_not_cached_and_the_post_returns_after_recovery(self, client, db_session):
        env, (_pa, slug_a), (page_b, slug_b) = await self._two_posts(db_session, "cachebroken")
        env.store[page_b.minio_path] = RuntimeError("MinIO niedostepne")

        with patch("monolynx.blog_ai.get_page_content", wraps=real_get_page_content) as spy:
            first = await self._get(client, env, slug_a, slug_b)
            reads_after_first = spy.call_count
            # storage wraca, metadane i `updated_at` bez zmian, cache nie jest czyszczony
            env.store[page_b.minio_path] = "Tresc B po naprawie MON271-FIXED."
            second = await self._get(client, env, slug_a, slug_b)

        assert first.status_code == 200
        assert "MON271-A" in first.text
        assert "MON271-FIXED" not in first.text
        assert reads_after_first == 2
        assert second.status_code == 200
        assert "MON271-FIXED" in second.text
        assert spy.call_count > reads_after_first

    async def test_complete_document_is_served_from_cache_even_when_storage_fails_later(self, client, db_session):
        env, (page_a, slug_a), (page_b, slug_b) = await self._two_posts(db_session, "cachecomplete")

        with patch("monolynx.blog_ai.get_page_content", wraps=real_get_page_content) as spy:
            first = await self._get(client, env, slug_a, slug_b)
            reads_after_first = spy.call_count
            # pelny dokument jest w cache: pozniejsza usterka storage nie moze go zepsuc ani wywolac odczytu
            env.store[page_a.minio_path] = RuntimeError("MinIO niedostepne")
            env.store[page_b.minio_path] = RuntimeError("MinIO niedostepne")
            second = await self._get(client, env, slug_a, slug_b)

        assert first.status_code == 200
        assert "MON271-A" in first.text
        assert "MON271-B" in first.text
        assert reads_after_first == 2
        assert spy.call_count == reads_after_first
        assert second.text == first.text

    async def test_content_change_with_a_newer_updated_at_invalidates_the_cache(self, client, db_session):
        env, (page_a, slug_a), (_pb, slug_b) = await self._two_posts(db_session, "cachecontent")
        first = await self._get(client, env, slug_a, slug_b)
        env.store[page_a.minio_path] = "Tresc A po zmianie MON271-NEW."
        page_a.updated_at = datetime(2026, 6, 6, 6, 0, tzinfo=UTC)
        await db_session.flush()

        second = await self._get(client, env, slug_a, slug_b)

        assert "MON271-NEW" not in first.text
        assert "MON271-NEW" in second.text
        assert "MON271-A." not in second.text

    async def test_content_edit_through_the_service_invalidates_the_cache(self, client, db_session):
        env, (page_a, slug_a), (_pb, slug_b) = await self._two_posts(db_session, "cacheedit")
        first = await self._get(client, env, slug_a, slug_b)

        env.store[page_a.minio_path] = "Tresc A po edycji przez serwis MON271-EDIT."
        with _wiki_side_effects():
            await update_wiki_page(
                page=page_a, project_slug=env.project.slug, content=env.store[page_a.minio_path], user_id=env.user.id, db=db_session
            )
        second = await self._get(client, env, slug_a, slug_b)

        assert "MON271-EDIT" not in first.text
        assert "MON271-EDIT" in second.text

    @pytest.mark.parametrize("field", sorted(_METADATA_MUTATIONS))
    async def test_metadata_change_invalidates_the_cache(self, field, client, db_session):
        env, (page_a, slug_a), (_pb, slug_b) = await self._two_posts(db_session, f"cachemeta{field.replace('_', '')}")
        post_a = await _blog_post_of(db_session, page_a.id)
        with patch("monolynx.blog_ai.get_page_content", wraps=real_get_page_content) as spy:
            first = await self._get(client, env, slug_a, slug_b)
            reads_after_first = spy.call_count
            _METADATA_MUTATIONS[field](page_a, post_a)
            await db_session.flush()

            second = await self._get(client, env, slug_a, slug_b)

        assert second.text != first.text
        assert spy.call_count > reads_after_first

    async def test_slug_change_invalidates_the_cache(self, client, db_session):
        env, (page_a, slug_a), (_pb, slug_b) = await self._two_posts(db_session, "cacheslug")
        post_a = await _blog_post_of(db_session, page_a.id)
        new_slug = f"ai-cacheslug-new-{_uid()}"
        first = await self._get(client, env, slug_a, slug_b, new_slug)
        post_a.slug = new_slug
        await db_session.flush()

        second = await self._get(client, env, slug_a, slug_b, new_slug)

        assert f'/blog/{slug_a}"' in first.text
        assert f'/blog/{new_slug}"' in second.text
        assert f'/blog/{slug_a}"' not in second.text

    async def test_translation_link_change_invalidates_the_cache(self, client, db_session):
        env = await _env(db_session, "cachetrans")
        root, root_slug = await env.post("cachetransroot", "Tresc root MON271.", lang="pl", published_at=_at(2), updated_at=_OLD_UPDATE)
        english, en_slug = await env.post("cachetransen", "Tresc en MON271.", lang="en", published_at=_at(1), updated_at=_OLD_UPDATE)
        first = await self._get(client, env, root_slug, en_slug)
        post_en = await _blog_post_of(db_session, english.id)
        post_en.translation_of_id = root.id
        await db_session.flush()

        second = await self._get(client, env, root_slug, en_slug)

        assert f"{BASE}/blog/{en_slug}" not in first.text.split(blog_ai.LLMS_FULL_SEPARATOR)[1]
        assert f"{BASE}/blog/{en_slug}" in second.text.split(blog_ai.LLMS_FULL_SEPARATOR)[1]

    async def test_unpublishing_a_post_invalidates_the_cache(self, client, db_session):
        env, (page_a, slug_a), (_pb, slug_b) = await self._two_posts(db_session, "cachehide")
        first = await self._get(client, env, slug_a, slug_b)
        page_a.is_public = False
        await db_session.flush()

        second = await self._get(client, env, slug_a, slug_b)

        assert "MON271-A" in first.text
        assert "MON271-A" not in second.text
        assert "MON271-B" in second.text

    async def test_changed_size_limit_invalidates_the_cache(self, client, db_session):
        env, (_pa, slug_a), (_pb, slug_b) = await self._two_posts(db_session, "cachelimit")
        full = await self._get(client, env, slug_a, slug_b)
        small_limit = len(full.content) - 1

        with patch("monolynx.blog_ai.LLMS_FULL_MAX_BYTES", small_limit):
            limited = await self._get(client, env, slug_a, slug_b)
        back_to_full = await self._get(client, env, slug_a, slug_b)

        assert "> Truncated:" not in full.text
        assert f"(size limit {small_limit} bytes)" in limited.text
        assert back_to_full.text == full.text
