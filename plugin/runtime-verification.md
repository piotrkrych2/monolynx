# Weryfikacja runtime MON-183

Data: 2026-09-17. Zmiany sprawdzano na osobnym branchu od `c56b584`, zawierającego dystrybucję MON-182. Ten dokument oddziela testy kontraktu od wykonania workflow przez model klienta.

## Środowiska

- Codex CLI 0.154.0: wersja i interfejs `exec --help` odczytane lokalnie. Bieżąca sesja Codex udostępnia natywne delegowanie agentów, katalog narzędzi MCP oraz terminal zwracający identyfikator procesu. MON-183 jest realizowany tymi narzędziami.
- Claude Code CLI 2.1.273: interfejs CLI odczytany lokalnie. Sesja `monolynx-MON-183-opus` przy próbie wykonania `work` zatrzymała się na limicie wydatków konta przed implementacją. Nie jest to wynik testu zmienionych skilli.
- Testy Python uruchamiane w kontenerze z zamontowanym worktree. Nie sprawdzano kodu przez `docker compose exec` głównego checkoutu.

## Testy kontraktu i dystrybucji

Przed implementacją: 18 niepowodzeń i 40 poprawnych testów. Brakowało funkcji sprawdzającej runtime, dołączania kanonicznej referencji oraz błędu przy brakującym źródle.

Po implementacji pierwszego zestawu: 60 testów poprawnych. Przypadki obejmują brak lub niekompletność kontraktu, wywołania API klienta poza oznaczonym wariantem, wariant Claude z poprawnym zamknięciem, wyjaśnienia fallbacku, brakujące i zewnętrzne referencje oraz bundlowanie wspólnego źródła.

Po integracji: pełne `pytest tests/ --cov=src/monolynx --cov-report=term` zakończone kodem 0, 3580 testów poprawnych, 1 pominięty, 37 ostrzeżeń, pokrycie 91,28%, czas 1685,84 s. Ruff dla `src/`, `tests/` i dwóch zmienianych skryptów oraz mypy dla `src/` zakończone poprawnie. `sync_skills.py --check` potwierdził zgodność 23 skilli, a `validate_plugin_consistency.py` spójność pluginu. Polecenia wykonano w kontenerze z bind mountem całego worktree do `/app`.

Po review doprecyzowano odczyt niesekretnych pól konfiguracji klienta i przywrócono dotychczasowy status częściowego raportu mutacji. Po tych zmianach instrukcji: 62 testy synchronizacji i walidatora poprawne, 1,75 s; synchronizacja, walidator, Ruff, kontrola formatowania 219 plików i mypy ponownie poprawne. Pełnego zestawu aplikacji nie powtarzano; kod Python po review nie zmienił się.

Testy walidatora są deterministycznym sprawdzeniem dokumentów i pakietów. Nie dowodzą, że model klienta poprawnie wykona każdą instrukcję w dowolnej sesji.

## Ręczne scenariusze klientów

Każdy scenariusz wykonuje się w osobnym projekcie próbnym, z nową dystrybucją i bez narzędzi zapisu do rzeczywistych projektów. Nie wyłącza się zabezpieczeń klienta. Należy zapisać faktycznie użyte narzędzia i wynik, nie tylko deklarację modela.

| Scenariusz | Warunki i oczekiwany wynik | Codex | Claude Code |
|---|---|---|---|
| Argument jawny | Jawny slug projektu próbnego różni się od konfiguracji; odczyt MCP używa jawnej wartości. Literalny znacznik argumentów nie jest przekazywany. | Wybrano `runtime-probe-explicit` zamiast `runtime-probe-env`; bez wywołania MCP, więc przekazanie argumentu do API pozostaje niepotwierdzone | Niewykonany: limit konta |
| Brak MCP | Dostarczony skill wymaga narzędzia projektu; katalog go nie zawiera. Agent wskazuje brak i nie wymyśla nazwy ani wyniku. | Zatrzymanie bez wymyślania wyników; katalog nadal ujawniał MCP, więc sprawdzono brak autoryzacji do użycia, nie brak narzędzia | Niewykonany: limit konta |
| Brak sluga | Brak argumentu, zmiennej procesu i ustawień projektu. Agent pyta o slug przed operacją MCP; nazwa katalogu i ticketu nie są fallbackiem. | Odczyt pustej zmiennej, prośba o slug, brak wywołania MCP | Niewykonany: limit konta |
| Skill zależny | Katalog zawiera instrukcje zależnego skilla, brak natywnego narzędzia jego wywołania. Agent odczytuje dostarczony plik i stosuje instrukcje; nie zgaduje ścieżki. | Odczyt dostarczonego `brief` i lokalnego runtime, propozycja kontraktu, bez rozpoczęcia implementacji i zapisów | Niewykonany: limit konta |

Cztery próby Codex zakończyły się kodem 0. Uruchomienie: `codex exec --ignore-user-config --ephemeral --sandbox read-only --skip-git-repo-check -C <katalog-próby> --json -o <scenariusz>.final.txt <prompt>`. Zapisano argumenty i prompty (`*.command.json`), transkrypty (`*.jsonl`), odpowiedzi końcowe, stderr oraz kody wyjścia. Runner miał limit 120 sekund i kończył grupę procesów przy przekroczeniu; limit nie zadziałał. Próby nie wykonywały operacji MCP ani zapisów produkcyjnych.

`--ignore-user-config` nie zapewnił nieobecności MCP w katalogu tej instalacji. Nie należy na podstawie tych prób zaliczać pełnego scenariusza brakującego MCP ani całego kryterium ręcznych testów obu klientów. Próby użyły kopii kontraktu sprzed końcowego doprecyzowania wyjątków niewymagających projektu, odczytu pól klienta i przekazywania konfiguracji do procesu potomnego; nie powtarzano ich po tych zmianach.

W bieżącej pracy Codex potwierdzono korzystanie z rzeczywistych nazw `mcp__codex_apps__monolynx_*`, odczyt instrukcji `work` z otrzymanej ścieżki, delegowanie przez dostępne narzędzia współpracy i oczekiwanie na wynik procesu po jego identyfikatorze. Sesja ma jednak skonfigurowany projekt i MCP, dlatego nie zastępuje prób brakujących możliwości z tabeli.

## Granice

- Macierz README opisuje wymagania i dostępne warianty, nie deklaruje pełnego E2E wszystkich 23 skilli.
- Backend sesji `sprint-run` pozostaje oparty na CLI Claude. Natywny backend Codex należy do MON-185.
- Definicje agentów, pamięć i egzekwowanie hooków między klientami należą do MON-184.
- Nie wykonywano publikacji, produkcyjnych zapisów w smoke ani zmian limitu wydatków konta.
