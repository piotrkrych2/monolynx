# Technical Writer — pamięć projektu Monolynx

## Konwencje pisania
- Dokumenty PL: pełne diakrytyki (ą ć ę ł ń ó ś ź ż), ZERO em-dash `—` (sprawdź `grep -c "—" <plik>` = 0). Zamiana em-dash na hyphen `-` (NIE kasuj myślnika, NIE zamieniaj na przecinek). Bulk: `LC_ALL=en_US.UTF-8 perl -CSD -i -pe 's/\x{2014}/-/g' <plik>`. UWAGA: `grep -c $'\u2014'` daje FALSZYWE ZERO - bash 3.2 na macOS nie zna `$'\uXXXX'`. Licz Pythonem: `python3 -c "import pathlib;print(pathlib.Path('f.md').read_text().count(chr(8212)))"` albo wklej znak dosłownie do grepa. To samo dla podmiany perlem - weryfikuj wynik, cichy no-op jest łatwy.
- CLAUDE.md jest w angielskim i używa strzałki `→` jako separatora sekwencji (już występuje w pliku, spójne). UWAGA: CLAUDE.md ma ~100 istniejących em-dash jako separator bulletów `nazwa.py — opis` (cudze, poza zakresem - NIE ruszać). Targetowane edycje: liczy się tylko czy MOJE dodane/nowe linie mają 0 em-dash. Check: `grep -c "—"` przed i po musi być równe (zero netto). Najprostszy check "zero netto" dla targetowanych edycji: `git -C <repo> diff -U0 -G "—" --stat -- <pliki>` (puste = żadna dodana/usunięta linia nie ma em-dash; pojedyncza komenda przechodzi guard worktree). Nowe bullety w sekcji Services pisz z hyphenem `-` jako separator, nie kopiuj em-dash z sąsiednich.
- Wszystkie komendy Pythona przez `docker compose exec app`, nigdy lokalnie.
- [Konwencje docs pluginu](plugin_docs_conventions.md) — SKILL.md BEZ diakrytyk, README pluginu Z nimi; zero em/en-dash (walidator); static/skills generowane przez sync_skills.py.
- `bash_guard.py` łapie literał `glab mr create` także w argumencie `grep` (deny dla subagenta). Szukaj fragmentem, np. `-e "mr crea"`. To samo dla `ruff` i `git add`: deny także w treści heredoca `python3 - <<EOF` i w `grep`. Grep: `r[u]ff`; podmiany z tymi słowami rób narzędziem Edit (MON-207).
- Prompt TM często zakazuje uruchamiania Pythona - wtedy wielokrotne podmiany rób narzędziem Edit (albo `sed -i ''` z literalną ścieżką), nie `python3` heredoc (MON-177: złamany zakaz, zgłoszony w raporcie).
- W sesji w worktree guard Bash odrzuca komendy złożone (heredoc + `wc` + `grep` w jednym wywołaniu) jako "too complex to verify". Nowe pliki twórz narzędziem Write, weryfikacje odpalaj osobnymi, prostymi komendami.

## Architektura / dystrybucja
- [Plugin vs install_monolynx_skills](project_plugin_vs_install_skills.md) — dwie równoległe ścieżki dystrybucji skilli, obie utrzymywane; mcp_server.py bez zmian.
- [Metoda LLM Wiki](project_wiki_llm_method.md) — regulamin (DEFAULT_WIKI_SCHEMA), strony systemowe wiki-index/wiki-log/wiki-schema, narzędzia MCP z gatingiem, skille wiki-init/ingest/lint + QUERY w search.
- [Pokrycie nowego skilla](plugin_skill_coverage_pages.md) - 6 stron wg DEFAULT_PAGES walidatora + licznik skilli w 4 miejscach, którego walidator NIE sprawdza.

## Raportowanie do ticketów (obowiązkowe)
- Po każdej sesji: komentarz PL (`add_comment`) + log czasu (`log_time`, min 1 min) na tickecie. MCP: `mcp__claude_ai_Monolynx__*`.
- WYJĄTEK: gdy prompt TM / `common-rules.md` mówi "NIE dodawaj komentarzy ani log_time - to robi TM", ten zakaz wygrywa z domyślnym obowiązkiem (inaczej dubel czasu). Raportujesz wtedy tylko do pipeline joba + SendMessage (MON-228).
- Raport końcowy do Team Managera wysyłaj przez `SendMessage(to="main")`. Nazwa sesji z promptu (`monolynx-<KEY>-<model>`) adresuje TĘ sesję, nie TM - wysyłka na nią kończy się błędem "no agent reachable". W raporcie napisz, że komentarz i `log_time` już dodałeś, żeby TM nie zalogował czasu drugi raz.

## Changelog pluginu w tickecie
- Walidator z MON-177 obsługuje `### Unreleased` i wymaga klucza ticketu na początku KAŻDEGO punktu pod nim (`- MON-123: opis`), nie tylko pierwszego (MON-178).
- Wpis idzie pod `### Unreleased` (reguła `plugin-version-bump.md` pkt 1), ale `check_version_matches_changelog` w `scripts/validate_plugin_consistency.py` porównuje PIERWSZY nagłówek `^###\s*(\S+)` z `version` w `plugin.json`, więc `Unreleased` da naruszenie w CI. Numer nadaje mergujący (pkt 2). Jeśli masz `plugin.json` na liście "nie ruszać", zrób pkt 1 i zgłoś ten konflikt Team Managerowi zamiast bumpować samemu.

## Reguły z review
- Nowa integracja dostępna użytkownikowi musi być opisana na landing page w obu obsługiwanych językach (MON-182).
- Komendę, którą skill ma faktycznie wykonać, uruchamiasz w tym samym tickecie w środowisku docelowym (dla `work`/`work-simple` to sesja w git worktree) i piszesz ją bez `$VAR` i `$(...)` - guard izolacji worktree odrzuca każdy argument wyliczany w runtime (MON-184). Wzorzec obejścia (potwierdzony wykonaniem): osobna komenda `echo $HOME` (albo `git rev-parse ...`) wypisuje ścieżkę, druga komenda używa jej literalnie. `echo $VAR` przechodzi, `find "$HOME/..."` już nie. Ten sam wzorzec jest w regule `.claude/rules/docs-commands-verified.md` pkt 3.
- W `--help` (clap/argparse) flaga z wartością ma placeholder (`-C, --cd <DIR>`), przełącznik go nie ma (`--worktree`). Sprawdzaj nie tylko ISTNIENIE flagi, ale i to, czy bierze argument - podanie wartości przełącznikowi przesuwa ją na pierwszy argument pozycyjny i narzędzie odrzuca całe wywołanie (MON-185: `codex exec --worktree <KEY>` zjadało prompt). To samo dotyczy cudzego kodu, który powołuje się na moją weryfikację flagi.
- bash_guard dopasowuje `glab mr create` jako substring: subagent nie uruchomi nawet `glab mr create --help` ani `grep "glab mr create"`. Weryfikację flag zleć TM (główna sesja) od razu na starcie, nie obchodź guarda inną pisownią (MON-178).
- Prompt skilla Codeksa w komendzie powłoki pisz w APOSTROFACH: `'$monolynx:work <KEY>'`. W cudzysłowie powłoka rozwinie `$monolynx` do pustego napisu i zostanie `:work <KEY>`.
- Bump wersji pluginu dotyczy DWÓCH manifestów: `plugin/.claude-plugin/plugin.json` i `plugin/.codex-plugin/plugin.json`. `tests/unit/test_plugin_distribution.py` asertuje ich równość, a `scripts/validate_plugin_consistency.py` (stage `lint` w CI) wymaga zgodności wersji z pierwszym wpisem `### X.Y.Z` changelogu - czyli `### Unreleased` oblewa CI, dopóki walidator go nie obsługuje.

## Reguly z review krytyka

- Zmieniając zachowanie skilla, poprawiasz każdą deklarację tego zachowania w plikach, które i tak edytujesz w tym diffie (frontmatter, tabela skilli w README, changelog), po grepie starego sformułowania. (MON-206)
