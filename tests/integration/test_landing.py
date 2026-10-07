"""Testy integracyjne strony głównej i layoutu landingu: Blog w nawigacji i sekcja najnowszych wpisów (MON-266).

Nawigacja: link `/blog/?ui={lang}` (MON-271: `ui` to wyłącznie język interfejsu, bez filtra wpisów) ma być
w nawigacji górnej, w panelu `id="mobile-menu"` i w `<footer>`
(osobne asercje na każde miejsce, bo usunięcie linku z jednego nie może przejść niezauważone), a `aria-current="page"`
tylko na stronach bloga.

Moduł Blog: `TestBlogModuleLinks` sprawdza kafel na stronie głównej (poza `<nav>`), pozycję w dropdownie Funkcjonalności
(w `<nav>` poza panelem mobilnym) i pozycję w panelu mobilnym, każde miejsce osobno.

Najnowsze wpisy: sekcja `id="latest-posts"` na `/` i nagłówek z linkami `.md` w `/index.md`, trzy najnowsze wpisy
w języku strony, bez sekcji gdy nie ma wpisów albo baza rzuca wyjątek.
"""

import logging
from datetime import UTC, datetime
from html.parser import HTMLParser
from unittest.mock import AsyncMock, patch

import pytest

from monolynx.main import _LANDING_I18N
from tests.integration.test_blog_public import _create_project, _create_user, _create_wiki_page

_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_SECTION_HEADING = {"pl": "## Najnowsze wpisy", "en": "## Latest posts"}


class _LinkCollector(HTMLParser):
    """Zbiera linki z atrybutami i przodkami (tag + id), oraz id wszystkich elementów (tag -> ids)."""

    def __init__(self) -> None:
        super().__init__()
        self._stack: list[tuple[str, str | None]] = []
        self.links: list[dict] = []
        self.ids: dict[str, set[str]] = {}

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if attr.get("id"):
            self.ids.setdefault(tag, set()).add(attr["id"])
        if tag == "a":
            self.links.append({"attrs": attr, "ancestors": list(self._stack)})
        if tag not in _VOID_TAGS:
            self._stack.append((tag, attr.get("id")))

    def handle_startendtag(self, tag, attrs):
        # `<path ... />`: element bez zawartości nie trafia na stos przodków.
        attr = dict(attrs)
        if attr.get("id"):
            self.ids.setdefault(tag, set()).add(attr["id"])

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                return


def _parse(html: str) -> _LinkCollector:
    collector = _LinkCollector()
    collector.feed(html)
    return collector


def _in_mobile_menu(link: dict) -> bool:
    return any(node_id == "mobile-menu" for _tag, node_id in link["ancestors"])


def _in_footer(link: dict) -> bool:
    return any(tag == "footer" for tag, _id in link["ancestors"])


def _in_top_nav(link: dict) -> bool:
    return any(tag == "nav" for tag, _id in link["ancestors"]) and not _in_mobile_menu(link) and not _in_footer(link)


def _blog_links(collector: _LinkCollector, lang: str, place) -> list[dict]:
    return [link for link in collector.links if link["attrs"].get("href") == f"/blog/?ui={lang}" and place(link)]


def _ui_params(path: str, lang: str) -> dict[str, str]:
    """Język interfejsu: na liście bloga `ui` (`lang` tam filtruje wpisy), na pozostałych stronach `lang`."""
    return {"ui" if path == "/blog/" else "lang": lang}


def _any_blog_link(collector: _LinkCollector) -> list[dict]:
    return [link for link in collector.links if (link["attrs"].get("href") or "").startswith("/blog")]


@pytest.fixture
def landing_enabled(monkeypatch):
    monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)


_PAGES = ["/", "/features/wiki", "/contact", "/blog/"]


@pytest.mark.integration
class TestBlogNavigationLinks:
    @pytest.mark.parametrize("lang", ["pl", "en"])
    @pytest.mark.parametrize("path", _PAGES)
    async def test_blog_link_is_in_the_top_navigation(self, client, landing_enabled, path, lang):
        response = await client.get(path, params=_ui_params(path, lang))

        assert response.status_code == 200
        assert _blog_links(_parse(response.text), lang, _in_top_nav) != []

    @pytest.mark.parametrize("lang", ["pl", "en"])
    @pytest.mark.parametrize("path", _PAGES)
    async def test_blog_link_is_in_the_mobile_menu_panel(self, client, landing_enabled, path, lang):
        response = await client.get(path, params=_ui_params(path, lang))

        assert response.status_code == 200
        collector = _parse(response.text)
        assert "mobile-menu" in collector.ids.get("div", set())
        assert _blog_links(collector, lang, _in_mobile_menu) != []

    @pytest.mark.parametrize("lang", ["pl", "en"])
    @pytest.mark.parametrize("path", _PAGES)
    async def test_blog_link_is_in_the_footer(self, client, landing_enabled, path, lang):
        response = await client.get(path, params=_ui_params(path, lang))

        assert response.status_code == 200
        assert _blog_links(_parse(response.text), lang, _in_footer) != []

    @pytest.mark.parametrize("lang", ["pl", "en"])
    @pytest.mark.parametrize("path", _PAGES)
    async def test_mobile_menu_toggle_button_exists(self, client, landing_enabled, path, lang):
        response = await client.get(path, params=_ui_params(path, lang))

        assert response.status_code == 200
        assert "mobile-menu-toggle" in _parse(response.text).ids.get("button", set())


@pytest.mark.integration
class TestBlogNavigationActiveState:
    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_blog_index_marks_top_nav_link_as_current_page(self, client, lang):
        response = await client.get("/blog/", params={"ui": lang})

        assert response.status_code == 200
        links = _blog_links(_parse(response.text), lang, _in_top_nav)
        assert links != []
        assert all(link["attrs"].get("aria-current") == "page" for link in links)

    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_blog_index_marks_mobile_menu_link_as_current_page(self, client, lang):
        response = await client.get("/blog/", params={"ui": lang})

        assert response.status_code == 200
        links = _blog_links(_parse(response.text), lang, _in_mobile_menu)
        assert links != []
        assert all(link["attrs"].get("aria-current") == "page" for link in links)

    @pytest.mark.parametrize(("name", "place"), [("topnav", _in_top_nav), ("mobile", _in_mobile_menu)])
    async def test_blog_post_page_marks_blog_link_as_current_page(self, client, db_session, name, place):
        """`/blog/<slug>` zaczyna się od `/blog/`, ale nie jest równe `/blog/` - stan aktywny dotyczy też wpisów."""
        project = await _create_project(db_session, f"land-active-{name}")
        user = await _create_user(db_session, f"land-active-{name}@test.com")
        page = await _create_wiki_page(db_session, project, user, f"Aktywny Wpis {name}", is_public=True, lang="pl")

        with patch("monolynx.services.wiki.get_markdown", return_value="Tresc wpisu."):
            response = await client.get(f"/blog/{page.slug}", params={"lang": "pl"})

        assert response.status_code == 200
        links = _blog_links(_parse(response.text), "pl", place)
        assert links != []
        assert all(link["attrs"].get("aria-current") == "page" for link in links)

    @pytest.mark.parametrize("lang", ["pl", "en"])
    @pytest.mark.parametrize("path", ["/", "/features/wiki", "/contact"])
    async def test_blog_link_is_not_current_outside_the_blog(self, client, landing_enabled, path, lang):
        response = await client.get(path, params={"lang": lang})

        assert response.status_code == 200
        links = _any_blog_link(_parse(response.text))
        assert links != []
        assert all("aria-current" not in link["attrs"] for link in links)


def _at(day: int) -> datetime:
    return datetime(2026, 3, day, 10, 0, tzinfo=UTC)


async def _seed_posts(db_session, tag: str):
    """4 opublikowane wpisy PL, 1 EN (najnowszy z wszystkich) i 1 niepubliczny PL (najnowszy).

    Kolejność wstawiania (dzień 12, 20, 5, 15) różni się zarówno od rosnącej, jak i malejącej, więc zniknięcie
    sortowania albo jego odwrócenie zmienia wynik. Wpis EN jest nowszy od wszystkich PL, żeby filtr języka
    był konieczny do jego wykluczenia z trzech najnowszych PL.
    """
    project = await _create_project(db_session, f"land-{tag}")
    user = await _create_user(db_session, f"land-{tag}@test.com")
    posts: dict[str, dict[str, str]] = {}

    async def add(key: str, title: str, day: int, *, lang: str = "pl", public: bool = True, description: str | None = None) -> None:
        slug = f"{key}-{tag}"
        await _create_wiki_page(
            db_session,
            project,
            user,
            f"{title} {tag}",
            is_public=public,
            with_post=True,
            post_slug=slug,
            published_at=_at(day),
            lang=lang,
            description=description,
        )
        posts[key] = {"slug": slug, "title": f"{title} {tag}"}

    await add("second", "PlSecondTitle", 12)
    await add("newest", "PlNewestTitle", 20, description=f"OpisNajnowszego{tag}")
    await add("oldest", "PlOldestTitle", 5)
    await add("third", "PlThirdTitle", 15)
    await add("english", "EnOnlyTitle", 25, lang="en")
    await add("hidden", "PlHiddenTitle", 28, public=False)
    return posts


def _latest_section(html: str) -> str:
    start = html.index('id="latest-posts"')
    return html[start : html.index("</section>", start)]


def _card(section: str, slug: str) -> str:
    start = section.index(f'href="/blog/{slug}?lang=')
    return section[start : section.index("</a>", start)]


def _assert_error_logged_with_traceback(caplog) -> None:
    records = [r for r in caplog.records if r.name == "monolynx" and r.levelno == logging.ERROR]
    assert len(records) == 1
    assert records[0].exc_info is not None


def _features_blog_links(collector: _LinkCollector, lang: str) -> list[dict]:
    return [link for link in collector.links if link["attrs"].get("href") == f"/features/blog?lang={lang}"]


def _blog_index_href(html: str, place) -> str:
    links = [link for link in _parse(html).links if (link["attrs"].get("href") or "").startswith("/blog/?") and place(link)]
    assert links != []
    return links[0]["attrs"]["href"]


@pytest.mark.integration
class TestLandingBlogLinkKeepsInterfaceOnly:
    """MON-271: link Blog z landingu niesie `ui`, nie `lang`, więc nie zawęża listy do języka interfejsu."""

    @pytest.mark.parametrize("lang", ["pl", "en"])
    @pytest.mark.parametrize("place", [_in_top_nav, _in_mobile_menu, _in_footer])
    async def test_blog_link_has_only_ui_parameter(self, client, landing_enabled, lang, place):
        response = await client.get("/", params={"lang": lang})

        assert _blog_index_href(response.text, place) == f"/blog/?ui={lang}"

    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_all_posts_link_in_latest_section_has_only_ui_parameter(self, client, db_session, landing_enabled, lang):
        await _seed_posts(db_session, f"latestui{lang}")

        response = await client.get("/", params={"lang": lang})

        section = _latest_section(response.text)
        assert f'href="/blog/?ui={lang}"' in section
        assert 'href="/blog/?lang=' not in section

    async def test_pl_post_is_visible_after_following_blog_link_from_en_landing(self, client, db_session, landing_enabled):
        project = await _create_project(db_session, "land-ui-enpl")
        user = await _create_user(db_session, "land-ui-enpl@test.com")
        await _create_wiki_page(
            db_session, project, user, "Polski Wpis Z Angielskiego", is_public=True, with_post=True, post_slug="pl-from-en-271", lang="pl"
        )
        landing = await client.get("/", params={"lang": "en"})
        href = _blog_index_href(landing.text, _in_top_nav)

        with patch("monolynx.services.wiki.get_markdown", return_value="Tresc."):
            response = await client.get(href)

        assert response.status_code == 200
        assert "pl-from-en-271" in response.text
        assert "Polski Wpis Z Angielskiego" in response.text

    async def test_en_post_is_visible_after_following_blog_link_from_pl_landing(self, client, db_session, landing_enabled):
        project = await _create_project(db_session, "land-ui-plen")
        user = await _create_user(db_session, "land-ui-plen@test.com")
        await _create_wiki_page(
            db_session, project, user, "English Post From Polish", is_public=True, with_post=True, post_slug="en-from-pl-271", lang="en"
        )
        landing = await client.get("/", params={"lang": "pl"})
        href = _blog_index_href(landing.text, _in_top_nav)

        with patch("monolynx.services.wiki.get_markdown", return_value="Content."):
            response = await client.get(href)

        assert response.status_code == 200
        assert "en-from-pl-271" in response.text
        assert "English Post From Polish" in response.text


@pytest.mark.integration
class TestBlogModuleLinks:
    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_landing_has_blog_tile_outside_the_navigation(self, client, landing_enabled, lang):
        response = await client.get("/", params={"lang": lang})

        assert response.status_code == 200
        tiles = [link for link in _features_blog_links(_parse(response.text), lang) if not any(tag == "nav" for tag, _id in link["ancestors"])]
        assert len(tiles) == 1
        assert _LANDING_I18N[lang]["m_blog"] in response.text

    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_features_dropdown_lists_blog(self, client, landing_enabled, lang):
        response = await client.get("/", params={"lang": lang})

        links = [link for link in _features_blog_links(_parse(response.text), lang) if _in_top_nav(link)]
        assert len(links) == 1

    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_mobile_menu_lists_blog_feature_page(self, client, landing_enabled, lang):
        response = await client.get("/", params={"lang": lang})

        links = [link for link in _features_blog_links(_parse(response.text), lang) if _in_mobile_menu(link)]
        assert len(links) == 1


@pytest.mark.integration
class TestLatestPostsSection:
    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_section_is_absent_without_any_post(self, client, landing_enabled, lang):
        response = await client.get("/", params={"lang": lang})

        assert response.status_code == 200
        assert 'id="latest-posts"' not in response.text

    async def test_pl_page_shows_three_newest_pl_posts_newest_first(self, client, db_session, landing_enabled):
        posts = await _seed_posts(db_session, "latestpl")

        response = await client.get("/", params={"lang": "pl"})

        assert response.status_code == 200
        section = _latest_section(response.text)
        newest, third, second = (posts[key] for key in ("newest", "third", "second"))
        positions = [section.index(f'href="/blog/{item["slug"]}?lang=pl"') for item in (newest, third, second)]
        assert positions == sorted(positions)
        for item in (newest, third, second):
            assert item["title"] in section
        assert section.count('href="/blog/') - section.count('href="/blog/?ui=') == 3
        for key in ("oldest", "english", "hidden"):
            assert posts[key]["slug"] not in section
            assert posts[key]["title"] not in section

    async def test_en_page_shows_only_the_en_post(self, client, db_session, landing_enabled):
        posts = await _seed_posts(db_session, "latesten")

        response = await client.get("/", params={"lang": "en"})

        assert response.status_code == 200
        section = _latest_section(response.text)
        assert f'href="/blog/{posts["english"]["slug"]}?lang=en"' in section
        assert posts["english"]["title"] in section
        for key in ("newest", "second", "third", "oldest", "hidden"):
            assert posts[key]["slug"] not in section
            assert posts[key]["title"] not in section

    async def test_section_is_absent_when_no_post_has_the_page_language(self, client, db_session, landing_enabled):
        project = await _create_project(db_session, "land-onlyen")
        user = await _create_user(db_session, "land-onlyen@test.com")
        await _create_wiki_page(
            db_session, project, user, "Wylacznie Angielski Wpis", is_public=True, with_post=True, post_slug="only-en-266", lang="en"
        )

        response = await client.get("/", params={"lang": "pl"})

        assert response.status_code == 200
        assert 'id="latest-posts"' not in response.text

    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_section_has_localized_title_and_link_to_all_posts(self, client, db_session, landing_enabled, lang):
        await _seed_posts(db_session, f"latesttexts{lang}")

        response = await client.get("/", params={"lang": lang})

        assert response.status_code == 200
        section = _latest_section(response.text)
        assert _LANDING_I18N[lang]["blog_latest_title"] in section
        assert f'href="/blog/?ui={lang}"' in section
        assert _LANDING_I18N[lang]["blog_latest_all"] in section

    async def test_database_error_leaves_landing_without_the_section(self, client, db_session, landing_enabled, caplog):
        await _seed_posts(db_session, "latestdberr")

        with (
            patch("monolynx.main.list_public_posts", AsyncMock(side_effect=RuntimeError("db down"))) as fake,
            caplog.at_level(logging.ERROR, logger="monolynx"),
        ):
            response = await client.get("/", params={"lang": "pl"})

        assert response.status_code == 200
        assert 'id="latest-posts"' not in response.text
        fake.assert_awaited_once()
        _assert_error_logged_with_traceback(caplog)

    async def test_card_shows_publication_date_and_description_only_when_present(self, client, db_session, landing_enabled):
        posts = await _seed_posts(db_session, "latestcard")

        response = await client.get("/", params={"lang": "pl"})

        section = _latest_section(response.text)
        newest = _card(section, posts["newest"]["slug"])
        assert '<time datetime="2026-03-20' in newest
        assert ">2026-03-20</time>" in newest
        assert "OpisNajnowszegolatestcard" in newest
        assert "<p" in newest
        third = _card(section, posts["third"]["slug"])
        assert '<time datetime="2026-03-15' in third
        assert "<p" not in third

    async def test_card_date_falls_back_to_page_creation_when_post_has_no_publication_date(self, client, db_session, landing_enabled):
        project = await _create_project(db_session, "land-datefallback")
        user = await _create_user(db_session, "land-datefallback@test.com")
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Bez Daty Publikacji",
            is_public=True,
            with_post=True,
            post_slug="no-published-at-266",
            published_at=None,
            created_at=datetime(2026, 2, 2, 9, 0, tzinfo=UTC),
            lang="pl",
        )

        response = await client.get("/", params={"lang": "pl"})

        card = _card(_latest_section(response.text), "no-published-at-266")
        assert '<time datetime="2026-02-02' in card
        assert ">2026-02-02</time>" in card

    async def test_skip_landing_page_redirects_before_querying_posts(self, client, monkeypatch):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", True)

        with patch("monolynx.main.list_public_posts", AsyncMock(side_effect=RuntimeError("db down"))) as fake:
            response = await client.get("/", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/auth/login"
        fake.assert_not_awaited()


@pytest.mark.integration
class TestIndexMarkdownLatestPosts:
    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_heading_is_absent_without_any_post(self, client, lang):
        response = await client.get("/index.md", params={"lang": lang})

        assert response.status_code == 200
        assert _SECTION_HEADING[lang] not in response.text

    async def test_pl_lists_three_newest_pl_posts_as_markdown_links_in_order(self, client, db_session):
        posts = await _seed_posts(db_session, "mdpl")

        response = await client.get("/index.md", params={"lang": "pl"})

        assert response.status_code == 200
        text = response.text
        assert _SECTION_HEADING["pl"] in text
        section = text[text.index(_SECTION_HEADING["pl"]) :]
        lines = [f"- [{posts[key]['title']}](https://monolynx.com/blog/{posts[key]['slug']}.md)" for key in ("newest", "third", "second")]
        positions = [section.index(line) for line in lines]
        assert positions == sorted(positions)
        for key in ("oldest", "english", "hidden"):
            assert posts[key]["slug"] not in text
            assert posts[key]["title"] not in text

    async def test_link_text_of_a_title_with_brackets_backslash_and_spaces_is_escaped(self, client, db_session):
        project = await _create_project(db_session, "land-mdescape")
        user = await _create_user(db_session, "land-mdescape@test.com")
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Tytul [beta]  z\\ukosnikiem",
            is_public=True,
            with_post=True,
            post_slug="escape-266",
            published_at=_at(10),
            lang="pl",
        )

        response = await client.get("/index.md", params={"lang": "pl"})

        assert "- [Tytul \\[beta\\] z\\\\ukosnikiem](https://monolynx.com/blog/escape-266.md)" in response.text

    async def test_en_lists_only_the_en_post(self, client, db_session):
        posts = await _seed_posts(db_session, "mden")

        response = await client.get("/index.md", params={"lang": "en"})

        assert response.status_code == 200
        text = response.text
        assert _SECTION_HEADING["en"] in text
        assert f"- [{posts['english']['title']}](https://monolynx.com/blog/{posts['english']['slug']}.md)" in text
        for key in ("newest", "second", "third", "oldest", "hidden"):
            assert posts[key]["slug"] not in text

    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_database_error_leaves_index_markdown_without_the_section(self, client, db_session, lang, caplog):
        await _seed_posts(db_session, f"mderr{lang}")

        with (
            patch("monolynx.main.list_public_posts", AsyncMock(side_effect=RuntimeError("db down"))) as fake,
            caplog.at_level(logging.ERROR, logger="monolynx"),
        ):
            response = await client.get("/index.md", params={"lang": lang})

        _assert_error_logged_with_traceback(caplog)
        assert response.status_code == 200
        assert _SECTION_HEADING[lang] not in response.text
        assert "# Monolynx" in response.text
        fake.assert_awaited_once()
