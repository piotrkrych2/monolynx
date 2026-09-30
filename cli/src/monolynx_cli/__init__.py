"""Pakiet monolynx-cli: instalowalny klient linii komend platformy Monolynx.

Niezależny od pakietu backendu `monolynx` -- nie importuje z niego niczego (patrz
tests/test_main.py::test_package_never_imports_backend_monolynx_package).
"""

from __future__ import annotations

from monolynx_cli.version import __version__

__all__ = ["__version__"]
