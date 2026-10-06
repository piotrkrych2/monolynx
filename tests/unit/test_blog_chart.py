"""Testy jednostkowe MON-257 -- wykres SVG z tabeli markdown (services/blog_chart.py).

Kontrakt: `parse_chart_table` (pierwsza tabela markdown -> `ChartData` albo None) i
`render_chart_svg` (typ, tytul, dane -> napis `<svg>` albo None). SVG jest parsowany jako XML
(`xml.etree.ElementTree`), wiec asercje dotycza realnych elementow i atrybutow, a nie
podciagow. Kolory daje wylacznie CSS (klasy `blog-chart-series-N`), SVG nie niesie kolorow.
"""

import dataclasses
import re
import xml.etree.ElementTree as ET

import pytest

from monolynx.services.blog_chart import CHART_TYPES, ChartData, ChartSeries, parse_chart_table, render_chart_svg

SVG_NS = "http://www.w3.org/2000/svg"
_SERIES_CLASS = re.compile(r"blog-chart-series-([0-9]+)")
_ALLOWED_PAINT = {"none", "currentColor"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _svg(chart_type: str, title: str, data: ChartData) -> ET.Element:
    result = render_chart_svg(chart_type, title, data)
    assert result is not None
    return ET.fromstring(result)


def _data(labels: list[str], *series: tuple[str, list[float]]) -> ChartData:
    return ChartData(
        labels=tuple(labels),
        series=tuple(ChartSeries(name, tuple(float(value) for value in values)) for name, values in series),
    )


def _series_shapes(root: ET.Element) -> list[tuple[str, int]]:
    """(nazwa elementu, N) dla kazdego elementu z klasa `blog-chart-series-N`."""
    shapes = []
    for element in root.iter():
        for token in element.get("class", "").split():
            match = _SERIES_CLASS.fullmatch(token)
            if match:
                shapes.append((_local(element.tag), int(match.group(1))))
    return shapes


def _shapes_of(root: ET.Element, tag: str) -> list[ET.Element]:
    return [element for element in root.iter() if _local(element.tag) == tag and _SERIES_CLASS.search(element.get("class", ""))]


def _all_text(root: ET.Element) -> str:
    return " ".join(part for element in root.iter() for part in (element.text or "", element.tail or ""))


def _mentions(text: str, number: float) -> bool:
    """Liczba w opisie moze miec kropke albo przecinek dziesietny."""
    dotted = f"{number:g}"
    return dotted in text or dotted.replace(".", ",") in text


_LABELS = ["Styczeń", "Luty", "Marzec"]
_TWO = _data(_LABELS, ("Tickety", [4217, 1890, 2561]), ("Bugi", [37, 12, 45]))
_ONE = _data(_LABELS, ("Tickety", [4217, 1890, 2561]))


# ---------------------------------------------------------------------------
# parse_chart_table
# ---------------------------------------------------------------------------

_TABLE_TWO = "| Miesiąc | Tickety | Bugi |\n|---|---|---|\n| Styczeń | 12 | 3 |\n| Luty | 18,5 | 1 |"


@pytest.mark.unit
class TestParseChartTable:
    def test_two_series_table_gives_labels_and_series_in_column_order(self):
        assert parse_chart_table(_TABLE_TWO) == ChartData(
            labels=("Styczeń", "Luty"),
            series=(ChartSeries("Tickety", (12.0, 18.5)), ChartSeries("Bugi", (3.0, 1.0))),
        )

    def test_single_series_table(self):
        assert parse_chart_table("| Etykieta | Wartość |\n|---|---|\n| A | 1 |\n| B | 2 |\n| C | 3 |") == ChartData(
            labels=("A", "B", "C"),
            series=(ChartSeries("Wartość", (1.0, 2.0, 3.0)),),
        )

    def test_decimal_comma_and_inner_spaces_in_number_are_accepted(self):
        data = parse_chart_table("| E | W |\n|---|---|\n| a | 1 234,5 |\n| b | 2 000 |\n| c | -0,5 |\n| d | 3.25 |")

        assert data is not None
        assert data.series[0].values == (1234.5, 2000.0, -0.5, 3.25)

    @pytest.mark.parametrize(
        ("cell", "expected"),
        [
            pytest.param("5.", 5.0, id="trailing-dot"),
            pytest.param(".5", 0.5, id="leading-dot"),
            pytest.param("+3", 3.0, id="explicit-plus"),
            pytest.param("-2.5", -2.5, id="negative-decimal"),
            pytest.param("1e3", 1000.0, id="exponent"),
            pytest.param("2,5E-1", 0.25, id="comma-and-negative-exponent"),
        ],
    )
    def test_number_forms_accepted_in_a_value_cell(self, cell, expected):
        data = parse_chart_table(f"| A | B |\n|---|---|\n| x | {cell} |")

        assert data is not None
        assert data.series[0].values == (expected,)

    @pytest.mark.parametrize(
        "cell",
        [
            pytest.param("1_0", id="underscore-separator"),
            pytest.param("1__0", id="double-underscore"),
            pytest.param("0x10", id="hex"),
            pytest.param("1.2.3", id="two-dots"),
            pytest.param("1e", id="dangling-exponent"),
            pytest.param("--1", id="double-sign"),
        ],
    )
    def test_value_that_only_looks_like_a_number_gives_none(self, cell):
        assert parse_chart_table(f"| A | B |\n|---|---|\n| x | {cell} |") is None

    def test_cells_are_stripped_of_surrounding_spaces(self):
        data = parse_chart_table("|   Etykieta   |   Wartość   |\n|---|---|\n|   Alfa   |   7   |")

        assert data == ChartData(labels=("Alfa",), series=(ChartSeries("Wartość", (7.0,)),))

    def test_alignment_colons_in_separator_row_are_accepted(self):
        assert parse_chart_table("| A | B |\n|:---|---:|\n| x | 1 |") == ChartData(labels=("x",), series=(ChartSeries("B", (1.0,)),))

    def test_first_table_wins_when_text_surrounds_and_a_second_table_follows(self):
        source = f"Wstęp przed tabelą.\n\n{_TABLE_TWO}\n\nTekst po.\n\n| Inna | Druga |\n|---|---|\n| z | 99 |"

        data = parse_chart_table(source)

        assert data is not None
        assert data.labels == ("Styczeń", "Luty")
        assert [series.name for series in data.series] == ["Tickety", "Bugi"]

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("", id="empty"),
            pytest.param("Sam akapit bez tabeli.", id="no-table"),
            pytest.param("| A | B |\n|---|---|", id="no-data-rows"),
            pytest.param("| Tylko |\n|---|\n| a |\n| b |", id="one-column"),
            pytest.param("| A | B | C |\n|---|---|---|\n| x | 1 |", id="row-shorter-than-header"),
            pytest.param("| A | B |\n|---|---|\n| x | 1 | 2 |", id="row-longer-than-header"),
            pytest.param("| A | B |\n|---|---|\n| x | 1 |\n| y | 2 | 3 |", id="second-row-uneven"),
            pytest.param("| A | B |\n|---|---|\n| x | abc |", id="non-numeric"),
            pytest.param("| A | B |\n|---|---|\n| x | 1 |\n| y | 12 zł |", id="unit-suffix"),
            pytest.param("| A | B |\n|---|---|\n| x | |", id="empty-value-cell"),
            pytest.param("| A | B |\n|---|---|\n| x | nan |", id="nan"),
            pytest.param("| A | B |\n|---|---|\n| x | NaN |", id="nan-capitalised"),
            pytest.param("| A | B |\n|---|---|\n| x | inf |", id="inf"),
            pytest.param("| A | B |\n|---|---|\n| x | -inf |", id="negative-inf"),
            pytest.param("| A | B |\n|---|---|\n| x | Infinity |", id="infinity-word"),
            pytest.param("| A | B | C |\n|---|---|---|\n| x | 1 | oops |", id="second-series-non-numeric"),
        ],
    )
    def test_invalid_table_gives_none(self, source):
        assert parse_chart_table(source) is None

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("\x00", id="nul"),
            pytest.param("|", id="lone-pipe"),
            pytest.param("||", id="double-pipe"),
            pytest.param("| |\n|-|\n| |", id="blank-cells"),
            pytest.param("|---|---|", id="separator-only"),
            pytest.param("<<<>>>", id="angles"),
            pytest.param("|" * 5000, id="very-long-pipes"),
        ],
    )
    def test_garbage_input_gives_none_and_never_raises(self, source):
        assert parse_chart_table(source) is None

    def test_result_is_frozen(self):
        data = parse_chart_table(_TABLE_TWO)

        assert data is not None
        with pytest.raises(dataclasses.FrozenInstanceError):
            data.labels = ()  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            data.series[0].name = "x"  # type: ignore[misc]

    def test_chart_types_are_bar_line_pie(self):
        assert CHART_TYPES == ("bar", "line", "pie")


# ---------------------------------------------------------------------------
# render_chart_svg: struktura i dostepnosc
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderChartSvgStructure:
    @pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
    def test_root_is_an_accessible_svg_image_with_class_and_viewbox(self, chart_type):
        root = _svg(chart_type, "Tickety", _TWO)

        assert root.tag == f"{{{SVG_NS}}}svg"
        assert root.get("role") == "img"
        assert "blog-chart-svg" in root.get("class", "").split()
        assert re.fullmatch(r"0 0 [0-9]+(\.[0-9]+)? [0-9]+(\.[0-9]+)?", root.get("viewBox", ""))

    @pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
    def test_title_and_desc_are_the_first_two_children(self, chart_type):
        root = _svg(chart_type, "Tickety w kwartale", _TWO)

        children = list(root)
        assert _local(children[0].tag) == "title"
        assert children[0].text == "Tickety w kwartale"
        assert _local(children[1].tag) == "desc"

    @pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
    def test_empty_title_falls_back_to_wykres(self, chart_type):
        root = _svg(chart_type, "", _TWO)

        assert root[0].text == "Wykres"

    @pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
    def test_desc_lists_labels_series_names_and_values(self, chart_type):
        root = _svg(chart_type, "Tickety", _TWO)

        desc = next(element for element in root if _local(element.tag) == "desc")
        text = " ".join(desc.itertext())
        for label in _LABELS:
            assert label in text
        assert "Tickety" in text
        for value in ("4217", "1890", "2561"):
            assert value in text

    @pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
    def test_desc_mentions_a_decimal_value(self, chart_type):
        data = _data(["Alfa", "Beta"], ("Wynik", [2.5, 7]))

        desc = next(element for element in _svg(chart_type, "T", data) if _local(element.tag) == "desc")

        assert _mentions(" ".join(desc.itertext()), 2.5)

    @pytest.mark.parametrize("chart_type", ["bar", "line"])
    def test_multi_series_desc_names_every_series(self, chart_type):
        desc = next(element for element in _svg(chart_type, "T", _TWO) if _local(element.tag) == "desc")

        text = " ".join(desc.itertext())
        assert "Tickety" in text
        assert "Bugi" in text
        assert "37" in text

    @pytest.mark.parametrize("chart_type", ["bar", "line"])
    def test_axes_grid_and_category_labels_use_css_classes(self, chart_type):
        root = _svg(chart_type, "T", _ONE)

        classes = {token for element in root.iter() for token in element.get("class", "").split()}
        assert {"blog-chart-axis", "blog-chart-label"} <= classes
        label_texts = {(element.text or "").strip() for element in root.iter() if "blog-chart-label" in element.get("class", "").split()}
        assert set(_LABELS) <= label_texts


# ---------------------------------------------------------------------------
# render_chart_svg: ksztalty serii odpowiadaja danym
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderChartSvgShapes:
    def test_bar_draws_one_rect_per_label_and_series_with_the_series_class(self):
        shapes = _series_shapes(_svg("bar", "T", _TWO))

        assert sorted(shapes) == [("rect", 1)] * 3 + [("rect", 2)] * 3

    def test_bar_with_one_series_draws_one_rect_per_label(self):
        assert _series_shapes(_svg("bar", "T", _ONE)) == [("rect", 1)] * 3

    def test_bar_heights_are_proportional_to_values(self):
        root = _svg("bar", "T", _data(["A", "B", "C"], ("S", [10, 20, 40])))

        rects = sorted(_shapes_of(root, "rect"), key=lambda rect: float(rect.get("x", "0")))
        heights = [float(rect.get("height", "0")) for rect in rects]
        assert len(heights) == 3
        assert heights[0] > 0
        assert heights[1] / heights[0] == pytest.approx(2, rel=0.02)
        assert heights[2] / heights[0] == pytest.approx(4, rel=0.02)

    def test_bar_with_negative_values_never_gets_a_negative_rect_size(self):
        root = _svg("bar", "T", _data(["A", "B", "C", "D"], ("S", [5, -3, 0, 8])))

        rects = _shapes_of(root, "rect")
        assert rects
        for rect in rects:
            assert float(rect.get("width", "0")) >= 0
            assert float(rect.get("height", "0")) >= 0

    def test_line_draws_one_line_element_and_one_circle_per_point_for_each_series(self):
        shapes = _series_shapes(_svg("line", "T", _TWO))

        for number in (1, 2):
            tags = sorted(tag for tag, n in shapes if n == number)
            assert len(tags) == 4
            assert tags[:3] == ["circle"] * 3
            assert tags[3] in {"path", "polyline"}
        assert {n for _, n in shapes} == {1, 2}

    def test_line_points_follow_label_order_and_value_height(self):
        root = _svg("line", "T", _data(["A", "B", "C"], ("S", [10, 30, 20])))

        circles = sorted(_shapes_of(root, "circle"), key=lambda circle: float(circle.get("cx", "0")))
        xs = [float(circle.get("cx", "0")) for circle in circles]
        ys = [float(circle.get("cy", "0")) for circle in circles]
        assert len(circles) == 3
        assert xs[0] < xs[1] < xs[2]
        # Oś Y w SVG rośnie w dół: największa wartość (30) ma najmniejsze cy.
        assert ys[1] < ys[2] < ys[0]

    def test_pie_draws_one_slice_per_label_from_the_first_series_only(self):
        shapes = _series_shapes(_svg("pie", "T", _TWO))

        assert sorted(shapes) == [("path", 1), ("path", 2), ("path", 3)]

    def test_pie_paths_differ_between_slices_and_change_with_the_values(self):
        equal = _shapes_of(_svg("pie", "T", _data(["A", "B", "C"], ("S", [1, 1, 1]))), "path")
        skewed = _shapes_of(_svg("pie", "T", _data(["A", "B", "C"], ("S", [1, 1, 8]))), "path")

        equal_paths = [path.get("d") for path in equal]
        skewed_paths = [path.get("d") for path in skewed]
        assert len(set(equal_paths)) == 3
        assert equal_paths != skewed_paths

    @pytest.mark.parametrize(
        ("values", "flags"),
        [
            pytest.param([1, 1, 8], [0, 0, 1], id="last-slice-is-eighty-percent"),
            pytest.param([8, 1, 1], [1, 0, 0], id="first-slice-is-eighty-percent"),
            pytest.param([1, 1, 1], [0, 0, 0], id="all-slices-are-a-third"),
            pytest.param([6, 4], [1, 0], id="sixty-forty"),
        ],
    )
    def test_pie_large_arc_flag_is_set_only_for_slices_above_half(self, values, flags):
        labels = [f"L{index}" for index in range(len(values))]

        paths = _shapes_of(_svg("pie", "T", _data(labels, ("S", values))), "path")

        found = [re.search(r" A \S+ \S+ 0 ([01]) 1 ", path.get("d", "")) for path in paths]
        assert all(found)
        assert [int(match.group(1)) for match in found if match] == flags

    def test_line_legend_wraps_to_a_new_row_instead_of_leaving_the_image(self):
        names = [f"Seria o długiej nazwie numer {index}" for index in range(4)]

        root = _svg("line", "T", _data(["A", "B"], *[(name, [1.0, 2.0]) for name in names]))

        swatches = [element for element in root.iter() if "blog-chart-swatch" in element.get("class", "").split()]
        assert len(swatches) == 4
        assert all(float(element.get("x", "0")) + float(element.get("width", "0")) <= 640 for element in swatches)
        assert len({element.get("y") for element in swatches}) >= 2

    def test_zero_baseline_is_drawn_only_when_values_go_below_zero(self):
        def axes(values: list[float]) -> int:
            root = _svg("bar", "T", _data(["A", "B", "C"], ("S", values)))
            return len([element for element in root.iter() if "blog-chart-axis" in element.get("class", "").split()])

        assert axes([4, 2, 9]) == 2
        assert axes([4, -2, 9]) == 3

    def test_pie_with_single_label_draws_exactly_one_shape(self):
        shapes = _series_shapes(_svg("pie", "T", _data(["Całość"], ("S", [5]))))

        assert len(shapes) == 1
        assert shapes[0][1] == 1
        assert shapes[0][0] in {"path", "circle"}

    def test_pie_with_a_zero_slice_still_renders_valid_xml(self):
        root = _svg("pie", "T", _data(["A", "B", "C"], ("S", [0, 3, 1])))

        assert root.tag == f"{{{SVG_NS}}}svg"
        assert len(_series_shapes(root)) >= 2

    def test_series_class_numbers_cycle_through_one_to_six_for_bars(self):
        data = _data(["A"], *[(f"S{i}", [float(i)]) for i in range(1, 8)])

        numbers = sorted(n for _, n in _series_shapes(_svg("bar", "T", data)))

        assert numbers == [1, 1, 2, 3, 4, 5, 6]

    def test_series_class_numbers_cycle_through_one_to_six_for_pie_slices(self):
        data = _data([f"L{i}" for i in range(7)], ("S", [1, 2, 3, 4, 5, 6, 7]))

        numbers = sorted(n for _, n in _series_shapes(_svg("pie", "T", data)))

        assert numbers == [1, 1, 2, 3, 4, 5, 6]

    @pytest.mark.parametrize("chart_type", ["bar", "line"])
    @pytest.mark.parametrize(
        "values",
        [
            pytest.param([0, 0, 0], id="all-zero"),
            pytest.param([5, 5, 5], id="all-equal"),
            pytest.param([-4, 2, -9], id="mixed-sign"),
            pytest.param([1e12, 3, 2], id="huge-spread"),
        ],
    )
    def test_degenerate_values_render_valid_xml_without_exception(self, chart_type, values):
        root = _svg(chart_type, "T", _data(["A", "B", "C"], ("S", values)))

        assert root.tag == f"{{{SVG_NS}}}svg"

    def test_line_with_a_single_label_renders_one_point(self):
        root = _svg("line", "T", _data(["Jedyna"], ("S", [3])))

        assert len([1 for tag, _ in _series_shapes(root) if tag == "circle"]) == 1


# ---------------------------------------------------------------------------
# render_chart_svg: odrzucenia (None)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderChartSvgRejects:
    @pytest.mark.parametrize("chart_type", ["radar", "", "scatter", "bar chart"])
    def test_unknown_chart_type_gives_none(self, chart_type):
        assert render_chart_svg(chart_type, "T", _TWO) is None

    @pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
    @pytest.mark.parametrize(
        "data",
        [
            pytest.param(ChartData(labels=(), series=()), id="no-labels-no-series"),
            pytest.param(ChartData(labels=("A",), series=()), id="no-series"),
            pytest.param(ChartData(labels=(), series=(ChartSeries("S", ()),)), id="no-labels"),
        ],
    )
    def test_empty_data_gives_none(self, chart_type, data):
        assert render_chart_svg(chart_type, "T", data) is None

    @pytest.mark.parametrize(
        "values",
        [
            pytest.param([3, -1], id="one-negative"),
            pytest.param([-2, -5], id="all-negative"),
            pytest.param([0, 0], id="sum-zero"),
            pytest.param([4, -4], id="sum-zero-with-negative"),
        ],
    )
    def test_pie_with_negative_value_or_non_positive_sum_gives_none(self, values):
        assert render_chart_svg("pie", "T", _data(["A", "B"], ("S", values))) is None


# ---------------------------------------------------------------------------
# render_chart_svg: escapowanie i brak kolorow
# ---------------------------------------------------------------------------

_HOSTILE = [
    pytest.param("<script>alert(1)</script>", id="script"),
    pytest.param("<img src=x onerror=alert(1)>", id="img-onerror"),
    pytest.param('" onload="alert(1)', id="quote-breakout"),
    pytest.param("</title><script>alert(1)</script>", id="close-title"),
    pytest.param("]]><script>alert(1)</script>", id="cdata-close"),
]


def _assert_no_markup_from_author(root: ET.Element) -> None:
    tags = {_local(element.tag) for element in root.iter()}
    assert {"script", "img", "iframe", "foreignObject"}.isdisjoint(tags)
    assert [name for element in root.iter() for name in element.attrib if name.lower().startswith("on")] == []


@pytest.mark.unit
class TestRenderChartSvgEscaping:
    @pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
    @pytest.mark.parametrize("payload", _HOSTILE)
    def test_hostile_title_stays_text_in_the_title_element(self, chart_type, payload):
        root = _svg(chart_type, payload, _ONE)

        _assert_no_markup_from_author(root)
        assert root[0].text == payload

    @pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
    @pytest.mark.parametrize("payload", _HOSTILE)
    def test_hostile_label_stays_text(self, chart_type, payload):
        root = _svg(chart_type, "T", _data(["Spokojna", payload], ("S", [1, 2])))

        _assert_no_markup_from_author(root)
        assert payload in _all_text(root)

    @pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
    @pytest.mark.parametrize("payload", _HOSTILE)
    def test_hostile_series_name_stays_text(self, chart_type, payload):
        root = _svg(chart_type, "T", _data(["A", "B"], (payload, [1, 2])))

        _assert_no_markup_from_author(root)
        assert payload in _all_text(root)

    @pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
    def test_ampersand_and_quotes_survive_a_round_trip_through_xml(self, chart_type):
        title = "R&D 'x' <Q>"

        root = _svg(chart_type, title, _data([title], (title, [1])))

        assert root[0].text == title
        assert title in " ".join(next(element for element in root if _local(element.tag) == "desc").itertext())

    @pytest.mark.parametrize("chart_type", ["bar", "line", "pie"])
    def test_svg_carries_no_color_literals_or_inline_styles(self, chart_type):
        result = render_chart_svg(chart_type, "T", _TWO)

        assert result is not None
        assert re.search(r"#[0-9a-fA-F]{3,8}\b", result) is None
        assert re.search(r"\b(rgb|rgba|hsl|hsla)\(", result) is None
        root = ET.fromstring(result)
        assert [element for element in root.iter() if "style" in element.attrib] == []
        for element in root.iter():
            for name in ("fill", "stroke"):
                assert element.get(name, "none") in _ALLOWED_PAINT, (element.tag, name)
