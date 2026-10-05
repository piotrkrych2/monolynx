"""Testy warstwy AI bloga (MON-258): bliźniak markdown `/blog/{slug}.md`, `/blog/index.md`,
negocjacja `Accept: text/markdown`, nagłówek `Link`, JSON-LD i cache warunkowy (ETag, 304).

Wpisy powstają bezpośrednio w DB (helpery z `test_blog_public.py`), a treść z MinIO jest
podstawiana przez `monolynx.services.wiki.get_markdown` -- `get_page_content()` rozwiązuje
tę nazwę w globalsach modułu wiki, więc patch działa niezależnie od tego, gdzie nowy kod
importuje `get_page_content`.

Funkcje czyste `absolutize_markdown_urls` i `conditional_response` mają testy bez bazy
(klasy `TestAbsolutizeMarkdownUrls`, `TestConditionalResponse`).
"""

import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock, patch
from urllib.parse import quote, urlsplit

import pytest
import yaml
from starlette.requests import Request

from monolynx.blog_ai import absolutize_markdown_urls, conditional_response, wants_markdown
from monolynx.config import settings
from monolynx.services import blog as blog_service
from monolynx.services.blog import DEFAULT_AUTHOR_NAME
from monolynx.services.blog import list_public_posts as real_list_public_posts
from tests.integration.test_blog_public import _add_owner, _create_attachment, _create_project, _create_user, _create_wiki_page

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
        _page, slug = await env.post("wcc", ":::faq\nalfa beta\n:::")

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
        _page, slug = await env.post("ldwc", "Jeden dwa trzy.\n\n:::faq\nczte ry\n:::")

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
