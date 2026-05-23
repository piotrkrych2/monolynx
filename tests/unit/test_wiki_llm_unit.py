"""Testy jednostkowe warstw LLM Wiki - Warstwy 1-3 (MON-73).

Pokrycie:
- extract_wiki_links (services/wiki.py)
- is_wiki_llm_enabled (services/wiki.py)
- WikiBacklinkResponse (schemas/wiki.py)
- DEFAULT_WIKI_SCHEMA (services/wiki_templates.py)
- _find_dead_links, _find_contradictions (services/wiki_lint.py)
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from monolynx.schemas.wiki import WikiBacklinkResponse
from monolynx.services.wiki import RESERVED_SLUGS, extract_wiki_links, is_wiki_llm_enabled, strip_code_spans
from monolynx.services.wiki_lint import _find_contradictions, _find_dead_links, _find_gaps
from monolynx.services.wiki_templates import DEFAULT_WIKI_SCHEMA

# ---------------------------------------------------------------------------
# extract_wiki_links
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractWikiLinks:
    """Testy wyodrębniania linków wewnętrznych wiki z treści markdown."""

    def test_empty_content_returns_empty_dict(self):
        """Pusta treść zwraca pusty słownik."""
        assert extract_wiki_links("") == {}

    def test_single_wikilink(self):
        """Pojedynczy [[slug]] - anchor = slug."""
        result = extract_wiki_links("Sprawdź [[modul-scrum]] po więcej.")
        assert "modul-scrum" in result
        assert result["modul-scrum"] == "modul-scrum"

    def test_wikilink_anchor_equals_ref(self):
        """Anchor dla [[slug]] to dokładnie sam slug."""
        result = extract_wiki_links("[[api-docs]]")
        assert result["api-docs"] == "api-docs"

    def test_uuid_wikilink_recognized(self):
        """UUID jako ref w [[uuid]] jest rozpoznany."""
        ref = "550e8400-e29b-41d4-a716-446655440000"
        result = extract_wiki_links(f"[[{ref}]]")
        assert ref in result

    def test_markdown_link_to_wiki_page(self):
        """Markdown link [tekst](/dashboard/x/wiki/pages/slug) - ref = slug, anchor = tekst."""
        result = extract_wiki_links("[Moduł Scrum](/dashboard/proj/wiki/pages/modul-scrum)")
        assert "modul-scrum" in result
        assert result["modul-scrum"] == "Moduł Scrum"

    def test_external_http_link_skipped(self):
        """Link http:// jest pomijany."""
        result = extract_wiki_links("[Zewnętrzny](https://example.com/foo)")
        assert result == {}

    def test_external_https_link_skipped(self):
        """Link https:// jest pomijany."""
        result = extract_wiki_links("[Foo](https://docs.python.org)")
        assert result == {}

    def test_mailto_link_skipped(self):
        """Link mailto: jest pomijany."""
        result = extract_wiki_links("[Email](mailto:admin@example.com)")
        assert result == {}

    def test_anchor_hash_link_skipped(self):
        """Link #anchor jest pomijany."""
        result = extract_wiki_links("[Sekcja](#sekcja-typy)")
        assert result == {}

    def test_dedup_wikilink_wins_over_markdown(self):
        """Gdy ten sam ref jako [[slug]] i jako markdown link, wygrywa wikilink (pierwszy)."""
        content = "[[modul-scrum]] i [inny tekst](/dashboard/x/wiki/pages/modul-scrum)"
        result = extract_wiki_links(content)
        assert result["modul-scrum"] == "modul-scrum"

    def test_dedup_first_occurrence_wins(self):
        """Drugi wpis tego samego ref jest ignorowany."""
        content = "[[foo]] lorem [[foo]]"
        result = extract_wiki_links(content)
        assert len([k for k in result if k == "foo"]) == 1

    def test_multiple_different_links(self):
        """Wiele różnych linków - wszystkie uwzględnione."""
        content = "[[alpha]] i [[beta]] i [[gamma]]"
        result = extract_wiki_links(content)
        assert set(result.keys()) == {"alpha", "beta", "gamma"}

    def test_segment_with_spaces_skipped(self):
        """Segment zawierający spacje nie jest traktowany jako slug."""
        result = extract_wiki_links("[Foo](nie ma slug)")
        assert result == {}

    def test_slug_with_numbers(self):
        """Slug zawierający cyfry jest rozpoznany."""
        result = extract_wiki_links("[[sprint-42]]")
        assert "sprint-42" in result

    def test_markdown_link_with_empty_text(self):
        """Markdown link bez tekstu (pusty string) - anchor = None."""
        result = extract_wiki_links("[](/dashboard/proj/wiki/pages/target-slug)")
        assert "target-slug" in result
        assert result["target-slug"] is None

    def test_whitespace_in_wikilink_stripped(self):
        """Białe znaki wewnątrz [[  slug  ]] są przycinane."""
        result = extract_wiki_links("[[  modul-scrum  ]]")
        assert "modul-scrum" in result

    def test_no_links_in_plain_text(self):
        """Zwykły tekst bez żadnych linków - pusty wynik."""
        result = extract_wiki_links("To jest zwykły tekst bez linków.")
        assert result == {}

    def test_uuid_as_markdown_link_target(self):
        """UUID jako ostatni segment markdown linku jest rozpoznany."""
        uid = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
        result = extract_wiki_links(f"[Strona](/dashboard/x/wiki/pages/{uid})")
        assert uid in result


# ---------------------------------------------------------------------------
# is_wiki_llm_enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsWikiLlmEnabled:
    """Testy sprawdzania czy LLM Wiki jest włączone dla projektu."""

    def test_returns_true_when_enabled(self):
        """Zwraca True gdy wiki_llm_enabled = True."""
        project = SimpleNamespace(wiki_llm_enabled=True)
        assert is_wiki_llm_enabled(project) is True  #

    def test_returns_false_when_disabled(self):
        """Zwraca False gdy wiki_llm_enabled = False."""
        project = SimpleNamespace(wiki_llm_enabled=False)
        assert is_wiki_llm_enabled(project) is False  #

    def test_returns_false_when_none(self):
        """Zwraca False gdy wiki_llm_enabled = None."""
        project = SimpleNamespace(wiki_llm_enabled=None)
        assert is_wiki_llm_enabled(project) is False  #


# ---------------------------------------------------------------------------
# WikiBacklinkResponse (schemas/wiki.py)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWikiBacklinkResponse:
    """Testy schematu WikiBacklinkResponse."""

    def test_construction_with_required_fields(self):
        """Tworzy obiekt z wymaganymi polami."""
        src = uuid.uuid4()
        tgt = uuid.uuid4()
        resp = WikiBacklinkResponse(source_page_id=src, target_page_id=tgt)
        assert resp.source_page_id == src
        assert resp.target_page_id == tgt
        assert resp.anchor_text is None

    def test_construction_with_anchor_text(self):
        """Anchor_text jest opcjonalny i poprawnie zapisany."""
        src = uuid.uuid4()
        tgt = uuid.uuid4()
        resp = WikiBacklinkResponse(source_page_id=src, target_page_id=tgt, anchor_text="Moduł Scrum")
        assert resp.anchor_text == "Moduł Scrum"

    def test_serialization_to_dict(self):
        """model_dump zwraca słownik z UUID jako obiekty Python."""
        src = uuid.uuid4()
        tgt = uuid.uuid4()
        resp = WikiBacklinkResponse(source_page_id=src, target_page_id=tgt, anchor_text="link")
        data = resp.model_dump()
        assert data["source_page_id"] == src
        assert data["target_page_id"] == tgt
        assert data["anchor_text"] == "link"

    def test_anchor_text_default_is_none(self):
        """Domyślna wartość anchor_text to None."""
        resp = WikiBacklinkResponse(source_page_id=uuid.uuid4(), target_page_id=uuid.uuid4())
        assert resp.anchor_text is None


# ---------------------------------------------------------------------------
# DEFAULT_WIKI_SCHEMA (services/wiki_templates.py)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDefaultWikiSchema:
    """Testy stałej DEFAULT_WIKI_SCHEMA."""

    def test_is_non_empty_string(self):
        """DEFAULT_WIKI_SCHEMA jest niepustym stringiem."""
        assert isinstance(DEFAULT_WIKI_SCHEMA, str)
        assert len(DEFAULT_WIKI_SCHEMA) > 100

    def test_contains_contradiction_marker(self):
        """Zawiera marker sprzeczności wykrywany przez lint_wiki."""
        # lint_wiki szuka: > **Sprzeczność
        assert "> **Sprzeczność" in DEFAULT_WIKI_SCHEMA

    def test_no_em_dash(self):
        """Nie zawiera em-dash (U+2014) - błąd estetyczny."""
        assert "—" not in DEFAULT_WIKI_SCHEMA

    def test_contains_reserved_slugs(self):
        """Opisuje strony systemowe wiki-index, wiki-log, wiki-schema."""
        assert "wiki-index" in DEFAULT_WIKI_SCHEMA
        assert "wiki-log" in DEFAULT_WIKI_SCHEMA
        assert "wiki-schema" in DEFAULT_WIKI_SCHEMA

    def test_starts_with_markdown_header(self):
        """Zaczyna się od nagłówka markdown."""
        assert DEFAULT_WIKI_SCHEMA.strip().startswith("#")


# ---------------------------------------------------------------------------
# _find_dead_links (services/wiki_lint.py)
# ---------------------------------------------------------------------------


def _make_page(slug: str, title: str | None = None) -> MagicMock:
    """Helper: tworzy mock WikiPage o podanym slugu."""
    page = MagicMock()
    page.id = uuid.uuid4()
    page.slug = slug
    page.title = title or slug
    return page


@pytest.mark.unit
class TestFindDeadLinks:
    """Testy sub-funkcji _find_dead_links z wiki_lint.py."""

    def test_known_slug_not_dead(self):
        """Wikilink do istniejącego slugu nie jest martwym linkiem."""
        page_a = _make_page("strona-a")
        page_b = _make_page("strona-b")
        slug_to_id = {"strona-a": page_a.id, "strona-b": page_b.id}
        id_set = {page_a.id, page_b.id}
        content_cache = {
            page_a.id: "Sprawdź [[strona-b]]",
            page_b.id: "",
        }
        dead, _ = _find_dead_links([page_a, page_b], slug_to_id, id_set, content_cache)
        assert dead == []

    def test_unknown_slug_is_dead(self):
        """Wikilink do nieistniejącego slugu jest martwym linkiem."""
        page_a = _make_page("strona-a")
        slug_to_id = {"strona-a": page_a.id}
        id_set = {page_a.id}
        content_cache = {page_a.id: "Sprawdź [[nie-istnieje]]"}
        dead, _ = _find_dead_links([page_a], slug_to_id, id_set, content_cache)
        assert len(dead) == 1
        assert dead[0]["ref"] == "nie-istnieje"
        assert dead[0]["source_slug"] == "strona-a"

    def test_missing_ref_counted(self):
        """Ref nierozwiązany zliczany w missing_ref_counts."""
        page_a = _make_page("strona-a")
        page_b = _make_page("strona-b")
        slug_to_id = {"strona-a": page_a.id, "strona-b": page_b.id}
        id_set = {page_a.id, page_b.id}
        # Obie strony linkują do tego samego nieznanego sluga
        content_cache = {
            page_a.id: "[[brak-strony]]",
            page_b.id: "[[brak-strony]]",
        }
        _, counts = _find_dead_links([page_a, page_b], slug_to_id, id_set, content_cache)
        assert counts.get("brak-strony") == 2

    def test_empty_content_skipped(self):
        """Strona z pustym contentem nie zgłasza martwych linków."""
        page = _make_page("strona-a")
        slug_to_id = {"strona-a": page.id}
        id_set = {page.id}
        content_cache = {page.id: ""}
        dead, counts = _find_dead_links([page], slug_to_id, id_set, content_cache)
        assert dead == []
        assert counts == {}

    def test_known_uuid_not_dead(self):
        """UUID istniejącej strony jako ref - nie jest martwym linkiem."""
        page = _make_page("strona-a")
        slug_to_id = {"strona-a": page.id}
        id_set = {page.id}
        content_cache = {page.id: f"[[{page.id}]]"}
        dead, _ = _find_dead_links([page], slug_to_id, id_set, content_cache)
        assert dead == []

    def test_unknown_uuid_is_dead(self):
        """UUID nieistniejącej strony jako ref - jest martwym linkiem."""
        page = _make_page("strona-a")
        unknown_id = uuid.uuid4()
        slug_to_id = {"strona-a": page.id}
        id_set = {page.id}
        content_cache = {page.id: f"[[{unknown_id}]]"}
        dead, _ = _find_dead_links([page], slug_to_id, id_set, content_cache)
        assert len(dead) == 1


# ---------------------------------------------------------------------------
# _find_contradictions (services/wiki_lint.py)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindContradictions:
    """Testy sub-funkcji _find_contradictions z wiki_lint.py."""

    def test_page_with_contradiction_marker_detected(self):
        """Strona z markerem '> **Sprzeczność' jest wykryta."""
        page = _make_page("decyzja-auth", "Decyzja auth")
        content_cache = {
            page.id: "> **Sprzeczność [2026-05-22]:** Źródło A mówi X, źródło B mówi Y.",
        }
        result = _find_contradictions([page], content_cache)
        assert len(result) == 1
        assert result[0]["slug"] == "decyzja-auth"
        assert result[0]["title"] == "Decyzja auth"

    def test_page_without_marker_not_detected(self):
        """Strona bez markera sprzeczności nie jest wykryta."""
        page = _make_page("strona-normalna")
        content_cache = {page.id: "# Normalna strona\n\nTreść bez sprzeczności."}
        result = _find_contradictions([page], content_cache)
        assert result == []

    def test_marker_case_insensitive(self):
        """Marker sprzeczności jest case-insensitive (regex IGNORECASE)."""
        page = _make_page("test-case")
        content_cache = {page.id: "> **sprzeczność [2026-01-01]:** test."}
        result = _find_contradictions([page], content_cache)
        assert len(result) == 1

    def test_empty_content_not_detected(self):
        """Strona z pustą treścią nie jest wykryta."""
        page = _make_page("pusta")
        content_cache = {page.id: ""}
        result = _find_contradictions([page], content_cache)
        assert result == []

    def test_multiple_pages_some_with_marker(self):
        """Z wielu stron tylko te z markerem są wykryte."""
        page_ok = _make_page("ok-strona")
        page_bad = _make_page("sprzeczna-strona")
        content_cache = {
            page_ok.id: "# Bez sprzeczności",
            page_bad.id: "> **Sprzeczność [2026-05-01]:** Niezgodność danych.",
        }
        result = _find_contradictions([page_ok, page_bad], content_cache)
        assert len(result) == 1
        assert result[0]["slug"] == "sprzeczna-strona"

    def test_returns_page_id_as_string(self):
        """Zwracany page_id jest stringiem."""
        page = _make_page("test-page")
        content_cache = {page.id: "> **Sprzeczność:** test."}
        result = _find_contradictions([page], content_cache)
        assert isinstance(result[0]["page_id"], str)


@pytest.mark.unit
class TestFindGaps:
    """Testy heurystyki luk - koncepty wzmiankowane >=2 razy bez własnej strony."""

    def test_ref_mentioned_twice_is_gap(self):
        """Referencja wzmiankowana 2 razy jest luką."""
        result = _find_gaps({"event-sourcing": 2})
        assert result == [{"ref": "event-sourcing", "mention_count": 2}]

    def test_ref_mentioned_once_not_gap(self):
        """Referencja wzmiankowana raz nie jest luką (próg >=2)."""
        result = _find_gaps({"jednorazowy": 1})
        assert result == []

    def test_empty_counts_returns_empty(self):
        """Brak brakujących referencji - brak luk."""
        assert _find_gaps({}) == []

    def test_uuid_ref_skipped(self):
        """Referencja UUID jest pomijana - to błędny link, nie koncept."""
        ref_uuid = str(uuid.uuid4())
        result = _find_gaps({ref_uuid: 5})
        assert result == []

    def test_sorted_by_mention_count_desc(self):
        """Luki posortowane malejąco po liczbie wzmianek."""
        result = _find_gaps({"rzadki": 2, "czesty": 9, "sredni": 4})
        refs = [g["ref"] for g in result]
        assert refs == ["czesty", "sredni", "rzadki"]

    def test_custom_min_count_threshold(self):
        """Próg min_count jest konfigurowalny."""
        result = _find_gaps({"x": 3}, min_count=4)
        assert result == []


# ---------------------------------------------------------------------------
# strip_code_spans (MON-74)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStripCodeSpans:
    """Testy helpera strip_code_spans - usuwanie bloków/inline code przed analizą."""

    def test_plain_text_unchanged(self):
        """Zwykły tekst bez code spanów jest zwracany bez zmian."""
        text = "Normalny tekst bez żadnego kodu."
        assert strip_code_spans(text) == text

    def test_inline_code_replaced_with_space(self):
        """Inline code `...` jest zastępowany spacją."""
        result = strip_code_spans("przed `[[slug]]` po")
        assert "[[slug]]" not in result

    def test_fenced_block_replaced(self):
        """Fenced block ```...``` jest zastępowany (zawartość usunięta)."""
        content = "przed\n```\n[[slug]]\n```\npo"
        result = strip_code_spans(content)
        assert "[[slug]]" not in result

    def test_text_outside_code_preserved(self):
        """Tekst poza blokami kodu jest zachowany."""
        content = "tekst przed `code` tekst po"
        result = strip_code_spans(content)
        assert "tekst przed" in result
        assert "tekst po" in result

    def test_empty_string(self):
        """Pusty ciąg zwraca pusty ciąg."""
        assert strip_code_spans("") == ""

    def test_tilde_fenced_block_replaced(self):
        """Fenced block ~~~...~~~ jest również usuwany."""
        content = "tekst\n~~~\n[[slug]]\n~~~\ntekst"
        result = strip_code_spans(content)
        assert "[[slug]]" not in result

    def test_wikilink_outside_code_preserved(self):
        """Wikilink poza blokiem kodu jest zachowany w wyniku."""
        content = "[[realny]] a w kodzie `[[przykład]]`"
        result = strip_code_spans(content)
        assert "[[realny]]" in result
        assert "[[przykład]]" not in result


# ---------------------------------------------------------------------------
# extract_wiki_links - alias Obsidian i filtrowanie code span (MON-74)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractWikiLinksAlias:
    """Testy obsługi aliasów Obsidian w extract_wiki_links."""

    def test_obsidian_alias_basic(self):
        """[[slug|Label]] - ref = slug, anchor = Label."""
        result = extract_wiki_links("[[modul-scrum|Moduł Scrum]]")
        assert "modul-scrum" in result
        assert result["modul-scrum"] == "Moduł Scrum"

    def test_obsidian_alias_with_surrounding_whitespace(self):
        """[[ slug | Label ]] - białe znaki wokół segmentów są przycinane."""
        result = extract_wiki_links("[[ modul-scrum | Moduł Scrum ]]")
        assert "modul-scrum" in result
        assert result["modul-scrum"] == "Moduł Scrum"

    def test_obsidian_alias_empty_label_falls_back_to_ref(self):
        """[[slug|]] - pusty label daje anchor = ref (slug)."""
        result = extract_wiki_links("[[modul-scrum|]]")
        assert "modul-scrum" in result
        # pusty label po strip -> fallback na ref
        assert result["modul-scrum"] == "modul-scrum"

    def test_obsidian_alias_ref_dedup_wins(self):
        """Pierwszy napotkany [[slug|Label1]] wygrywa nad drugim [[slug|Label2]]."""
        content = "[[alpha|Pierwsza]] ... [[alpha|Druga]]"
        result = extract_wiki_links(content)
        assert result["alpha"] == "Pierwsza"

    def test_obsidian_alias_ref_is_slug(self):
        """Część przed | (ref) spełnia format sluga."""
        result = extract_wiki_links("[[api-v2|Dokumentacja API v2]]")
        assert "api-v2" in result

    def test_plain_wikilink_still_works_alongside_alias(self):
        """Plain [[slug]] i [[slug|label]] obok siebie - oba poprawnie parsowane."""
        content = "[[alpha]] i [[beta|Beta Label]]"
        result = extract_wiki_links(content)
        assert result["alpha"] == "alpha"
        assert result["beta"] == "Beta Label"


@pytest.mark.unit
class TestExtractWikiLinksCodeFiltering:
    """Testy pomijania wikilinków i markerów wewnątrz bloków kodu."""

    def test_wikilink_in_inline_code_skipped(self):
        """Wikilink w inline code `[[slug]]` jest pomijany."""
        result = extract_wiki_links("`[[beta]]`")
        assert result == {}

    def test_wikilink_in_fenced_block_skipped(self):
        """Wikilink w fenced block ```...``` jest pomijany."""
        content = "```\n[[slug]]\n```"
        result = extract_wiki_links(content)
        assert result == {}

    def test_wikilink_in_tilde_fenced_block_skipped(self):
        """Wikilink w bloku ~~~...~~~ jest pomijany."""
        content = "~~~\n[[slug]]\n~~~"
        result = extract_wiki_links(content)
        assert result == {}

    def test_real_link_preserved_code_link_skipped(self):
        """Realny [[alfa]] zachowany; [[beta]] w inline code pominięty."""
        content = "Realny [[alfa]] a w kodzie `[[beta]]`"
        result = extract_wiki_links(content)
        assert "alfa" in result
        assert "beta" not in result

    def test_multiple_inline_codes_all_skipped(self):
        """Wiele inline code span - wszystkie zawarte wikilinki pominięte."""
        content = "`[[a]]` tekst `[[b]]` i [[realny]]"
        result = extract_wiki_links(content)
        assert "a" not in result
        assert "b" not in result
        assert "realny" in result

    def test_alias_in_inline_code_skipped(self):
        """Alias [[slug|label]] w inline code jest pomijany."""
        content = "`[[modul-scrum|Moduł Scrum]]`"
        result = extract_wiki_links(content)
        assert result == {}

    def test_markdown_link_in_inline_code_skipped(self):
        """Markdown link w inline code jest pomijany."""
        content = "`[Tekst](/dashboard/x/wiki/pages/slug)`"
        result = extract_wiki_links(content)
        assert result == {}


# ---------------------------------------------------------------------------
# _find_contradictions - code span i strony systemowe (MON-74)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindContradictionsCodeFiltering:
    """Testy filtrowania markerów sprzeczności w blokach kodu i stronach systemowych."""

    def test_marker_in_inline_code_not_detected(self):
        """Marker tylko w inline code - strona NIE jest zgłaszana."""
        page = _make_page("przyklad-format")
        content_cache = {
            page.id: "Dokumentacja: `> **Sprzeczność [data]:** przykład formatu`",
        }
        result = _find_contradictions([page], content_cache)
        assert result == []

    def test_marker_in_fenced_block_not_detected(self):
        """Marker tylko w fenced block - strona NIE jest zgłaszana."""
        page = _make_page("schemat-wiki")
        content_cache = {
            page.id: "Opis:\n```\n> **Sprzeczność [2026-01-01]:** przykład\n```\nReszta tekstu.",
        }
        result = _find_contradictions([page], content_cache)
        assert result == []

    def test_real_marker_outside_code_detected(self):
        """Realny marker poza blokiem kodu - strona jest zgłaszana."""
        page = _make_page("decyzja-db", "Decyzja DB")
        content_cache = {
            page.id: "> **Sprzeczność [2026-05-22]:** Źródło A mówi X, źródło B mówi Y.",
        }
        result = _find_contradictions([page], content_cache)
        assert len(result) == 1
        assert result[0]["slug"] == "decyzja-db"

    def test_marker_in_code_and_real_marker_outside_detected(self):
        """Gdy strona ma marker w code i TEŻ poza code - strona jest zgłaszana (realny wygrywa)."""
        page = _make_page("mieszana")
        content_cache = {
            page.id: ("Przykład w code: `> **Sprzeczność:** format`\n\n> **Sprzeczność [2026-05-20]:** Realna niezgodność."),
        }
        result = _find_contradictions([page], content_cache)
        assert len(result) == 1
        assert result[0]["slug"] == "mieszana"

    def test_system_page_wiki_schema_with_real_marker_not_detected(self):
        """Strona systemowa wiki-schema z realnym markerem - NIE jest zgłaszana."""
        assert "wiki-schema" in RESERVED_SLUGS  # weryfikacja założenia testu
        page = _make_page("wiki-schema", "Wiki Schema")
        content_cache = {
            page.id: "> **Sprzeczność [2026-05-01]:** Dokumentuje format - to nie błąd.",
        }
        result = _find_contradictions([page], content_cache)
        assert result == []

    def test_system_page_wiki_index_with_real_marker_not_detected(self):
        """Strona systemowa wiki-index z realnym markerem - NIE jest zgłaszana."""
        assert "wiki-index" in RESERVED_SLUGS
        page = _make_page("wiki-index", "Wiki Index")
        content_cache = {
            page.id: "> **Sprzeczność [2026-05-01]:** To marker systemowy.",
        }
        result = _find_contradictions([page], content_cache)
        assert result == []

    def test_system_page_wiki_log_with_real_marker_not_detected(self):
        """Strona systemowa wiki-log z realnym markerem - NIE jest zgłaszana."""
        assert "wiki-log" in RESERVED_SLUGS
        page = _make_page("wiki-log", "Wiki Log")
        content_cache = {
            page.id: "> **Sprzeczność [2026-05-01]:** Wpis w logu.",
        }
        result = _find_contradictions([page], content_cache)
        assert result == []

    def test_mix_system_and_regular_pages(self):
        """Strona systemowa z markerem + zwykła z markerem - tylko zwykła zgłoszona."""
        system_page = _make_page("wiki-schema", "Wiki Schema")
        regular_page = _make_page("architektura", "Architektura")
        content_cache = {
            system_page.id: "> **Sprzeczność [2026-05-01]:** Marker w schemie.",
            regular_page.id: "> **Sprzeczność [2026-05-22]:** Realna niezgodność.",
        }
        result = _find_contradictions([system_page, regular_page], content_cache)
        assert len(result) == 1
        assert result[0]["slug"] == "architektura"
