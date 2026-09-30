"""Wersja pakietu monolynx-cli, jedyne miejsce edycji numeru.

`pyproject.toml` czyta tę wartość dynamicznie przez `[tool.hatch.version]`
(`path = "src/monolynx_cli/version.py"`), więc `version` w `[project]` nie
istnieje jako pole statyczne. Job `publish-pypi` / `publish-testpypi` w CI
porównuje tę wartość z tagiem `cli-v<wersja>` przed uploadem na PyPI.
"""

__version__ = "0.1.0"
