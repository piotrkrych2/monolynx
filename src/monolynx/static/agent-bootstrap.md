<!--
  PLACEHOLDER ENDPOINTU MCP: MCP_ENDPOINT_PLACEHOLDER
  Frontend podstawia string "MCP_ENDPOINT_PLACEHOLDER" (client-side) na faktyczny app_url tej instancji,
  np. "https://monolynx.com/mcp". To JEDYNY token do string-replace w tym pliku - nie uzywaj innych wariantow.
-->

# BOOTSTRAP: skonfiguruj projekt pod Monolynx i zacznij wspolprace

> Jestes agentem AI (Claude Code, Codex lub inny asystent MCP). Ten dokument to imperatyw dla Ciebie. Przeprowadz uzytkownika przez trzy fazy: SETUP (skonfiguruj polaczenie), NOTYFIKACJA (powiedz co ustawiles i czego potrzebujesz), WSPOLPRACA (zaproponuj konkretne skille i zacznij prace). Wykonuj fazy po kolei. Jesli czegos brakuje (np. tokenu), nie zgaduj - poproc uzytkownika.

Endpoint MCP tej instancji: `MCP_ENDPOINT_PLACEHOLDER`.

---

## Faza 1: SETUP

Skonfiguruj polaczenie Claude Code z Monolynx:

1. **Dodaj marketplace Monolynx:**
   ```bash
   /plugin marketplace add https://gitlab.com/piotrkrych/monolynx.git
   /plugin install monolynx@monolynx
   ```
2. **Ustaw `userConfig` pluginu:**
   - `mcp_token` - token API uzytkownika (format `osk_...`). To sekret trzymany w keychain.
   - `mcp_endpoint` - endpoint MCP tej instancji: `MCP_ENDPOINT_PLACEHOLDER` (domyslnie `https://monolynx.com/mcp`).
3. **Zapisz slug projektu w `.claude/settings.json`** repozytorium, w polu `env`, zeby skille wiedzialy na ktorym projekcie operuja:
   ```json
   {
     "env": {
       "MONOLYNX_PROJECT_SLUG": "twoj-slug-projektu"
     }
   }
   ```
   Kolejnosc rozwiazywania slugu (od najwyzszego priorytetu): `MONOLYNX_PROJECT_SLUG` z `.env`/`.claude/settings.json` repo -> `project_slug` z konfiguracji pluginu -> domyslny `"monolynx"`.
4. **Zweryfikuj polaczenie:** wywolaj `list_projects` przez MCP. Jesli zwraca liste projektow - polaczenie dziala. Jesli dostajesz blad autoryzacji (401/403) - token jest brakujacy lub niepoprawny, przejdz do Fazy 2 i poproc uzytkownika o token.

## Faza 2: NOTYFIKACJA

Powiadom uzytkownika jasno i konkretnie:

**Co ustawiles:**
- dodany marketplace i zainstalowany plugin `monolynx@monolynx`,
- `mcp_endpoint` = `MCP_ENDPOINT_PLACEHOLDER`,
- `MONOLYNX_PROJECT_SLUG` w `.claude/settings.json` (jesli ustawiony),
- wynik weryfikacji `list_projects` (dziala / blad autoryzacji).

**Czego POTRZEBUJESZ od uzytkownika (wypisz tylko brakujace elementy):**
- **Token API** - jesli `mcp_token` nie jest ustawiony lub weryfikacja zwrocila blad autoryzacji. Poinstruuj: wejdz na `/dashboard/profile/tokens`, wygeneruj token (format `osk_...`, pokazywany tylko raz przy tworzeniu), wklej go do `mcp_token` w konfiguracji pluginu.
- **Slug projektu** - jesli jest niejednoznaczny (wiele projektow w `list_projects`, brak `MONOLYNX_PROJECT_SLUG`). Poproc o wskazanie, na ktorym projekcie pracujecie, i zapisz go w `.claude/settings.json`.

Jesli wszystko jest ustawione i `list_projects` dziala - napisz to wprost i przejdz do Fazy 3.

## Faza 3: WSPOLPRACA

Zaproponuj uzytkownikowi konkretny start. Zasugeruj skille dobrane do typowych pierwszych krokow:

- **`/monolynx:setup`** - checklista konfiguracji projektu (OK/BRAK); za zgodą uruchamia skille naprawcze z listy poniżej, więc to naturalny pierwszy krok.
- **`/monolynx:project-toolchain`** - wykryj komendy lint i testow projektu i zapisz je jako strone wiki `toolchain`; bez tego `work` nie zweryfikuje kodu przed zamknieciem ticketu.
- **`/monolynx:create-graph-ci-script`** - wygeneruj skrypt CI, ktory synchronizuje graf zaleznosci kodu z Monolynx (dobry pierwszy krok w repo bez grafu).
- **`/monolynx:create-mutation-ci-script`** - wygeneruj nieblokujacy krok CI z testami mutacyjnymi w trybie diffowym na MR/PR, z raportem jako artefaktem i zapadka baseline (narzedzia mutacyjnego nie instaluje).
- **`/monolynx:wiki-init`** - wlacz metode LLM Wiki dla projektu (strony systemowe: regulamin, katalog, dziennik); fundament dla wiki jako zrodla prawdy.
- **`/monolynx:ticket-create`** - utworz pierwszy porzadny ticket z kontekstem z wiki, grafu i kodu.
- **`/monolynx:brief`** - dla pracy bez ticketu (konfiguracja, investigation, drobna zmiana): jedno pytanie o kontrakt (gotowe / nie ruszac / stop przed), ENTER i start. Hook `brief_guard` pluginu odpala go sam, gdy pierwszy prompt sesji to goly imperatyw.

Zapytaj uzytkownika, **ktory modul go interesuje** (Scrum, Wiki, monitoring, graf zaleznosci, error tracking, pipelines) i zacznij od odpowiedniego skilla. Nastepnie prowadz wspolprace.

---

## Komplet 23 skilli `/monolynx:*`

- **`/monolynx:next`** - pierwsza komenda do zapamiętania. Router read-only sprawdza git, ticket, sprint, sesje, MR i konfigurację; podaje 1-3 komendy z uzasadnieniem oraz `NEXT: <komenda>`, bez uruchamiania pracy.

- **`/monolynx:setup`** - checklista konfiguracji projektu: slug, strona wiki `toolchain`, LLM Wiki, graf i mutacje w CI, flagi `MONOLYNX_*`. Pokazuje OK/BRAK i za zgodą uruchamia skill naprawczy; sam niczego nie zmienia, flagi autonomii tylko raportuje. Dobry pierwszy krok w nowym projekcie.
- **`/monolynx:brief`** - kontrakt epizodu bez ticketu: gotowe = obserwowalny stan, czego nie ruszac, przed czym sie zatrzymac; ENTER = start pracy.
- **`/monolynx:work`** - podejmij ticket pelnym flow: research + kontrakt autonomii + zespol agentow + tryb defektu (najpierw czerwony test) + rekoncyliacja z kryteriami + obowiazkowy krytyk + lint i testy przed zamknieciem.
- **`/monolynx:work-simple`** - uproszczony flow dla malych ticketow (do 3 SP): 1 dev + krytyk, bez Agent Teams; ten sam kontrakt i tryb defektu.
- **`/monolynx:resume`** - wznow prace po /clear, kompakcji albo nastepnego dnia: stan ticketu z komentarzy, pipeline'u, kryteriow i gita; read-only.
- **`/monolynx:project-toolchain`** - wykryj komendy lint/test projektu i zapisz jako strone wiki `toolchain` (raz per projekt).
- **`/monolynx:mutation-check`** - uruchom testy mutacyjne narzędziem ze strony `toolchain` (domyślnie diff wobec baseline brancha, twardy budżet czasu) i zamień przeżywających mutantów w pracę: kryteria akceptacji przy tickecie albo tickety w backlogu. Score nie jest bramką.
- **`/monolynx:ticket-create`** - utworz ticket z kontekstu wiki, grafu i kodu, w ustalonej formie (cel, kontekst, zakres, kryteria akceptacji, zaleznosci).
- **`/monolynx:ticket-review`** - zrecenzuj ticket pod katem formy i zgodnosci z wiki oraz kodem.
- **`/monolynx:sprint-run`** - dyspozytor sprintu: jeden idempotentny tick startuje brakujące sesje `work` w tle (jeden ticket = jedna nazwana sesja `<slug>-<KEY>-<model>` i własny worktree), zbiera zakończone, raportuje czekające na człowieka. Sam nie pracuje nad ticketem; pętla jest na zewnątrz (`/loop 15m` albo skrypt `sprint_run.sh` z pluginu).
- **`/monolynx:mr-queue`** - kolejka merge MR (glab) i PR (gh): jeden tick bierze pierwszy MR/PR z kolejki i robi jedno działanie w kolejności konflikt -> CI -> approve -> merge, a po merge czeka na zielone CI brancha docelowego. Bez argumentów sam układa kolejkę z otwartych MR/PR.
- **`/monolynx:sprint-end`** - zamknij sprint jako pipeline `sprint_close`: INGEST logow pracy do wiki + LINT + retro korekt + domkniecie sprintu.
- **`/monolynx:retro`** - retro korekt sprintu: uwagi krytyka, NEEDS WORK, STOP-y i eskalacje z ticketow oraz pamieci agentow, pogrupowane per ticket, z propozycja trwalej maszynerii (regula, constitution, hook; wiersz rubryki tylko w repo pluginu, poza nim regula projektu albo ticket do projektu Monolynx).
- **`/monolynx:search`** - semantyczne wyszukiwanie RAG w wiki projektu.
- **`/monolynx:wiki-init`** - wlacz metode LLM Wiki dla projektu (strony systemowe + flaga).
- **`/monolynx:wiki-ingest`** - zintegruj nowe zrodlo (plik, URL, wklejona tresc) z wiki, linkujac wikilinkami.
- **`/monolynx:wiki-lint`** - audyt zdrowia wiki: sieroty, martwe linki, sprzecznosci, luki.
- **`/monolynx:wiki-sync-merge`** - post-merge INGEST do wiki, uruchamiany po merge ticketow/PR do main.
- **`/monolynx:help`** - mapa skilli w 6 grupach (Setup, Planowanie, Praca, Integracja, Sprint, Wiki) z dwiema ścieżkami pracy: ręczną i autopilotem.
- **`/monolynx:create-graph-ci-script`** - wygeneruj skrypt CI synchronizujacy graf zaleznosci kodu z Monolynx.
- **`/monolynx:create-mutation-ci-script`** - wygeneruj nieblokujacy krok CI z testami mutacyjnymi: tryb diffowy na MR/PR, raport jako artefakt, zapadka baseline (score / baseline / delta).
- **`/monolynx:graph-sync`** - zsynchronizuj graf zaleznosci lokalnie, bez CI (pierwsze zasilenie albo odswiezenie ad hoc).

Pelna dokumentacja modulow i narzedzi: <https://monolynx.com/llms.txt>.
