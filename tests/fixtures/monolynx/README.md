# Fixture'y CLI `monolynx`

Dwa zestawy przechwyceń: wydane CLI 0.1.0 (pliki `*.0.1.0.txt`) i CLI zbudowane z drzewa źródeł repo
(pliki `*.source.txt`, sekcja "Pliki z drzewa źródeł" niżej).

## Wydanie 0.1.0

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

## Pliki z drzewa źródeł

Flagi `--compact` (`ticket list`) i `--brief` (`ticket create`, `ticket update`) z MON-247 nie są jeszcze wydane, więc
wydane CLI nie pokaże ich w `--help`. Ich `--help` jest przechwycone z CLI zbudowanego z katalogu `cli/` repo, w
jednorazowym kontenerze (montowanie tylko do odczytu, pakiet kopiowany do środka):

```bash
docker run --rm -e NO_COLOR=1 -e COLUMNS=200 -e TERM=dumb -v <repo>/cli:/src:ro python:3.10-slim sh -c "cp -r /src /cli && pip install -q /cli && monolynx ticket <komenda> --help"
```

`<repo>` to absolutna ścieżka checkoutu, `<komenda>` to `list`, `create` albo `update`; jedno uruchomienie na plik,
wyjście `--help` zapisane do pliku.

Nazwa pliku: `<komenda z myślnikami>-help.source.txt`. Nagłówek meta i linia `# exit: <kod>` są takie jak w plikach
0.1.0; druga linia nagłówka to `# tool_version: monolynx 0.2.0 (drzewo zrodel, MON-247, niewydane)`.

| Plik | Komenda | Uwagi |
|---|---|---|
| `ticket-list-help.source.txt` | `monolynx ticket list --help` | zawiera `--compact` |
| `ticket-create-help.source.txt` | `monolynx ticket create --help` | zawiera `--brief` |
| `ticket-update-help.source.txt` | `monolynx ticket update --help` | zawiera `--brief` |
| `graph-help.source.txt` | `monolynx graph --help` | grupa `graph` z MON-248 (w 0.1.0 pusty placeholder) |
| `graph-nodes-help.source.txt` | `monolynx graph nodes --help` | `--type`, `--search`, `--limit` |
| `graph-node-help.source.txt` | `monolynx graph node --help` | `--depth`, powtarzalne `--relation-type` i `--node-type` |
| `graph-query-help.source.txt` | `monolynx graph query --help` | `--node-type`, `--search`, `--limit` |
| `graph-path-help.source.txt` | `monolynx graph path --help` | wymagane `--source-id` i `--target-id` |
| `graph-stats-help.source.txt` | `monolynx graph stats --help` | bez opcji |
| `root-help.source.txt` | `monolynx --help` | lista grup z grupą `blog` z MON-261 (wydanie 0.1.0 jej nie ma); przechwycone ponownie w MON-263, bo opis grupy `blog` wymienia publikację i jej cofnięcie |
| `blog-help.source.txt` | `monolynx blog --help` | grupa `blog` z MON-261: komendy `list`, `get`, `meta`, `lint` (MON-262), `publish` i `unpublish` (MON-263); przechwycone ponownie w MON-263 |
| `blog-list-help.source.txt` | `monolynx blog list --help` | `--status` |
| `blog-get-help.source.txt` | `monolynx blog get --help` | argument `page_id` |
| `blog-lint-help.source.txt` | `monolynx blog lint --help` | argument `page_id`; `blog lint` z MON-262, kod wyjścia 1 przy błędzie lintu (przechwycone z drzewa źródeł MON-262) |
| `blog-meta-help.source.txt` | `monolynx blog meta --help` | argument `page_id`; `--slug`, `--description`, `--author`, `--tag`, `--no-tags`, `--lang`, `--translation-of`, `--cover`, `--verified` |
| `blog-publish-help.source.txt` | `monolynx blog publish --help` | argument `page_id`; przełącznik `--force` (bez wartości); `blog publish` z MON-263, kod wyjścia 1 przy odmowie przez lint |
| `blog-unpublish-help.source.txt` | `monolynx blog unpublish --help` | argument `page_id`, bez opcji; `blog unpublish` z MON-263 |

Pliki `graph-*` mają w nagłówku `# tool_version: monolynx 0.2.0 (drzewo zrodel, MON-248, niewydane)`; komenda
przechwycenia jest ta sama, z `monolynx graph <komenda> --help` (albo `monolynx graph --help`) na końcu.

Pliki `blog-list-help`, `blog-get-help` i `blog-meta-help` mają w nagłówku `# tool_version: monolynx 0.3.0 (drzewo zrodel, MON-261, niewydane)`, `blog-lint-help` ten sam z `MON-262`, a `blog-publish-help`, `blog-unpublish-help`, `blog-help` i `root-help.source.txt` z `MON-263` (dwa ostatnie przechwycone ponownie po dodaniu komend publikacji);
komenda przechwycenia jest ta sama, z `monolynx blog <komenda> --help`, `monolynx blog --help` albo `monolynx --help` na końcu.

Pierwszeństwo: `fixture_text(name)` w `tests/unit/test_monolynx_op.py` bierze `<name>-help.source.txt`, gdy taki plik
istnieje, a w przeciwnym razie `<name>-help.0.1.0.txt`; dla `root` oznacza to `root-help.source.txt` przed `root-help.0.1.0.txt`. Pliki `ticket-list-help.0.1.0.txt`, `ticket-create-help.0.1.0.txt`
i `ticket-update-help.0.1.0.txt` zostają bez zmian jako zapis wydania 0.1.0. Plik `.source.txt` zmieniasz tylko przez
ponowne przechwycenie tą komendą, nigdy ręcznie.

PyPI: `tests/fixtures/pypi/monolynx-cli-json.json` (`curl -s https://pypi.org/pypi/monolynx-cli/json`, przycięte do
`info.name`, `info.version` i kluczy `releases`).
