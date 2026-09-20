# Weryfikacja runtime

Dokument zbiera stan faktyczny prób dla kolejnych ticketów. Kolejność: najnowszy ticket na górze.

---

# MON-184: agenci, pamięć i hooki w obu klientach

Data: 2026-09-20. Zakres: definicje ról, lokalizacja trwałych reguł, ładowanie i zaufanie do hooków, wykrywanie braku aktywnej ochrony. Sekcja opisuje **wyłącznie to, co faktycznie wykonano**; wszystko, czego nikt nie uruchomił, jest oznaczone jako niezweryfikowane w przebiegu.

## Współpraca dev / krytyk

| Klient | Co wykonano | Stan |
|---|---|---|
| Claude Code | Rzeczywisty przebieg zespołowy nad tym ticketem: trzech wykonawców z **rozłącznym przydziałem plików** (hook i skrypt ról, testy regresyjne, dokumentacja i skille) uruchomionych natywnym mechanizmem agentów, komunikacja przez mechanizm wiadomości klienta, raport końcowy od każdego wykonawcy oraz **osobny krytyk** oceniający według rubryki `work/review-rubric.md`. | Wykonano w tej sesji. Wpis powstał przed zamknięciem przebiegu, więc werdykt krytyka i liczba iteracji poprawkowych nie są tu zapisane - są w komentarzach ticketu MON-184 i w pipeline tego ticketu. |
| Codex | **Nie uruchomiono.** Brak CLI Codex w tym środowisku. | Niezweryfikowane w przebiegu. Dostępne są wyłącznie ustalenia z dokumentacji producenta (niżej). |

Wniosek, którego **nie** wolno z tego wyciągnąć: że instrukcje `work` / `work-simple` wykonają się poprawnie w Codex. Sprawdzono tam wyłącznie kształt kontraktu hooków, nie wykonanie workflow.

## Ustalenia o hookach w Codex (z dokumentacji, bez uruchomienia)

Źródło: `tests/fixtures/codex/README.md` - dokumentacja producenta („Hooks | Codex”, `https://learn.chatgpt.com/docs/hooks`) pobrana 2026-09-20, z cytatami dosłownymi. Nie jest to output CLI: Codex nie był zainstalowany w tym środowisku, więc fixture opisuje **udokumentowany kształt JSON**, nie przechwycone wywołanie procesu (zgodnie z `.claude/rules/external-tool-output.md`).

1. **PreToolUse w Codex nie niesie `agent_id` ani `agent_type`.** Pola te są udokumentowane wyłącznie dla `SubagentStart` / `SubagentStop`, a `session_id` subagenta jest identyczny z sesją rodzica. Konsekwencja: `is_subagent` w `bash_guard.py` zawsze wylicza `False`, więc twarde blokady subagentów nie działają - subagent dostaje traktowanie sesji głównej.
2. **`permissionDecision: "ask"` jest w Codex „parsed but not supported yet”** - klient oznacza hook jako failed, raportuje błąd i **wykonuje komendę mimo to** (fail-open). Mechanizm zgód sesji głównej (`git push`, lint/testy, `glab mr create`, merge) tam nie działa.

Oba ustalenia są udokumentowane, nie odgadnięte, ale **żadne z nich nie zostało potwierdzone uruchomieniem Codex**.

## Reakcja w kodzie i dokumentacji (co z tego wynikło)

Z kształtu payloadu nie da się rozpoznać klienta: brak `agent_id` występuje także w sesji głównej Claude Code. Dlatego hook nie zgaduje klienta, tylko zostawia ślad: przy każdym wywołaniu PreToolUse z rozpoznanym `session_id` zapisuje best-effort marker `~/.cache/monolynx/hook-active/<session_id>` z polem `subagent_identification`. Skille `work`, `work-simple` i `mr-queue` czytają go i raportują jeden z trzech stanów (potwierdzona / niepełna / brak potwierdzenia); w dwóch ostatnich nie deklarują deterministycznej ochrony i egzekwują zgody rozmową z użytkownikiem. Opcjonalna, domyślnie wyłączona zmienna `MONOLYNX_HOOK_ASK_UNSUPPORTED=true` zamienia `ask` na `deny` dla klientów, które `ask` ignorują.

## Testy i walidatory

| Sprawdzenie | Wynik | Kto i kiedy |
|---|---|---|
| `python3 scripts/validate_plugin_consistency.py` (em-dash, referencje kroków, nazwy komend) | **OK** przy pominięciu nagłówka `### Unreleased` w kopii README (`--readme <kopia>`); na właściwym README ta sama komenda zwraca `plugin.json: version '1.16.2' nie zgadza sie z pierwszym wpisem changeloga 'Unreleased'` i kod 1 - zgodnie z punktem 4 `.claude/rules/plugin-version-bump.md` (walidator nie obsługuje jeszcze `Unreleased`). Numer wersji nadaje mergujący. | 2026-09-20, autor sekcji dokumentacyjnej |
| Odczyt markera hooka w sesji izolowanej w git worktree (`.claude/worktrees/MON-184`), czyli w tym samym środowisku, w którym `sprint-run` startuje `/monolynx:work --worktree` | **Wykonano.** Wariant jednolinijkowy `find "$HOME/.cache/monolynx/hook-active" ...` został **odrzucony** przez guard izolacji worktree („runs find with a value computed at runtime (the variable HOME)”), więc skille czytają marker dwiema komendami: `echo $HOME` -> `/Users/piotrkrych`, a następnie `find /Users/piotrkrych/.cache/monolynx/hook-active -type f -mmin -5 -exec cat {} + 2>/dev/null` -> `{"subagent_identification": false, "checked_at": "2026-09-20T10:34:55.460604+00:00"}`. Komenda wykonała się i zwróciła marker; wartość `false` jest oczekiwana dla payloadu sesji głównej. | 2026-09-20, autor sekcji dokumentacyjnej |
| `tests/unit/test_bash_guard.py`, `tests/unit/test_brief_guard.py`, `tests/unit/test_sync_agents.py` | **Niezweryfikowane w przebiegu przez autora tej sekcji** - hook blokuje uruchamianie testów subagentom i nie obchodzi się go. Wynik pełnego przebiegu jest u Team Managera (gate lint/testy przed review). | 2026-09-20 |
| Smoke `brief_guard` / `bash_guard` w Codex | **Nie uruchomiono** - brak CLI. | - |

## Granice tej sekcji

- Żaden scenariusz nie został wykonany w Codex; wszystkie wnioski o tym kliencie pochodzą z dokumentacji producenta.
- Odczyt markera aktywnej ochrony sprawdzono wykonaniem w sesji Claude Code izolowanej w git worktree (tabela wyżej). W realnej sesji Codex nie był sprawdzony.
- Nie zmieniano uprawnień ani zaufania do hooków w żadnym kliencie, nie wykonywano publikacji ani zapisów produkcyjnych.

---

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
