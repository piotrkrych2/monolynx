"""Testy dla src/monolynx/features.py (tresc stron landing/feature) i endpointow /features/*."""

import pytest

from monolynx.features import (
    _FEATURES,
    _ICON_CLI,
    _other_modules,
    feature_markdown,
    get_feature_content,
)
from monolynx.main import _LANDING_I18N

ALL_SLUGS = list(_FEATURES.keys())

# Zrodlo: cli/src/monolynx_cli/commands/*.py. Wpis mcp_tools modulu CLI musi byc jedna z tych komend.
ALLOWED_CLI_COMMANDS = {
    "monolynx project list",
    "monolynx project get",
    "monolynx project create",
    "monolynx project update",
    "monolynx project delete",
    "monolynx project summary",
    "monolynx member list",
    "monolynx member invite",
    "monolynx member remove",
    "monolynx ticket list",
    "monolynx ticket search",
    "monolynx ticket get",
    "monolynx ticket create",
    "monolynx ticket update",
    "monolynx ticket delete",
    "monolynx ticket bulk-update",
    "monolynx ticket comment list",
    "monolynx ticket comment add",
    "monolynx ticket label list",
    "monolynx ticket label create",
    "monolynx ticket ac list",
    "monolynx ticket ac add",
    "monolynx ticket ac update",
    "monolynx ticket ac delete",
    "monolynx sprint list",
    "monolynx sprint get",
    "monolynx sprint create",
    "monolynx sprint update",
    "monolynx sprint start",
    "monolynx sprint complete",
    "monolynx sprint board",
    "monolynx sprint burndown",
    "monolynx wiki list",
    "monolynx wiki get",
    "monolynx wiki create",
    "monolynx wiki update",
    "monolynx wiki edit",
    "monolynx wiki delete",
    "monolynx wiki search",
    "monolynx wiki config get",
    "monolynx wiki config set",
    "monolynx auth login",
    "monolynx auth logout",
    "monolynx auth whoami",
    "monolynx config get",
    "monolynx config set",
    "monolynx config list",
    "monolynx graph nodes",
    "monolynx graph node",
    "monolynx graph query",
    "monolynx graph path",
    "monolynx graph stats",
    "monolynx blog list",
    "monolynx blog get",
    "monolynx blog meta",
    "monolynx blog lint",
}
REQUIRED_CLI_GROUPS = {"project", "member", "ticket", "sprint", "wiki", "graph", "blog"}
GRAPH_CLI_COMMANDS = {
    "monolynx graph nodes",
    "monolynx graph node",
    "monolynx graph query",
    "monolynx graph path",
    "monolynx graph stats",
}
BLOG_CLI_COMMANDS = {
    "monolynx blog list",
    "monolynx blog get",
    "monolynx blog meta",
    "monolynx blog lint",
}
TOOLS_LABEL = {"pl": "Dostępne komendy", "en": "Available commands"}
MCP_LABEL = {"pl": "Dostępne narzędzia MCP", "en": "Available MCP tools"}


def _all_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _all_strings(v)]
    if isinstance(value, list | tuple):
        return [t for v in value for t in _all_strings(v)]
    return []


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


@pytest.mark.unit
class TestWikiFeatureBlogLint:
    """MON-262: moduł Wiki wymienia `lint_blog_post`, a licznik narzędzi w `ai_intro` zgadza się z listą."""

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_wiki_mcp_tools_list_lint_blog_post_once(self, lang):
        content = get_feature_content("wiki", lang)

        assert content is not None
        names = [t["name"] for t in content["mcp_tools"]]
        assert names.count("lint_blog_post") == 1
        assert {"list_blog_posts", "get_blog_post", "set_blog_post_meta"} <= set(names)

    @pytest.mark.parametrize(("lang", "suffix"), [("pl", "narzędzi MCP"), ("en", "MCP tools")])
    def test_wiki_ai_intro_counter_matches_the_number_of_mcp_tools(self, lang, suffix):
        content = get_feature_content("wiki", lang)

        assert content is not None
        count = len(content["mcp_tools"])
        assert count == 17
        assert f"{count} {suffix}" in content["ai_intro"]

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_lint_blog_post_tool_has_a_non_empty_description(self, lang):
        content = get_feature_content("wiki", lang)

        assert content is not None
        tool = next(t for t in content["mcp_tools"] if t["name"] == "lint_blog_post")
        assert tool["desc"].strip()
        assert chr(0x2014) not in tool["desc"]


@pytest.mark.unit
class TestCliFeatureContent:
    def test_cli_registered_in_features(self):
        assert "cli" in _FEATURES

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_builder_returns_complete_content(self, lang):
        content = get_feature_content("cli", lang)

        assert content is not None
        for key in (
            "title",
            "color",
            "icon",
            "badge",
            "headline",
            "description",
            "features",
            "steps",
            "mcp_tools",
            "tech_details",
            "tools_label",
            "other_modules",
        ):
            assert content.get(key), key

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_color_is_teal(self, lang):
        content = get_feature_content("cli", lang)

        assert content is not None
        assert content["color"] == "teal"

    def test_icon_uses_teal_class(self):
        assert "text-teal-400" in _ICON_CLI

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_icon_in_content_is_cli_icon(self, lang):
        content = get_feature_content("cli", lang)

        assert content is not None
        assert content["icon"] == _ICON_CLI

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_tools_label_exact_value(self, lang):
        content = get_feature_content("cli", lang)

        assert content is not None
        assert content["tools_label"] == TOOLS_LABEL[lang]

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_every_mcp_tool_is_allowed_cli_command(self, lang):
        content = get_feature_content("cli", lang)

        assert content is not None
        names = [t["name"] for t in content["mcp_tools"]]
        assert names
        unknown = [n for n in names if n not in ALLOWED_CLI_COMMANDS]
        assert unknown == []

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_mcp_tools_cover_all_required_groups(self, lang):
        content = get_feature_content("cli", lang)

        assert content is not None
        groups = {t["name"].split()[1] for t in content["mcp_tools"] if t["name"].startswith("monolynx ")}
        assert groups >= REQUIRED_CLI_GROUPS

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_mcp_tools_list_every_graph_command(self, lang):
        content = get_feature_content("cli", lang)

        assert content is not None
        names = {t["name"] for t in content["mcp_tools"]}
        assert names >= GRAPH_CLI_COMMANDS, sorted(GRAPH_CLI_COMMANDS - names)

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_mcp_tools_list_every_blog_command(self, lang):
        content = get_feature_content("cli", lang)

        assert content is not None
        names = {t["name"] for t in content["mcp_tools"]}
        assert names >= BLOG_CLI_COMMANDS, sorted(BLOG_CLI_COMMANDS - names)

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_text_has_required_facts(self, lang):
        content = get_feature_content("cli", lang)

        assert content is not None
        text = "\n".join(_all_strings(content))
        for fact in ("mnx", "3.10", "brew install monolynx/tap/monolynx", "brew tap monolynx/tap", ".items[]", "table", "yaml", "csv"):
            assert fact in text, fact

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_output_flag_is_global_so_it_precedes_the_group_in_every_example(self, lang):
        """MON-228 B1: `-o` istnieje tylko w root callbacku CLI, `monolynx ticket list -o json` konczy sie
        bledem 'No such option'. Przyklady musza miec flage przed grupa."""
        content = get_feature_content("cli", lang)

        assert content is not None
        text = "\n".join(_all_strings(content))
        assert "monolynx -o json ticket list" in text
        assert "ticket list -o json" not in text

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_markdown_shows_output_flag_before_the_group(self, lang):
        md = feature_markdown("cli", lang)

        assert md is not None
        assert "monolynx -o json ticket list" in md
        assert "ticket list -o json" not in md

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_text_has_no_mlx_or_em_dash(self, lang):
        content = get_feature_content("cli", lang)

        assert content is not None
        text = "\n".join(_all_strings(content))
        assert "mlx" not in text.lower()
        assert chr(0x2014) not in text

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_ai_intro_has_no_command_counter(self, lang):
        content = get_feature_content("cli", lang)

        assert content is not None
        intro = content.get("ai_intro") or ""
        assert not any(ch.isdigit() for ch in intro)

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_other_modules_of_cli_exclude_cli(self, lang):
        content = get_feature_content("cli", lang)

        assert content is not None
        assert all(m["slug"] != "cli" for m in content["other_modules"])

    @pytest.mark.parametrize("slug", [s for s in ALL_SLUGS if s != "cli"])
    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_other_modules_of_every_module_list_cli_teal(self, slug, lang):
        content = get_feature_content(slug, lang)

        assert content is not None
        entries = [m for m in content["other_modules"] if m["slug"] == "cli"]
        assert len(entries) == 1
        assert entries[0]["name"] == "CLI"
        assert entries[0]["color"] == "teal"

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_other_modules_helper_includes_cli(self, lang):
        result = _other_modules("scrum", lang)

        assert any(m["slug"] == "cli" and m["color"] == "teal" and m["name"] == "CLI" for m in result)

    @pytest.mark.parametrize("slug", [s for s in ALL_SLUGS if s != "cli"])
    def test_other_builders_have_no_tools_label(self, slug):
        """Fallback: inne moduly nie definiuja tools_label, wiec szablon pokazuje tekst MCP."""
        content = get_feature_content(slug, "pl")

        assert content is not None
        assert not content.get("tools_label")


@pytest.mark.unit
class TestCliFeatureMarkdown:
    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_markdown_uses_commands_label_not_mcp_label(self, lang):
        md = feature_markdown("cli", lang)

        assert md is not None
        assert TOOLS_LABEL[lang] in md
        assert MCP_LABEL[lang] not in md

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_markdown_lists_commands(self, lang):
        md = feature_markdown("cli", lang)

        assert md is not None
        assert "`monolynx ticket list`" in md
        assert "`monolynx sprint board`" in md

    def test_markdown_of_other_module_has_no_commands_label(self):
        md = feature_markdown("pipelines", "pl")

        assert md is not None
        assert TOOLS_LABEL["pl"] not in md
        assert "`create_pipeline`" in md


@pytest.mark.integration
class TestCliHttpAndLanding:
    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_cli_feature_page_returns_200(self, client, monkeypatch, lang):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)

        response = await client.get("/features/cli", params={"lang": lang})

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_cli_feature_page_shows_output_flag_before_the_group(self, client, monkeypatch, lang):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)

        response = await client.get("/features/cli", params={"lang": lang})

        assert response.status_code == 200
        assert "monolynx -o json ticket list" in response.text
        assert "ticket list -o json" not in response.text

    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_cli_feature_page_uses_commands_label(self, client, monkeypatch, lang):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)

        response = await client.get("/features/cli", params={"lang": lang})

        assert TOOLS_LABEL[lang] in response.text
        assert MCP_LABEL[lang] not in response.text

    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_other_feature_page_keeps_mcp_label(self, client, monkeypatch, lang):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)

        response = await client.get("/features/pipelines", params={"lang": lang})

        assert response.status_code == 200
        assert MCP_LABEL[lang] in response.text
        assert TOOLS_LABEL[lang] not in response.text

    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_cli_markdown_endpoint_returns_commands_label(self, client, lang):
        response = await client.get("/features/cli.md", params={"lang": lang})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert TOOLS_LABEL[lang] in response.text
        assert MCP_LABEL[lang] not in response.text

    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_landing_has_cli_tile_with_lang_link(self, client, monkeypatch, lang):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)

        response = await client.get("/", params={"lang": lang})

        assert response.status_code == 200
        assert f"/features/cli?lang={lang}" in response.text

    @pytest.mark.parametrize("lang", ["pl", "en"])
    async def test_index_markdown_links_cli(self, client, lang):
        response = await client.get("/index.md", params={"lang": lang})

        assert response.status_code == 200
        assert f"[CLI](https://monolynx.com/features/cli.md?lang={lang}): {_LANDING_I18N[lang]['m_cli']}" in response.text

    async def test_llms_txt_links_cli(self, client):
        response = await client.get("/llms.txt")

        assert response.status_code == 200
        assert "- [CLI](https://monolynx.com/features/cli?lang=en): " in response.text

    @pytest.mark.parametrize("lang", ["pl", "en"])
    def test_landing_i18n_has_non_empty_m_cli(self, lang):
        assert _LANDING_I18N[lang]["m_cli"].strip()

    def test_landing_i18n_m_cli_differs_between_languages(self):
        assert _LANDING_I18N["pl"]["m_cli"] != _LANDING_I18N["en"]["m_cli"]
