"""Wykres SVG z tabeli markdown dla bloku `:::chart` wpisu bloga (MON-257).

`parse_chart_table` czyta pierwsza tabele markdown (pierwsza kolumna to etykiety, kolejne to serie
liczbowe), `render_chart_svg` buduje z niej inline SVG: slupkowy (`bar`), liniowy (`line`) albo
kolowy (`pie`, pierwsza seria). SVG powstaje wylacznie z danych sparsowanych (liczby przez `float`,
teksty autora przez `html.escape`), nigdy z surowego tekstu autora, i nie niesie kolorow: ksztalty
serii maja klasy `blog-chart-series-N`, a kolory daja zmienne CSS strony. Biblioteka standardowa
bez zaleznosci; obie funkcje nigdy nie rzucaja wyjatku.
"""

from __future__ import annotations

import html
import logging
import math
import re
from dataclasses import dataclass

logger = logging.getLogger("monolynx.blog_chart")

CHART_TYPES: tuple[str, ...] = ("bar", "line", "pie")

_SEPARATOR_CELL = re.compile(r":?-+:?")
# `float()` przyjmuje tez `1_0`, `nan` i `inf`, wiec ksztalt liczby sprawdza wzorzec.
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")

_WIDTH = 640
_HEIGHT = 360
_PALETTE_SIZE = 6
_MARGIN_LEFT = 64.0
_MARGIN_RIGHT = 16.0
_MARGIN_BOTTOM = 48.0
_MARGIN_TOP = 16.0
_LEGEND_ROW = 20.0
_KIND_NAMES = {"bar": "słupkowy", "line": "liniowy", "pie": "kołowy"}


@dataclass(frozen=True)
class ChartSeries:
    name: str
    values: tuple[float, ...]


@dataclass(frozen=True)
class ChartData:
    labels: tuple[str, ...]
    series: tuple[ChartSeries, ...]


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().removeprefix("|").removesuffix("|").split("|")]


def _number(cell: str) -> float | None:
    compact = "".join(cell.split()).replace(",", ".")
    if not _NUMBER.fullmatch(compact):
        return None
    value = float(compact)
    return value if math.isfinite(value) else None


def _build_data(header: list[str], rows: list[list[str]]) -> ChartData | None:
    if len(header) < 2 or not rows or any(len(row) != len(header) for row in rows):
        return None
    series = []
    for column in range(1, len(header)):
        values = [_number(row[column]) for row in rows]
        if any(value is None for value in values):
            return None
        series.append(ChartSeries(header[column], tuple(value for value in values if value is not None)))
    return ChartData(labels=tuple(row[0] for row in rows), series=tuple(series))


def parse_chart_table(markdown: str) -> ChartData | None:
    """Pierwsza tabela markdown -> `ChartData`; None, gdy brak tabeli, danych albo wartosc serii nie jest liczba."""
    try:
        lines = markdown.splitlines()
        for index in range(len(lines) - 1):
            if "|" not in lines[index]:
                continue
            header = _cells(lines[index])
            separator = _cells(lines[index + 1])
            if len(separator) != len(header) or not all(_SEPARATOR_CELL.fullmatch(cell) for cell in separator):
                continue
            rows = []
            for line in lines[index + 2 :]:
                if "|" not in line:
                    break
                rows.append(_cells(line))
            return _build_data(header, rows)
        return None
    except Exception:
        logger.exception("Blad parsowania tabeli wykresu wpisu bloga")
        return None


def _esc(text: str) -> str:
    # `[` jako encja: SVG trafia do strony przed `_link_footnotes`, ktory zamienilby `[^x]` w tekscie na `<sup>`.
    return html.escape(text, quote=True).replace("[", "&#91;")


def _coord(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _num(value: float) -> str:
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10g}"


def _tick(value: float) -> str:
    text = f"{value:.4g}"
    return "0" if text == "-0" else text


def _series_class(index: int) -> str:
    return f"blog-chart-series-{index % _PALETTE_SIZE + 1}"


def _swatch(index: int, x: float, y: float) -> str:
    # Klasa `blog-chart-swatch-N`, nie `blog-chart-series-N`: ta druga oznacza ksztalt danych.
    return f'<rect class="blog-chart-swatch blog-chart-swatch-{index % _PALETTE_SIZE + 1}" x="{_coord(x)}" y="{_coord(y)}" width="12" height="12"/>'


def _text(x: float, y: float, content: str, anchor: str = "middle") -> str:
    return f'<text class="blog-chart-label" x="{_coord(x)}" y="{_coord(y)}" text-anchor="{anchor}">{_esc(content)}</text>'


def _legend_row(names: list[str]) -> tuple[list[str], float]:
    """Legenda poziomo nad wykresem; zwraca elementy i wysokosc, ktora zabiera."""
    items: list[str] = []
    x, y, rows = _MARGIN_LEFT, 4.0, 1
    for index, name in enumerate(names):
        width = 34.0 + 7.0 * len(name)
        if x + width > _WIDTH - _MARGIN_RIGHT and x > _MARGIN_LEFT:
            x, y, rows = _MARGIN_LEFT, y + _LEGEND_ROW, rows + 1
        items.append(_swatch(index, x, y))
        items.append(_text(x + 18, y + 10, name, "start"))
        x += width
    return items, rows * _LEGEND_ROW


def _cartesian(chart_type: str, data: ChartData) -> list[str]:
    labels, series = data.labels, data.series
    count = len(labels)
    legend, legend_height = _legend_row([item.name for item in series]) if len(series) > 1 else ([], 0.0)
    left, right = _MARGIN_LEFT, _WIDTH - _MARGIN_RIGHT
    top, bottom = _MARGIN_TOP + legend_height, _HEIGHT - _MARGIN_BOTTOM
    values = [value for item in series for value in item.values]
    low, high = min(0.0, min(values)), max(0.0, max(values))
    if high == low:
        high = low + 1.0

    def y_of(value: float) -> float:
        return bottom - (value - low) / (high - low) * (bottom - top)

    zero = y_of(0.0)
    body = list(legend)
    for step in range(5):
        level = low + (high - low) * step / 4
        y = y_of(level)
        body.append(f'<line class="blog-chart-grid" x1="{_coord(left)}" y1="{_coord(y)}" x2="{_coord(right)}" y2="{_coord(y)}"/>')
        body.append(_text(left - 8, y + 4, _tick(level), "end"))
    body.append(f'<line class="blog-chart-axis" x1="{_coord(left)}" y1="{_coord(top)}" x2="{_coord(left)}" y2="{_coord(bottom)}"/>')
    body.append(f'<line class="blog-chart-axis" x1="{_coord(left)}" y1="{_coord(bottom)}" x2="{_coord(right)}" y2="{_coord(bottom)}"/>')
    if low < 0:
        body.append(f'<line class="blog-chart-axis" x1="{_coord(left)}" y1="{_coord(zero)}" x2="{_coord(right)}" y2="{_coord(zero)}"/>')

    if chart_type == "bar":
        group = (right - left) / count
        bar_width = group * 0.7 / len(series)
        centers = [left + group * (index + 0.5) for index in range(count)]
        for index in range(count):
            for position, item in enumerate(series):
                y = y_of(item.values[index])
                body.append(
                    f'<rect class="{_series_class(position)}" x="{_coord(left + group * (index + 0.15) + position * bar_width)}" '
                    f'y="{_coord(min(y, zero))}" width="{_coord(bar_width)}" height="{_coord(abs(y - zero))}"/>'
                )
    else:
        if count == 1:
            centers = [(left + right) / 2]
        else:
            pad = 24.0
            step_x = (right - left - 2 * pad) / (count - 1)
            centers = [left + pad + step_x * index for index in range(count)]
        for position, item in enumerate(series):
            points = [(centers[index], y_of(value)) for index, value in enumerate(item.values)]
            joined = " ".join(f"{_coord(x)},{_coord(y)}" for x, y in points)
            body.append(f'<polyline class="{_series_class(position)}" fill="none" points="{joined}"/>')
            body.extend(f'<circle class="{_series_class(position)}" cx="{_coord(x)}" cy="{_coord(y)}" r="4"/>' for x, y in points)

    body.extend(_text(centers[index], bottom + 18, label) for index, label in enumerate(labels))
    return body


def _pie(data: ChartData) -> list[str] | None:
    values = data.series[0].values
    total = sum(values)
    if any(value < 0 for value in values) or total <= 0:
        return None
    cx, cy, radius = 170.0, _HEIGHT / 2, 140.0
    body: list[str] = []
    start = -math.pi / 2
    for index, value in enumerate(values):
        fraction = value / total
        if fraction == 0:
            continue
        css = _series_class(index)
        if fraction >= 1 - 1e-9:
            body.append(f'<circle class="{css}" cx="{_coord(cx)}" cy="{_coord(cy)}" r="{_coord(radius)}"/>')
            continue
        end = start + 2 * math.pi * fraction
        x1, y1 = cx + radius * math.cos(start), cy + radius * math.sin(start)
        x2, y2 = cx + radius * math.cos(end), cy + radius * math.sin(end)
        large = 1 if fraction > 0.5 else 0
        body.append(
            f'<path class="{css}" d="M {_coord(cx)} {_coord(cy)} L {_coord(x1)} {_coord(y1)} '
            f'A {_coord(radius)} {_coord(radius)} 0 {large} 1 {_coord(x2)} {_coord(y2)} Z"/>'
        )
        start = end
    spacing = min(24.0, (_HEIGHT - 40.0) / len(values))
    for index, (label, value) in enumerate(zip(data.labels, values, strict=True)):
        y = 20.0 + spacing * index
        percent = f"{value / total * 100:.1f}".replace(".", ",")
        body.append(_swatch(index, 340, y))
        body.append(_text(358, y + 10, f"{label} ({percent} %)", "start"))
    return body


def _description(chart_type: str, data: ChartData) -> str:
    kind = f"Wykres {_KIND_NAMES[chart_type]}."
    if chart_type == "pie":
        item = data.series[0]
        entries = [f"{label}: {_num(value)}" for label, value in zip(data.labels, item.values, strict=True)]
        return f"{kind} Seria {item.name}. {'; '.join(entries)}."
    entries = [f"{label}: {', '.join(f'{item.name} {_num(item.values[index])}' for item in data.series)}" for index, label in enumerate(data.labels)]
    return f"{kind} {'; '.join(entries)}."


def render_chart_svg(chart_type: str, title: str, data: ChartData) -> str | None:
    """Typ, tytul i dane -> napis `<svg>`; None, gdy typ jest nieznany albo danych nie da sie narysowac."""
    try:
        if chart_type not in CHART_TYPES or not data.labels or not data.series:
            return None
        if any(len(item.values) != len(data.labels) or not all(math.isfinite(value) for value in item.values) for item in data.series):
            return None
        body = _pie(data) if chart_type == "pie" else _cartesian(chart_type, data)
        if body is None:
            return None
        return (
            f'<svg class="blog-chart-svg" role="img" viewBox="0 0 {_WIDTH} {_HEIGHT}" xmlns="http://www.w3.org/2000/svg">'
            f"<title>{_esc(title.strip() or 'Wykres')}</title><desc>{_esc(_description(chart_type, data))}</desc>{''.join(body)}</svg>"
        )
    except Exception:
        logger.exception("Blad renderowania wykresu wpisu bloga")
        return None
