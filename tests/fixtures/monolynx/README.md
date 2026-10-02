# Fixture'y CLI `monolynx` 0.1.0

Wyjście realnego `/opt/homebrew/bin/monolynx` (wersja 0.1.0, formuła Homebrew), przechwycone 2026-10-01 z
`NO_COLOR=1 COLUMNS=200 TERM=dumb` (reguła `.claude/rules/external-tool-output.md`). Przechwycono wyłącznie komendy
tylko-do-odczytu: `--version`, `--help`, `<grupa> --help`, `<grupa> <komenda> --help`, `version --check`
(komenda nie istnieje w 0.1.0, kod 2) oraz `auth whoami`.

Nazwa pliku: `<komenda z myślnikami>.0.1.0.txt`; sufiks `-help` oznacza wywołanie z `--help`. Pierwsze linie
`# command`, `# tool_version`, `# captured`, `# env`, `# note` to metadane (testy pomijają linie zaczynające się od `# `),
ostatnia linia `# exit: <kod>` to kod wyjścia. Ciało zawiera stdout i stderr w kolejności zapisu.

| Plik | Komenda | Uwagi |
|---|---|---|
| `version-flag` | `monolynx --version` | wypisuje `0.1.0`, kod 0 |
| `root-help`, `<grupa>-help` | `monolynx --help`, `monolynx <grupa> --help` | grupy: auth, config, project, member, ticket, sprint, wiki, a dla zagnieżdżonych `ticket-comment`, `ticket-label`, `ticket-ac`, `wiki-config` |
| `<grupa>-<komenda>-help` | `monolynx <grupa> <komenda> --help` | m.in. `ticket-comment-add-help`, `ticket-update-help`, `wiki-config-get-help` |
| `version-check` | `monolynx version --check` | `No such command 'version'`, kod 2 (CLI 0.1.0 nie ma komendy `version`, stąd fallback na PyPI) |
| `auth-whoami` | `monolynx --timeout 5 -o json auth whoami` | zalogowany; e-mail zredagowany na `user@example.com`, token tylko w postaci zamaskowanej przez CLI (`****` + 4 znaki) |
| `auth-whoami-unauthenticated` | `HOME=<pusty katalog> monolynx --timeout 5 -o json auth whoami` | pusty `HOME` daje pustą konfigurację (platformdirs rozwija `~`), więc przechwycenie nie dotknęło konfiguracji użytkownika; stderr `Błąd: Brak tokenu...`, kod 3 |

Nieprzechwycone (świadomie): `auth login`, `auth logout`, `config set` i każda komenda zapisu na platformie. Komendy
zapisu zna tylko `--help`. Format JSON `version --check` (komenda dodana w CLI 0.2.0, w repo od MON-232) nie ma
przechwycenia, bo 0.2.0 nie jest jeszcze wydane; testy opisują go z kodu `cli/src/monolynx_cli/commands/version.py`.

PyPI: `tests/fixtures/pypi/monolynx-cli-json.json` (`curl -s https://pypi.org/pypi/monolynx-cli/json`, przycięte do
`info.name`, `info.version` i kluczy `releases`).
