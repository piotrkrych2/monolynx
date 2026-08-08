"""Generuje warianty progresywne diagramu Spektrum z pliku bazowego."""

import pathlib
import subprocess

HERE = pathlib.Path(__file__).parent
BASE = (HERE / "monolynx-spektrum.svg").read_text(encoding="utf-8")

for n in range(1, 6):
    svg = BASE
    for k in range(n + 1, 6):
        svg = svg.replace(f'<g id="step-{k}">', f'<g id="step-{k}" class="dim">')
    if n < 5:
        svg = svg.replace('<g id="loop">', '<g id="loop" class="dim">')

    out = HERE / f"monolynx-spektrum-{n}.svg"
    out.write_text(svg, encoding="utf-8")
    subprocess.run(
        ["rsvg-convert", "-w", "3840", str(out), "-o", str(out.with_suffix(".png"))],
        check=True,
    )
    print(f"{out.name} + PNG")
