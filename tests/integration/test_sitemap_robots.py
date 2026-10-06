"""Testy integracyjne sitemap.xml z bazy (wpisy bloga) i robots.txt (jawne Allow: /blog/).

MON-254: adresy wpisów pochodzą z `BlogPost.slug` (nie ze sluga strony wiki), a `lastmod`
z `BlogPost.last_verified_at`, potem `BlogPost.published_at`, potem `WikiPage.updated_at`.
Sitemap zawiera też stronę listy `{base}/blog/`.

MON-259: pary `xhtml:link hreflang` dla publicznych tłumaczeń (klasa `TestSitemapHreflang`) oraz osobne
grupy `User-agent` dla botów AI w robots.txt (klasa `TestRobotsAiBots`).

MON-265: trasa `/blog-authoring.md` (przewodnik pisania wpisów) i jej adres w sitemap (klasa `TestBlogAuthoringGuide`).
"""

import secrets
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from monolynx.config import settings
from monolynx.features import feature_slugs
from monolynx.models.blog_post import BlogPost
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.services.auth import hash_password

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


# ---------------------------------------------------------------------------
# Helpers lokalne (konwencja z tests/integration/test_blog_public.py)
# ---------------------------------------------------------------------------


async def _create_project(db_session, slug, code=None):
    """Helper: tworzy projekt z unikalnym kodem."""
    if code is None:
        code = "S" + secrets.token_hex(4).upper()
    project = Project(
        name=f"Sitemap {slug}",
        slug=slug,
        code=code,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _create_user(db_session, email):
    """Helper: tworzy uzytkownika w DB."""
    user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=False)
    db_session.add(user)
    await db_session.flush()
    return user


async def _create_wiki_page(
    db_session,
    project,
    user,
    title,
    is_public=False,
    updated_at=None,
    *,
    with_post=None,
    post_slug=None,
    published_at=None,
    last_verified_at=None,
    lang="pl",
    translation_of_id=None,
):
    """Helper: tworzy strone wiki (bez realnego MinIO) i jej BlogPost.

    lang / translation_of_id: jezyk wpisu i `page_id` wpisu-oryginalu (grupa tlumaczen sitemapy, MON-259).

    updated_at: jawne przypisanie PRZED pierwszym flush (INSERT) nadpisuje
    server_default -- potrzebne do deterministycznego lastmod w testach sitemap.
    with_post: domyslnie BlogPost powstaje dla stron publicznych; True wymusza go tez dla
    prywatnej (wpis cofniety z publikacji).
    """
    page = WikiPage(
        project_id=project.id,
        title=title,
        slug=title.lower().replace(" ", "-"),
        position=0,
        minio_path=f"{project.slug}/{uuid.uuid4()}.md",
        is_ai_touched=False,
        is_public=is_public,
        created_by_id=user.id,
        last_edited_by_id=user.id,
    )
    if updated_at is not None:
        page.updated_at = updated_at
    db_session.add(page)
    await db_session.flush()
    if with_post if with_post is not None else is_public:
        post = BlogPost(
            page_id=page.id,
            slug=post_slug or page.slug,
            published_at=published_at,
            last_verified_at=last_verified_at,
            lang=lang,
            translation_of_id=translation_of_id,
        )
        db_session.add(post)
        await db_session.flush()
    return page


def _sitemap_entries(xml_text: str) -> dict[str, str | None]:
    """Sparsuj sitemap.xml (walidacja poprawnosci XML -- rzuca ParseError jesli zle
    sformowany) i zwroc mape loc -> lastmod (None jesli wpis nie ma lastmod)."""
    root = ET.fromstring(xml_text)
    entries: dict[str, str | None] = {}
    for url_el in root.findall("sm:url", _SITEMAP_NS):
        loc_el = url_el.find("sm:loc", _SITEMAP_NS)
        lastmod_el = url_el.find("sm:lastmod", _SITEMAP_NS)
        if loc_el is not None and loc_el.text:
            entries[loc_el.text] = lastmod_el.text if lastmod_el is not None else None
    return entries


# ---------------------------------------------------------------------------
# 1. sitemap.xml -- wpisy publiczne z lastmod (kryterium 4), bez prywatnych (kryterium 5)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSitemapPublicWikiPages:
    """sitemap.xml zawiera publiczne wpisy z <lastmod> i pomija prywatne."""

    async def test_sitemap_is_well_formed_xml(self, client):
        """Regresja: sitemap.xml pozostaje poprawnym XML (np. slug z & musialby byc
        escapowany) niezaleznie od tego ile wpisow z bazy do niego dopiszemy."""
        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        ET.fromstring(resp.text)

    async def test_sitemap_includes_blog_index(self, client):
        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        base = settings.APP_URL.rstrip("/")
        assert f"{base}/blog/" in _sitemap_entries(resp.text)

    async def test_sitemap_includes_public_page_under_post_slug_not_page_slug(self, client, db_session):
        project = await _create_project(db_session, "sitemap-pub")
        user = await _create_user(db_session, "sitemap-pub@test.com")
        page = await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Sitemap Publiczny MON254",
            is_public=True,
            post_slug="adres-wpisu-w-sitemap-mon254",
            published_at=datetime(2026, 2, 1, 8, 0, tzinfo=UTC),
        )

        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        base = settings.APP_URL.rstrip("/")
        entries = _sitemap_entries(resp.text)
        assert f"{base}/blog/adres-wpisu-w-sitemap-mon254" in entries
        assert f"{base}/blog/{page.slug}" not in entries

    async def test_sitemap_lastmod_is_published_at_when_no_last_verified_at(self, client, db_session):
        project = await _create_project(db_session, "sitemap-lm-pub")
        user = await _create_user(db_session, "sitemap-lm-pub@test.com")
        published = datetime(2026, 2, 1, 8, 0, tzinfo=UTC)
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Lastmod Published MON254",
            is_public=True,
            post_slug="lastmod-published-mon254",
            published_at=published,
            updated_at=datetime(2026, 5, 5, 5, 0, tzinfo=UTC),
        )

        resp = await client.get("/sitemap.xml")

        base = settings.APP_URL.rstrip("/")
        assert _sitemap_entries(resp.text)[f"{base}/blog/lastmod-published-mon254"] == published.isoformat()

    async def test_sitemap_lastmod_prefers_last_verified_at(self, client, db_session):
        project = await _create_project(db_session, "sitemap-lm-ver")
        user = await _create_user(db_session, "sitemap-lm-ver@test.com")
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Lastmod Verified MON254",
            is_public=True,
            post_slug="lastmod-verified-mon254",
            published_at=datetime(2026, 2, 1, 8, 0, tzinfo=UTC),
            last_verified_at=date(2026, 3, 9),
            updated_at=datetime(2026, 5, 5, 5, 0, tzinfo=UTC),
        )

        resp = await client.get("/sitemap.xml")

        base = settings.APP_URL.rstrip("/")
        assert _sitemap_entries(resp.text)[f"{base}/blog/lastmod-verified-mon254"] == date(2026, 3, 9).isoformat()

    async def test_sitemap_lastmod_falls_back_to_page_updated_at(self, client, db_session):
        project = await _create_project(db_session, "sitemap-lm-upd")
        user = await _create_user(db_session, "sitemap-lm-upd@test.com")
        updated = datetime(2026, 5, 5, 5, 0, tzinfo=UTC)
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Lastmod Updated MON254",
            is_public=True,
            post_slug="lastmod-updated-mon254",
            updated_at=updated,
        )

        resp = await client.get("/sitemap.xml")

        base = settings.APP_URL.rstrip("/")
        assert _sitemap_entries(resp.text)[f"{base}/blog/lastmod-updated-mon254"] == updated.isoformat()

    async def test_sitemap_excludes_private_page(self, client, db_session):
        """Kryterium 5: strona nieoznaczona is_public nie pojawia sie w sitemap.xml."""
        project = await _create_project(db_session, "sitemap-priv")
        user = await _create_user(db_session, "sitemap-priv@test.com")
        page = await _create_wiki_page(db_session, project, user, "Wpis Sitemap Prywatny MON202", is_public=False)

        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        base = settings.APP_URL.rstrip("/")
        entries = _sitemap_entries(resp.text)
        assert f"{base}/blog/{page.slug}" not in entries
        assert "Wpis Sitemap Prywatny MON202" not in resp.text

    async def test_sitemap_excludes_private_page_that_has_blog_post(self, client, db_session):
        """Wpis cofniety z publikacji zachowuje BlogPost, ale nie moze trafic do sitemapy (ani pod slugiem, ani starym)."""
        project = await _create_project(db_session, "sitemap-priv-post")
        user = await _create_user(db_session, "sitemap-priv-post@test.com")
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Cofniety MON254",
            is_public=False,
            with_post=True,
            post_slug="wpis-cofniety-adres-mon254",
            published_at=datetime(2026, 2, 1, 8, 0, tzinfo=UTC),
        )

        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        assert "wpis-cofniety-adres-mon254" not in resp.text


# ---------------------------------------------------------------------------
# 2. sitemap.xml -- regresja: wszystkie dotychczasowe adresy /features/* zostaja
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSitemapStillContainsFeatureUrls:
    """Regresja: sitemap.xml zawiera nadal WSZYSTKIE dotychczasowe adresy /features/*
    (kryterium 4, druga polowa) -- wpisy z bazy sa DOPISYWANE obok, nie zastepuja."""

    async def test_all_feature_slugs_present_in_both_languages(self, client, db_session):
        project = await _create_project(db_session, "sitemap-features")
        user = await _create_user(db_session, "sitemap-features@test.com")
        await _create_wiki_page(db_session, project, user, "Wpis Obok Features MON202", is_public=True)

        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        base = settings.APP_URL.rstrip("/")
        entries = _sitemap_entries(resp.text)
        slugs = feature_slugs()
        assert slugs, "feature_slugs() nie powinno byc puste -- inaczej test nic nie sprawdza"
        for slug in slugs:
            assert f"{base}/features/{slug}?lang=en" in entries
            assert f"{base}/features/{slug}?lang=pl" in entries

    async def test_blog_feature_module_present_in_both_languages(self, client):
        """MON-266: moduł Blog ma własny adres /features/blog w sitemapie (kryterium 76b76968)."""
        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        base = settings.APP_URL.rstrip("/")
        entries = _sitemap_entries(resp.text)
        assert f"{base}/features/blog?lang=en" in entries
        assert f"{base}/features/blog?lang=pl" in entries


# ---------------------------------------------------------------------------
# 3. robots.txt -- jawny Allow: /blog/, dotychczasowe Disallow zostaja (kryterium 6)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRobotsAllowsBlog:
    """robots.txt pozwala na /blog/ i nadal blokuje /dashboard/, /auth/, /api/, /mcp."""

    async def test_robots_has_explicit_allow_blog(self, client):
        resp = await client.get("/robots.txt")

        assert resp.status_code == 200
        assert "Allow: /blog/" in resp.text

    async def test_robots_still_blocks_protected_paths(self, client):
        """Regresja: dotychczasowe linie Disallow nie zostaly usuniete przy dodawaniu
        jawnego Allow: /blog/."""
        resp = await client.get("/robots.txt")

        assert resp.status_code == 200
        assert "Disallow: /dashboard/" in resp.text
        assert "Disallow: /auth/" in resp.text
        assert "Disallow: /api/" in resp.text
        assert "Disallow: /mcp" in resp.text


# ---------------------------------------------------------------------------
# 4. sitemap.xml -- pary hreflang dla tlumaczen (MON-259, kryterium 6)
# ---------------------------------------------------------------------------

_XHTML_NS = "http://www.w3.org/1999/xhtml"
_XHTML_LINK = f"{{{_XHTML_NS}}}link"


def _sitemap_alternates(xml_text: str) -> dict[str, list[tuple[str, str, str]]]:
    """Sparsuj sitemap.xml -> mapa loc -> lista (rel, hreflang, href) z elementow xhtml:link danego <url>."""
    root = ET.fromstring(xml_text)
    result: dict[str, list[tuple[str, str, str]]] = {}
    for url_el in root.findall("sm:url", _SITEMAP_NS):
        loc = url_el.findtext("sm:loc", namespaces=_SITEMAP_NS)
        result[loc] = [(link.get("rel"), link.get("hreflang"), link.get("href")) for link in url_el.findall(_XHTML_LINK)]
    return result


@pytest.mark.integration
class TestSitemapHreflang:
    async def test_urlset_declares_the_xhtml_namespace(self, client):
        resp = await client.get("/sitemap.xml")

        assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in resp.text
        ET.fromstring(resp.text)

    async def test_each_url_of_a_translation_group_lists_every_version_including_itself(self, client, db_session):
        project = await _create_project(db_session, "sitemap-hl")
        user = await _create_user(db_session, "sitemap-hl@test.com")
        root = await _create_wiki_page(db_session, project, user, "Wpis Hreflang Pl MON259", is_public=True, post_slug="hl-pl-mon259", lang="pl")
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Hreflang En MON259",
            is_public=True,
            post_slug="hl-en-mon259",
            lang="en",
            translation_of_id=root.id,
        )

        resp = await client.get("/sitemap.xml")

        base = settings.APP_URL.rstrip("/")
        alternates = _sitemap_alternates(resp.text)
        expected = [
            ("alternate", "en", f"{base}/blog/hl-en-mon259"),
            ("alternate", "pl", f"{base}/blog/hl-pl-mon259"),
        ]
        assert sorted(alternates[f"{base}/blog/hl-pl-mon259"]) == expected
        assert sorted(alternates[f"{base}/blog/hl-en-mon259"]) == expected

    async def test_loc_and_lastmod_stay_unchanged_for_posts_with_translations(self, client, db_session):
        project = await _create_project(db_session, "sitemap-hl-lm")
        user = await _create_user(db_session, "sitemap-hl-lm@test.com")
        published = datetime(2026, 2, 1, 8, 0, tzinfo=UTC)
        root = await _create_wiki_page(
            db_session, project, user, "Wpis Hreflang Lm Pl MON259", is_public=True, post_slug="hl-lm-pl-mon259", published_at=published
        )
        await _create_wiki_page(
            db_session, project, user, "Wpis Hreflang Lm En MON259", is_public=True, post_slug="hl-lm-en-mon259", lang="en", translation_of_id=root.id
        )

        resp = await client.get("/sitemap.xml")

        base = settings.APP_URL.rstrip("/")
        assert _sitemap_entries(resp.text)[f"{base}/blog/hl-lm-pl-mon259"] == published.isoformat()

    async def test_post_without_translation_has_no_xhtml_link(self, client, db_session):
        project = await _create_project(db_session, "sitemap-hl-solo")
        user = await _create_user(db_session, "sitemap-hl-solo@test.com")
        await _create_wiki_page(db_session, project, user, "Wpis Bez Tlumaczenia MON259", is_public=True, post_slug="hl-solo-mon259")

        resp = await client.get("/sitemap.xml")

        base = settings.APP_URL.rstrip("/")
        assert _sitemap_alternates(resp.text)[f"{base}/blog/hl-solo-mon259"] == []

    async def test_translation_group_does_not_leak_into_an_unrelated_post(self, client, db_session):
        project = await _create_project(db_session, "sitemap-hl-iso")
        user = await _create_user(db_session, "sitemap-hl-iso@test.com")
        root = await _create_wiki_page(db_session, project, user, "Wpis Grupa Pl MON259", is_public=True, post_slug="hl-iso-pl-mon259")
        await _create_wiki_page(
            db_session, project, user, "Wpis Grupa En MON259", is_public=True, post_slug="hl-iso-en-mon259", lang="en", translation_of_id=root.id
        )
        await _create_wiki_page(db_session, project, user, "Wpis Obcy MON259", is_public=True, post_slug="hl-iso-other-mon259")

        resp = await client.get("/sitemap.xml")

        base = settings.APP_URL.rstrip("/")
        alternates = _sitemap_alternates(resp.text)
        assert alternates[f"{base}/blog/hl-iso-other-mon259"] == []
        assert len(alternates[f"{base}/blog/hl-iso-pl-mon259"]) == 2

    async def test_private_translation_gives_no_xhtml_link_and_does_not_appear(self, client, db_session):
        project = await _create_project(db_session, "sitemap-hl-priv")
        user = await _create_user(db_session, "sitemap-hl-priv@test.com")
        root = await _create_wiki_page(db_session, project, user, "Wpis Hl Pub Pl MON259", is_public=True, post_slug="hl-priv-pl-mon259")
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Hl Priv En MON259",
            is_public=False,
            with_post=True,
            post_slug="hl-priv-en-mon259",
            lang="en",
            translation_of_id=root.id,
        )

        resp = await client.get("/sitemap.xml")

        base = settings.APP_URL.rstrip("/")
        assert _sitemap_alternates(resp.text)[f"{base}/blog/hl-priv-pl-mon259"] == []
        assert "hl-priv-en-mon259" not in resp.text

    async def test_private_translation_is_left_out_of_a_group_with_a_public_translation(self, client, db_session):
        project = await _create_project(db_session, "sitemap-hl-mix")
        user = await _create_user(db_session, "sitemap-hl-mix@test.com")
        root = await _create_wiki_page(db_session, project, user, "Wpis Hl Mix Pl MON259", is_public=True, post_slug="hl-mix-pl-mon259")
        await _create_wiki_page(
            db_session, project, user, "Wpis Hl Mix En MON259", is_public=True, post_slug="hl-mix-en-mon259", lang="en", translation_of_id=root.id
        )
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Hl Mix Ukryty MON259",
            is_public=False,
            with_post=True,
            post_slug="hl-mix-hidden-mon259",
            lang="en",
            translation_of_id=root.id,
        )

        resp = await client.get("/sitemap.xml")

        base = settings.APP_URL.rstrip("/")
        hrefs = {href for _rel, _lang, href in _sitemap_alternates(resp.text)[f"{base}/blog/hl-mix-pl-mon259"]}
        assert hrefs == {f"{base}/blog/hl-mix-pl-mon259", f"{base}/blog/hl-mix-en-mon259"}
        assert "hl-mix-hidden-mon259" not in resp.text


# ---------------------------------------------------------------------------
# 5. robots.txt -- grupy dla botow AI (MON-259, kryterium 5)
# ---------------------------------------------------------------------------

_AI_BOTS = ["GPTBot", "ClaudeBot", "Claude-User", "PerplexityBot", "Google-Extended", "CCBot"]
_DISALLOWED = ["/dashboard/", "/auth/", "/api/", "/mcp"]


def _robots_groups(text: str) -> list[tuple[list[str], list[tuple[str, str]]]]:
    """robots.txt -> lista grup (user-agenty, reguly Allow/Disallow); komentarze i Sitemap poza grupami.

    Nowa grupa zaczyna sie od `User-agent` po jakiejkolwiek regule (kolejne `User-agent` pod rzad to jedna grupa, RFC 9309).
    """
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, value = (part.strip() for part in line.split(":", 1))
        key = name.lower()
        if key == "user-agent":
            if rules:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value)
        elif key in ("allow", "disallow"):
            rules.append((name.capitalize(), value))
    if agents:
        groups.append((agents, rules))
    return groups


@pytest.mark.integration
class TestRobotsAiBots:
    @pytest.mark.parametrize("bot", _AI_BOTS)
    async def test_bot_has_its_own_group_with_blog_allowed_and_private_paths_blocked(self, bot, client):
        resp = await client.get("/robots.txt")

        groups = [rules for agents, rules in _robots_groups(resp.text) if agents == [bot]]
        assert len(groups) == 1
        rules = groups[0]
        assert ("Allow", "/blog/") in rules
        assert sorted(value for name, value in rules if name == "Disallow") == sorted(_DISALLOWED)
        assert len(rules) == 5

    async def test_every_bot_group_is_separate_from_the_wildcard_group(self, client):
        resp = await client.get("/robots.txt")

        all_agents = [agent for agents, _rules in _robots_groups(resp.text) for agent in agents]
        assert all_agents.count("*") == 1
        for bot in _AI_BOTS:
            assert all_agents.count(bot) == 1

    async def test_wildcard_group_keeps_its_rules_in_the_original_order(self, client):
        resp = await client.get("/robots.txt")

        wildcard = next(rules for agents, rules in _robots_groups(resp.text) if agents == ["*"])
        assert wildcard == [
            ("Allow", "/"),
            ("Allow", "/blog/"),
            ("Disallow", "/dashboard/"),
            ("Disallow", "/auth/"),
            ("Disallow", "/api/"),
            ("Disallow", "/mcp"),
        ]

    async def test_robots_points_at_sitemap_llms_txt_and_blog_feed(self, client):
        resp = await client.get("/robots.txt")

        base = settings.APP_URL.rstrip("/")
        assert f"Sitemap: {base}/sitemap.xml" in resp.text.splitlines()
        assert f"{base}/llms.txt" in resp.text
        assert f"{base}/blog/feed.xml" in resp.text


# ---------------------------------------------------------------------------
# 6. /blog-authoring.md -- publiczny przewodnik pisania wpisow (MON-265)
# ---------------------------------------------------------------------------

_BLOG_GUIDE_PATH = Path(__file__).resolve().parents[2] / "src" / "monolynx" / "static" / "blog-authoring.md"


@pytest.mark.integration
class TestBlogAuthoringGuide:
    async def test_guide_returns_markdown_with_the_file_content(self, client):
        resp = await client.get("/blog-authoring.md")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/markdown")
        assert resp.text == _BLOG_GUIDE_PATH.read_text(encoding="utf-8")
        assert "## Block catalog" in resp.text

    async def test_guide_returns_404_when_file_is_missing(self, client):
        with patch.object(Path, "is_file", return_value=False):
            resp = await client.get("/blog-authoring.md")

        assert resp.status_code == 404

    async def test_sitemap_lists_the_guide(self, client):
        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        base = settings.APP_URL.rstrip("/")
        assert f"{base}/blog-authoring.md" in _sitemap_entries(resp.text)
