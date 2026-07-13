"""Testy dla src/monolynx/features.py (tresc stron landing/feature) i endpointow /features/*."""

import pytest

from monolynx.features import (
    _FEATURES,
    _other_modules,
    feature_markdown,
    get_feature_content,
)

ALL_SLUGS = list(_FEATURES.keys())


@pytest.mark.unit
class TestOtherModules:
    def test_returns_nine_modules_excluding_current(self):
        result = _other_modules("scrum", "pl")

        assert len(result) == len(ALL_SLUGS) - 1
        assert all(m["slug"] != "scrum" for m in result)

    def test_each_entry_has_required_keys(self):
        result = _other_modules("500ki", "en")

        for module in result:
            assert set(module.keys()) == {"slug", "color", "name", "short"}

    def test_unknown_exclude_returns_all_modules(self):
        result = _other_modules("nonexistent-slug", "pl")

        assert len(result) == len(ALL_SLUGS)

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_lang_variants_produce_same_slugs(self, lang):
        result = _other_modules("wiki", lang)

        slugs = {m["slug"] for m in result}
        assert slugs == set(ALL_SLUGS) - {"wiki"}


@pytest.mark.unit
class TestGetFeatureContent:
    @pytest.mark.parametrize("slug", ALL_SLUGS)
    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_returns_content_dict_for_every_module_and_lang(self, slug, lang):
        content = get_feature_content(slug, lang)

        assert content is not None
        assert isinstance(content, dict)
        assert "title" in content
        assert "headline" in content
        assert "description" in content
        assert "other_modules" in content

    def test_returns_none_for_unknown_slug(self):
        assert get_feature_content("does-not-exist", "pl") is None
        assert get_feature_content("does-not-exist", "en") is None

    @pytest.mark.parametrize("slug", ALL_SLUGS)
    def test_pl_and_en_have_matching_top_level_keys(self, slug):
        """Regression guard: pominiecie galezi jednego jezyka w builderze to czesty bug."""
        content_pl = get_feature_content(slug, "pl")
        content_en = get_feature_content(slug, "en")

        assert content_pl is not None
        assert content_en is not None
        assert set(content_pl.keys()) == set(content_en.keys())

    @pytest.mark.parametrize("slug", ALL_SLUGS)
    def test_pl_and_en_have_same_mcp_tools_count(self, slug):
        content_pl = get_feature_content(slug, "pl")
        content_en = get_feature_content(slug, "en")

        assert content_pl is not None
        assert content_en is not None
        assert len(content_pl.get("mcp_tools", [])) == len(content_en.get("mcp_tools", []))

    @pytest.mark.parametrize("slug", ALL_SLUGS)
    def test_pl_and_en_have_same_features_count(self, slug):
        content_pl = get_feature_content(slug, "pl")
        content_en = get_feature_content(slug, "en")

        assert content_pl is not None
        assert content_en is not None
        assert len(content_pl.get("features", [])) == len(content_en.get("features", []))

    @pytest.mark.parametrize("slug", ALL_SLUGS)
    def test_other_modules_excludes_self_for_every_module(self, slug):
        content = get_feature_content(slug, "pl")

        assert content is not None
        assert all(m["slug"] != slug for m in content["other_modules"])


@pytest.mark.unit
class TestFeatureMarkdown:
    @pytest.mark.parametrize("slug", ALL_SLUGS)
    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_renders_markdown_with_title_and_sections(self, slug, lang):
        md = feature_markdown(slug, lang)

        assert md is not None
        content = get_feature_content(slug, lang)
        assert content is not None
        assert f"# {content['title']}" in md
        assert content["headline"] in md
        assert md.endswith("\n")
        assert not md.endswith("\n\n")

    def test_returns_none_for_unknown_slug(self):
        assert feature_markdown("does-not-exist", "pl") is None

    def test_includes_features_section_when_present(self):
        md = feature_markdown("scrum", "pl")

        assert md is not None
        assert "## Funkcje" in md
        assert "- **Tablica Kanban**:" in md

    def test_includes_features_section_en(self):
        md = feature_markdown("scrum", "en")

        assert md is not None
        assert "## Features" in md
        assert "- **Kanban board**:" in md

    def test_includes_steps_section(self):
        md = feature_markdown("monitoring", "pl")

        assert md is not None
        assert "## Jak to działa" in md
        assert "1. **" in md

    def test_includes_mcp_tools_section(self):
        md = feature_markdown("wiki", "pl")

        assert md is not None
        assert "## AI i MCP" in md
        assert "- `list_wiki_pages`:" in md

    def test_includes_tech_details_section(self):
        md = feature_markdown("500ki", "en")

        assert md is not None
        assert "## Technical details" in md
        assert "- **Fingerprinting**:" in md

    def test_omits_screenshot_only_none_sections_gracefully(self):
        """reports/pl ma screenshot_2=None ale ma features/steps -- markdown musi sie wygenerowac bez bledu."""
        md = feature_markdown("reports", "pl")

        assert md is not None
        assert "## Funkcje" in md


@pytest.mark.integration
class TestFeatureHttpEndpoints:
    @pytest.mark.parametrize("slug", ALL_SLUGS)
    async def test_feature_markdown_endpoint_returns_content(self, client, slug):
        response = await client.get(f"/features/{slug}.md", params={"lang": "pl"})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        content = get_feature_content(slug, "pl")
        assert content is not None
        assert content["title"] in response.text

    async def test_feature_markdown_endpoint_defaults_to_en(self, client):
        response = await client.get("/features/scrum.md")

        assert response.status_code == 200
        content = get_feature_content("scrum", "en")
        assert content is not None
        assert content["title"] in response.text

    async def test_feature_markdown_endpoint_unknown_slug_404(self, client):
        response = await client.get("/features/does-not-exist.md")

        assert response.status_code == 404

    async def test_feature_markdown_endpoint_invalid_lang_falls_back_to_en(self, client):
        response = await client.get("/features/scrum.md", params={"lang": "de"})

        assert response.status_code == 200
        content = get_feature_content("scrum", "en")
        assert content is not None
        assert content["title"] in response.text

    async def test_feature_page_redirects_when_skip_landing_page_enabled(self, client, monkeypatch):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", True)

        response = await client.get("/features/scrum", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/auth/login"

    async def test_feature_page_renders_when_skip_landing_page_disabled(self, client, monkeypatch):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)

        response = await client.get("/features/scrum", params={"lang": "pl"})

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    async def test_feature_page_unknown_slug_404_when_landing_enabled(self, client, monkeypatch):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)

        response = await client.get("/features/does-not-exist")

        assert response.status_code == 404

    async def test_feature_page_invalid_lang_falls_back_to_en(self, client, monkeypatch):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)

        response = await client.get("/features/scrum", params={"lang": "xx"})

        assert response.status_code == 200
