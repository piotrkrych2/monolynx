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

- **`/monolynx:create-graph-ci-script`** - wygeneruj skrypt CI, ktory synchronizuje graf zaleznosci kodu z Monolynx (dobry pierwszy krok w repo bez grafu).
- **`/monolynx:wiki-init`** - wlacz metode LLM Wiki dla projektu (strony systemowe: regulamin, katalog, dziennik); fundament dla wiki jako zrodla prawdy.
- **`/monolynx:ticket-create`** - utworz pierwszy porzadny ticket z kontekstem z wiki, grafu i kodu.

Zapytaj uzytkownika, **ktory modul go interesuje** (Scrum, Wiki, monitoring, graf zaleznosci, error tracking, pipelines) i zacznij od odpowiedniego skilla. Nastepnie prowadz wspolprace.

---

## Komplet 12 skilli `/monolynx:*`

- **`work`** - podejmij ticket pelnym flow: research + zespol agentow + obowiazkowy krytyk.
- **`work-simple`** - uproszczony flow dla mniejszych ticketow (< 8 SP): 1 dev + krytyk, bez Agent Teams.
- **`ticket-create`** - utworz ticket z kontekstu wiki, grafu i kodu, w ustalonej formie (cel, kontekst, zakres, kryteria akceptacji, zaleznosci).
- **`ticket-review`** - zrecenzuj ticket pod katem formy i zgodnosci z wiki oraz kodem.
- **`sprint-end`** - zamknij sprint jako pipeline `sprint_close`: INGEST logow pracy do wiki + LINT + domkniecie sprintu.
- **`search`** - semantyczne wyszukiwanie RAG w wiki projektu.
- **`wiki-init`** - wlacz metode LLM Wiki dla projektu (strony systemowe + flaga).
- **`wiki-ingest`** - zintegruj nowe zrodlo (plik, URL, wklejona tresc) z wiki, linkujac wikilinkami.
- **`wiki-lint`** - audyt zdrowia wiki: sieroty, martwe linki, sprzecznosci, luki.
- **`wiki-sync-merge`** - post-merge INGEST do wiki, uruchamiany po merge ticketow/PR do main.
- **`help`** - przewodnik po skillach Monolynx i flow pracy z ticketami.
- **`create-graph-ci-script`** - wygeneruj skrypt CI synchronizujacy graf zaleznosci kodu z Monolynx.

Pelna dokumentacja modulow i narzedzi: <https://monolynx.com/llms.txt>.
