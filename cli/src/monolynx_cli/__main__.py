"""Umożliwia `python -m monolynx_cli`, wołając ten sam obiekt Typer co entry pointy CLI."""

from __future__ import annotations

from monolynx_cli.main import app

if __name__ == "__main__":
    app(prog_name="monolynx")
