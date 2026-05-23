**Claude Code**

**\+**

**Obsidian**

*Raport integracji — jak budować AI "drugi mózg" w wytwarzaniu oprogramowania*

Piotr Krych · Monolynx · Maj 2026

# 

# **Spis treści**

[**Spis treści	1**](#heading=)

[**1\. Wprowadzenie i kontekst	3**](#heading=)

[**2\. Jak działa integracja Claude Code \+ Obsidian	3**](#heading=)

[**2.1. Architektura systemu	3**](#heading=)

[**2.2. Czym jest Claude Code	3**](#heading=)

[**2.3. Czym jest Obsidian w tym kontekście	4**](#heading=)

[**3\. Metody połączenia — 5 strategii	4**](#heading=)

[**3.1. Strategia A — obsidian-mcp (polecana dla początkujących)	4**](#heading=)

[**3.2. Strategia B — Local REST API (zaawansowana)	5**](#heading=)

[**3.3. Strategia C — Vault jako katalog roboczy	5**](#heading=)

[**3.4. Strategia D — Symlinki do dedykowanego vault	5**](#heading=)

[**3.5. Strategia E — Plugin Claudian	5**](#heading=)

[**4\. Kluczowe narzędzia i pluginy	6**](#heading=)

[**5\. CLAUDE.md — serce konfiguracji	6**](#heading=)

[**5.1. Co umieszczać w CLAUDE.md	6**](#heading=)

[**5.2. Zasady pisania skutecznego CLAUDE.md	6**](#heading=)

[**5.3. Zarządzanie kontekstem	7**](#heading=)

[**6\. Najlepsze praktyki w wytwarzaniu oprogramowania	7**](#heading=)

[**6.1. Zarządzanie wiedzą projektową	7**](#heading=)

[**6.2. Workflow sesji kodowania	7**](#heading=)

[**6.3. Zarządzanie wieloma repozytoriami	8**](#heading=)

[**6.4. Automatyzacja przez Hooks	8**](#heading=)

[**6.5. Code Review wspomagany notatkami	8**](#heading=)

[**7\. Przykładowe workflow dla developera	8**](#heading=)

[**7.1. Scenariusz: Nowa funkcjonalność w projekcie	8**](#heading=)

[**7.2. Scenariusz: Onboarding nowego developera	9**](#heading=)

[**8\. Korzyści biznesowe i produktywność	9**](#heading=)

[**9\. Znane ograniczenia i pułapki	10**](#heading=)

[**10\. Metoda Karpathy'ego — LLM Wiki	10**](#heading=)

[**10.1. Czym jest LLM Wiki — podstawowa idea	10**](#heading=)

[**10.2. Architektura trójwarstwowa	11**](#heading=)

[**10.3. Trzy operacje rdzeniowe	12**](#heading=)

[**INGEST — wstrzykiwanie nowych źródeł	12**](#heading=)

[**QUERY — zapytania do wiki	12**](#heading=)

[**LINT — health-check wiki	12**](#heading=)

[**10.4. Dwa kluczowe pliki nawigacyjne	12**](#heading=)

[**10.5. Zasady i porady Karpathy'ego	12**](#heading=)

[**Granularność stron	13**](#heading=)

[**Obsługa sprzeczności	13**](#heading=)

[**Co-evolving schema	13**](#heading=)

[**10.6. Zastosowania w wytwarzaniu oprogramowania	13**](#heading=)

[**10.7. Bootstrapujący prompt Karpathy'ego	13**](#heading=)

[**10.8. Kontekst historyczny — Memex Bush 1945	14**](#heading=)

[**11\. Polecane źródła i tutoriale	14**](#heading=)

[**Podsumowanie	15**](#heading=)

# 

# **1\. Wprowadzenie i kontekst**

Jednym z największych problemów pracy z narzędziami AI jest utrata kontekstu. Godzina produktywnej sesji architektonicznej z modelem językowym kończy się wymazaniem całego kontekstu wraz z zamknięciem okna. Następnego dnia trzeba zaczynać od zera — powtarzać decyzje, wyjaśniać strukturę projektu, przypominać stosowany stack technologiczny.

Claude Code (terminalowy agent AI firmy Anthropic) oraz Obsidian (lokalny, oparty na Markdown notatnik dla programistów) razem tworzą potężny system, który rozwiązuje ten problem. Obsidian przechowuje wiedzę trwale; Claude Code czyta tę wiedzę i działa na jej podstawie.

| Dlaczego ta integracja jest istotna? Claude Code generuje markdown stale — plany, instrukcje CLAUDE.md, notatki z sesji. Obsidian to najlepszy edytor markdown na rynku — local-first, z graph view i pluginami. MCP (Model Context Protocol) pozwala Claude'owi czytać i zapisywać vault bez copy-paste. Każda sesja kodowania może automatycznie zostawiać ślad w bazie wiedzy projektu. |
| :---- |

# **2\. Jak działa integracja Claude Code \+ Obsidian**

## **2.1. Architektura systemu**

Integracja opiera się na protokole MCP (Model Context Protocol) — otwartym standardzie opracowanym przez Anthropic, który umożliwia modelom AI bezpieczne połączenie z zewnętrznymi źródłami danych. W praktyce wygląda to tak:

| Schemat przepływu danych Claude Code (terminal)  ──\[MCP\]──  Obsidian Vault (folder .md) Sesja kodowania         ──zapis──  Claude Chats/projekt-alpha/ CLAUDE.md (instrukcje)  ──odczyt── Claude wczytuje przy starcie sesji Notatki z decyzji       ──szukaj── Kontekst dostępny w kolejnych sesjach |
| :---- |

## **2.2. Czym jest Claude Code**

Claude Code to terminalowy agent AI — uruchamiany komendą claude w katalogu projektu. Potrafi:

* Czytać dziesiątki plików przed odpowiedzią

* Pisać strukturalne outputy do konkretnych ścieżek

* Wykonywać skrypty shell i interpretować wyniki

* Utrzymywać roboczy dokument kontekstu, który aktualizuje w trakcie pracy

* Działać przez długie, wieloetapowe zadania bez utraty celu

## **2.3. Czym jest Obsidian w tym kontekście**

Vault Obsidiana to po prostu folder z plikami .md. Claude Code może ten folder czytać, rozumieć strukturę i do niego zapisywać. Dzięki temu vault staje się trwałą pamięcią agenta — przechowuje:

* Decyzje architektoniczne (Architecture Decision Records)

* Podsumowania sesji kodowania z datami i kontekstem

* Instrukcje dla Claude'a specificzne dla projektu

* Bazy wiedzy (runbooki, konwencje kodu, wzorce testów)

* Dzienniki bugów i rozwiązań

# **3\. Metody połączenia — 5 strategii**

Społeczność wypracowała kilka sprawdzonych sposobów łączenia tych narzędzi. Poniżej zestawienie według stopnia złożoności i przypadku użycia:

| Strategia | Poziom trudności | Najlepszy dla |
| :---- | :---- | :---- |
| A: Bezpośredni dostęp do plików (obsidian-mcp) | Niski — brak pluginów | Szybki start, prosta lektura/zapis vault |
| B: REST API (Local REST API plugin) | Średni — wymaga Python/uv | Zaawansowane wyszukiwanie, frontmatter, periodic notes |
| C: Vault \= katalog roboczy | Niski — zero konfiguracji | PKM / "drugi mózg" — vault jest projektem |
| D: Symlinki do dedykowanego vault | Niski — jedno polecenie | Praca w wielu repozytoriach naraz |
| E: Plugin Claudian / obsidian-claude-code | Niski — instalacja pluginu | Wywoływanie Claude'a bezpośrednio z notatek |

## **3.1. Strategia A — obsidian-mcp (polecana dla początkujących)**

Pakiet npm obsidian-mcp czyta vault bezpośrednio z dysku. Obsidian nie musi być uruchomiony. Wystarczy konfiguracja w pliku .claude.json:

**{ "mcpServers": { "obsidian": {**

    **"command": "npx",**

    **"args": \["-y", "obsidian-mcp", "/ścieżka/do/vaulta"\]**

  **} } }**

Po restarcie Claude Code komenda /mcp potwierdza połączenie. Można zapytać: "Czy widzisz mój vault? Wylistuj pliki."

## **3.2. Strategia B — Local REST API (zaawansowana)**

Plugin Local REST API w Obsidianie udostępnia API na porcie 27124\. Pakiet mcp-obsidian (Python/uvx) łączy się przez to API. Daje bogatszy dostęp: wyszukiwanie pełnotekstowe, patch zawartości, periodic notes. Wymaga: Python, narzędzia uv oraz działającego Obsidiana.

## **3.3. Strategia C — Vault jako katalog roboczy**

Najpopularniejsza metoda w społeczności. Vault Obsidiana jest katalogiem, w którym uruchamiamy Claude Code. Plik CLAUDE.md pełni podwójną rolę: instrukcje dla agenta i czytelna notatka w Obsidianie.

| Struktura vault-jako-projekt my-vault/   CLAUDE.md              ← Claude wczytuje to \+ Obsidian wyświetla   .claude/               ← Skills, hooks, ustawienia   daily-notes/   projects/              ← podfoldery na projekty   research/   decisions/             ← Architecture Decision Records   templates/ |
| :---- |

## **3.4. Strategia D — Symlinki do dedykowanego vault**

Dla programistów pracujących w wielu repozytoriach jednocześnie. Tworzymy dedykowany vault i symlinkujemy interesujące nas katalogi:

**mkdir \~/Developer-Vault && cd \~/Developer-Vault**

**ln \-s \~/.claude claude-global**

**ln \-s \~/projects/moj-projekt moj-projekt**

**ln \-s \~/projects/lepszesmsy lepszesmsy**

Daje to ujednolicone wyszukiwanie po wszystkich CLAUDE.md, planach i notatkach ze wszystkich projektów. Bez zaśmiecania repozytoriów.

## **3.5. Strategia E — Plugin Claudian**

Plugin Claudian instalowany przez BRAT (Beta Reviewers Auto-update Tool) pozwala wywoływać Claude Code bezpośrednio z okna Obsidiana — bez przełączania się do terminala. Obsługuje strumieniowanie odpowiedzi, edycję zaznaczonego tekstu, generowanie i modyfikację kodu z podglądem zmian przed ich zastosowaniem.

# **4\. Kluczowe narzędzia i pluginy**

| Narzędzie / Plugin | Opis i zastosowanie |
| :---- | :---- |
| obsidian-mcp (npm) | MCP server — bezpośredni dostęp Claude Code do vault bez pluginów Obsidiana |
| mcp-obsidian (Python) | Pełny REST API bridge — wyszukiwanie, frontmatter, periodic notes |
| Claudian (plugin) | Wywołanie Claude Code z UI Obsidiana, strumieniowanie, edycja notatek |
| obsidian-claude-code-plugin | Wielobackend (Claude/OpenCode), zakładkowy UI, śledzenie zadań, podgląd zmian |
| Local REST API (plugin) | Udostępnia API vaulta na porcie 27124, wymagany przez Strategię B |
| Smart Connections (plugin) | Semantyczne łączenie notatek — przydatne w wyszukiwaniu kontekstu |
| Claudesidian MCP (plugin) | Semantic search przez Ollama embeddings, tryb agentowy |
| Dataview (plugin) | Zapytania do notatek jak do bazy danych — dashboardy projektów |

# **5\. CLAUDE.md — serce konfiguracji**

CLAUDE.md to plik, który Claude Code wczytuje na początku każdej sesji. Jest tak ważny, że środowisko programistyczne 2026 traktuje go jak .gitignore — niezbędna infrastruktura, nie opcjonalna dokumentacja.

## **5.1. Co umieszczać w CLAUDE.md**

* Komendy bash używane w projekcie (npm run test, npm run build)

* Styl kodu: "Używamy ES modules, nie CommonJS"

* Kluczowe pliki i wzorce architektoniczne: "State management przez Zustand"

* Instrukcje testowania: "Nowe komponenty wymagają pliku testowego z React Testing Library"

* Zasady bezpieczeństwa: "NIGDY nie commituj sekretów, NIGDY nie pusuj do main bez review"

* Linki do @importów innych plików dokumentacji

## **5.2. Zasady pisania skutecznego CLAUDE.md**

| Reguły CLAUDE.md od ekspertów Optymalna długość: 50-100 linii w pliku głównym, szczegóły w @importach. Test przydatności: "Czy usunięcie tej linii spowoduje błędy Claude'a?" Jeśli nie — usuń. Commituj do git — wartość rośnie z czasem, cały team korzysta. Używaj CAPS dla krytycznych reguł: IMPORTANT: Never push to main. Ścieżkowe scoping: reguły w .claude/rules/\*.md ładowane tylko w pasujących katalogach. Każdy plik .md w .claude/commands/ staje się komendą slash (np. /project:review). |
| :---- |

## **5.3. Zarządzanie kontekstem**

Świeża sesja Claude Code konsumuje \~20 000 tokenów (system prompt, definicje narzędzi, CLAUDE.md) zanim użytkownik wpisze cokolwiek. Jakość odpowiedzi spada przy 20-40% wypełnienia okna kontekstu (200 000 tokenów). Praktycy rekomendują:

| Wypełnienie kontekstu | Stan | Zalecane działanie |
| :---- | :---- | :---- |
| 0–50% | Praca swobodna | Normalna praca |
| 50–70% | Uwaga | Planuj reset sesji |
| 70–90% | Krytyczne | /compact — kompresja kontekstu |
| 90%+ | Niebezpieczne | /clear obowiązkowe, zapis stanu do pliku .md |

# **6\. Najlepsze praktyki w wytwarzaniu oprogramowania**

## **6.1. Zarządzanie wiedzą projektową**

* Każdy projekt ma własny folder w vaulcie: projects/nazwa-projektu/

* Architecture Decision Records (ADR) — dokumentuj każdą kluczową decyzję techniczną

* Session logs — automatyczne podsumowanie po każdej sesji komendą /compress

* Runbooki — jak deployować, jak debugować specyficzne problemy projektu

* Bug journal — co zostało naprawione i dlaczego — nieocenione przy regresjach

## **6.2. Workflow sesji kodowania**

Najskuteczniejszy wzorzec według społeczności:

| Wzorzec sesji AI+Obsidian 1\. START: Uruchom claude w katalogu projektu lub vault 2\. PLAN: Poproś Claude'a o plan w trybie planowania (Shift+Tab), bez wykonywania 3\. REVIEW: Sprawdź plan, zatwierdź lub popraw 4\. EXECUTE: Claude wykonuje zatwierdzone kroki 5\. TEST: claude uruchamia testy, linter, build — poprawia błędy 6\. COMPRESS: /compress lub /project:log-session zapisuje podsumowanie do Obsidiana 7\. GIT: Commit z sensownym komunikatem — Claude sugeruje na podstawie zmian |
| :---- |

## **6.3. Zarządzanie wieloma repozytoriami**

* Dedykowany Developer Vault z symlinkami do każdego projektu

* Globalny \~/.claude/CLAUDE.md z ustawieniami wspólnymi dla wszystkich projektów

* Per-projekt CLAUDE.md commitowany do repozytorium — cały team korzysta

* Komendy slash w .claude/commands/ — wspólne dla teamu, np. /project:review

* Personal commands w \~/.claude/commands/ — dostępne we wszystkich projektach

## **6.4. Automatyzacja przez Hooks**

Hooks pozwalają uruchamiać skrypty w kluczowych momentach cyklu Claude Code. Reguły w CLAUDE.md są przestrzegane w \~70% przypadków — Hooks domykają tę lukę do 100%. Przykłady zastosowań:

* Pre-commit hook — weryfikacja, czy nie ma sekretów w kodzie

* Post-session hook — automatyczny zapis podsumowania do Obsidiana

* Pre-push hook — uruchamia testy, blokuje push przy niepowodzeniu

* File-change hook — aktualizuje VAULT-INDEX.md po każdej zmianie

## **6.5. Code Review wspomagany notatkami**

Komenda /project:review podciąga aktualny git diff, wczytuje CLAUDE.md projektu i notatki architektoniczne z Obsidiana, a następnie generuje review z kontekstem historycznym. Eliminuje powtarzające się komentarze o stylu — reguły są zapisane raz w CLAUDE.md.

# **7\. Przykładowe workflow dla developera**

## **7.1. Scenariusz: Nowa funkcjonalność w projekcie**

Wyobraźmy sobie dodanie nowego endpointu API do projektu Django (np. w kontekście LepszeSMSy lub Monolynx):

| Krok po kroku — workflow z Claude Code \+ Obsidian 1\. claude uruchomiony w katalogu projektu    → Claude czyta CLAUDE.md: stack, konwencje, testy 2\. Programista: "Dodaj endpoint /api/sms/stats zwracający statystyki SMS"    → Claude w trybie planowania: tworzy plan z listą plików do modyfikacji 3\. Programista przegląda plan w Obsidianie (przez symlink lub MCP)    → Zatwierdza lub koryguje podejście 4\. Claude implementuje: views.py, serializers.py, urls.py, testy    → Uruchamia pytest, poprawia błędy 5\. /project:log-session zapisuje do Obsidiana:    projects/monolynx/sessions/2026-05-20-sms-stats-endpoint.md 6\. Plik zawiera: decyzje, problemy, rozwiązania, linki do commitów |
| :---- |

## **7.2. Scenariusz: Onboarding nowego developera**

Vault z historią projektu staje się żywą dokumentacją:

* Nowy developer otwiera vault w Obsidianie

* Czyta VAULT-INDEX.md — przegląd całej bazy wiedzy

* Przeglądanagraph view — wizualne połączenia między konceptami

* Claude Code wczytuje CLAUDE.md — natychmiast zna konwencje projektu

* Pierwsza sesja z Claude'em: "Przejrzyj ostatnie 10 session logs i powiedz mi, nad czym pracował team"

# **8\. Korzyści biznesowe i produktywność**

| Obszar | Konkretna korzyść |
| :---- | :---- |
| Utrata kontekstu | Eliminacja "wczoraj rozmawialiśmy o X, przypomnij mi" — kontekst jest w plikach |
| Onboarding | Nowi developerzy mają żywą dokumentację zamiast przestarzałych wiki |
| Code review | Review z historycznym kontekstem architektonicznym — mniej WTF/min |
| Decyzje techniczne | ADR w Obsidianie — "dlaczego wybraliśmy X zamiast Y" jest zawsze dostępne |
| Regresje | Bug journal w vault — "to już naprawialiśmy, tu jest rozwiązanie" |
| Współpraca | CLAUDE.md commitowany — cały team korzysta z tej samej konfiguracji AI |
| Dokumentacja | Session logs \= dokumentacja pisana przy okazji, nie osobno |
| Context window | Optymalny management kontekstu — praca w 50-70% okna, reset przez /clear |

# **9\. Znane ograniczenia i pułapki**

* Obsidian Mobile: symlinki nie działają — należy wykluczyć z synchronizacji mobilnej

* Git plugin Obsidiana: śledzi tylko jedno repo (vault), nie symlikowane projekty

* Przenoszenie plików: przez granice symlinków w eksploratorze Obsidiana nie działa

* Strategia B wymaga działającego Obsidiana: kolejny element w stacku

* Auto-kompakcja przy 83.5%: traci \~70-80% detali — zawsze zapisuj stan ręcznie przed

* CLAUDE.md: reguły przestrzegane w \~70% — dla krytycznych reguł użyj Hooks

* "80% problem": 66% developerów zgłasza rozwiązania AI "prawie dobre, ale nie do końca"

* Nadmiar konfiguracji: zacznij od prostego CLAUDE.md \+ kilku komend; dodawaj stopniowo

# **10\. Metoda Karpathy'ego — LLM Wiki**

**★ PRIORYTETOWY ROZDZIAŁ** 

| Kim jest Andrej Karpathy? Co-founder OpenAI, były Director of AI w Tesla (Autopilot), wykładowca Stanford CS231n. Jeden z najbardziej szanowanych i wpływowych badaczy AI na świecie. 4 kwietnia 2026 opublikował gist llm-wiki.md — viral: 5000+ gwiazdek w kilka dni. Jego metoda zrewolucjonizowała podejście do łączenia Claude Code z Obsidianem. Karpathy quote: Obsidian is the IDE; the LLM is the programmer; the wiki is the codebase. Link: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f |
| :---- |

## **10.1. Czym jest LLM Wiki — podstawowa idea**

Karpathy odwrócił konwencjonalne podejście RAG. Zamiast retrieval at query time — LLM inkrementalnie buduje i utrzymuje trwałe wiki z plików markdown. Każde nowe źródło nie jest indeksowane do późniejszego wyszukiwania — LLM je czyta, ekstrahuje wiedzę i integruje z istniejącym wiki: aktualizuje strony encji, rewiduje podsumowania tematyczne, oznacza sprzeczności.

| RAG vs. LLM Wiki — kluczowa różnica RAG (NotebookLM, ChatGPT files): LLM odkrywa wiedzę od nowa przy każdym pytaniu.                                   Brak akumulacji. Synteza 5 dokumentów \= za każdym razem. LLM Wiki (Karpathy):               Wiki \= trwały, narastający artefakt.                                   Cross-references już tam są. Sprzeczności oznaczone.                                   Synteza odzwierciedla wszystko, co przeczytano.                                   Wiki bogaci się z każdym źródłem i pytaniem. |
| :---- |

## **10.2. Architektura trójwarstwowa**

System składa się z trzech warstw, każda z jasno zdefiniowaną odpowiedzialnością:

| Warstwa | Zawartość | Kto zarządza |
| :---- | :---- | :---- |
| raw/ (surowce) | Artykuły, PDF-y, notatki, transkrypty — niezmienione, źródło prawdy | Ty — kuracja, immutable |
| wiki/ (skompilowana wiedza) | Strony encji, konceptów, podsumowań, porównań, synthesis | LLM — pisze i aktualizuje |
| CLAUDE.md (schemat) | Jak wiki jest zorganizowane, konwencje, workflow ingestowania i query | Ty \+ LLM — co-evolve |

Analogia kompilatorowa Karpathy'ego: raw/ to kod źródłowy, LLM to kompilator, wiki/ to binarny output, lint to testy, a queries to runtime.

**your-project/**

**├── raw/                    ← Immutable source material (Ty kurujesz)**

**│   ├── articles/**

**│   ├── papers/**

**│   └── notes/**

**├── wiki/                   ← LLM-generated pages (LLM pisze)**

**│   ├── entities/           ← Osoby, organizacje, produkty**

**│   ├── concepts/           ← Tematy, idee, frameworki**

**│   ├── sources/            ← Per-source summary pages**

**│   ├── index.md            ← Katalog wszystkich stron**

**│   └── log.md              ← Append-only log operacji**

**└── CLAUDE.md               ← Schemat: struktura, konwencje, workflow**

## **10.3. Trzy operacje rdzeniowe**

### **INGEST — wstrzykiwanie nowych źródeł**

Wrzucasz nowe źródło do raw/ i mówisz LLM, żeby je przetworzyło. Flow: LLM czyta źródło, dyskutuje key takeaways z Tobą, pisze stronę summary w wiki/, aktualizuje index, aktualizuje strony encji i konceptów, dopisuje do log.md. Jedno źródło może dotknąć 10-15 stron wiki.

**\# Komenda ingest — przykład**

**Dodałem nowy artykuł do raw/articles/. Proszę go przetworz.**

### **QUERY — zapytania do wiki**

Zadajesz pytania do wiki. LLM przeszukuje relevantne strony, czyta je i syntezuje odpowiedź z cytatami. Kluczowa zasada Karpathy'ego: dobre odpowiedzi mogą być zapisywane z powrotem do wiki jako nowe strony. Analizy, porównania, odkryte połączenia — nie powinny ginąć w historii chatu.

### **LINT — health-check wiki**

Periodycznie prosisz LLM o sprawdzenie wiki. Szuka: sprzeczności między stronami, przestarzałych twierdzeń, orphan pages (bez inbound linków), ważnych konceptów bez własnej strony, brakujących cross-references, luk w danych. LLM sugeruje też nowe pytania do zbadania.

**\# Komenda lint — przykład**

**Sprawdź zdrowie mojego wiki. Znajdź sprzeczności, orphan pages i luki.**

## **10.4. Dwa kluczowe pliki nawigacyjne**

Dwa specjalne pliki pomagają LLM (i Tobie) nawigować w wiki w miarę jego wzrostu:

| index.md vs log.md index.md — content-oriented. Katalog wszystkich stron z linkiem, jednozdaniowym summary            i metadata. LLM aktualizuje przy każdym ingeście. Czytany PIERWSZY przy query.            Działa zaskakująco dobrze przy \~100 źródłach bez potrzeby embedding/RAG. log.md   — chronological. Append-only record: co ingested, kiedy, co się zmieniło i dlaczego.            Historia ewolucji rozumienia tematu. Audyt wiki. Orientacja nowych sesji. |
| :---- |

## **10.5. Zasady i porady Karpathy'ego**

### **Granularność stron**

Jedna strona na koncept lub encję. Rozmiar: taki, żebyś mógł przeczytać całą stronę w jednym podejściu. Nie jeden akapit (za cienkie), nie mega-dokument (za trudny do aktualizacji). Wikipedia jako przybliżona miara skali.

### **Obsługa sprzeczności**

LLM nigdy nie nadpisuje cicho starych twierdzeń. Gdy nowe źródło jest sprzeczne z istniejącą treścią wiki, LLM oznacza sprzeczność explicite:

**\> \*\*Sprzeczność \[data\]:\*\* Źródło A mówi X; Źródło B mówi Y. Nierozwiązane.**

Flaga zostaje aż do Twojej decyzji. Można poprosić LLM o ocenę źródeł i rozwiązanie — ale transparentność jest kluczowa.

### **Co-evolving schema**

CLAUDE.md to najważniejszy plik. Pisz go kolaboratywnie — zacznij sesję, poproś LLM o pomoc w projektowaniu struktury i konwencji dla Twojej domeny. Wraz z używaniem wiki — aktualizuj schemat. Może potrzebujesz nowego typu strony, nowych pól frontmatter, nowego kroku w workflow ingestu. Karpathy: You and the LLM co-evolve this over time.

## **10.6. Zastosowania w wytwarzaniu oprogramowania**

| Zastosowanie | Konkretny use case |
| :---- | :---- |
| Wiedza projektowa | Wiki projektu: architektura, decyzje, gotowe rozwiazania bugow \- kompilowane automatycznie |
| Onboarding | Nowy developer pyta wiki o ostatnie decyzje architektoniczne teamu |
| Competitive analysis | Śledzenie konkurencji: każdy artykuł o konkurencie ingestowany, wiki syntezuje trendy |
| Due diligence | Analiza technologiczna: każdy raport, PDF, artykuł skompilowany w spójne wiki |
| Team knowledge base | Slack threads, meeting transcripts, project docs — LLM utrzymuje wiki za team |
| Personal learning | Kursy, tutoriale, konferencje — wiki rośnie razem z wiedzą developera |
| Bug & solution journal | Każdy naprawiony bug trafia do wiki — powiązania między bugami widoczne w graph view |

## **10.7. Bootstrapujący prompt Karpathy'ego**

Karpathy dołączył do gista gotowy prompt startowy. Wklej do Claude Code po przeczytaniu llm-wiki.md:

| Quick-start Prompt — Karpathy (do wklejenia w Claude Code) You are my LLM Wiki agent. I want to build a personal knowledge base using the pattern described in this file. Help me: 1\. Create the folder structure: raw/, wiki/, and the schema file (CLAUDE.md) 2\. Design the page types and naming conventions for my domain 3\. Set up index.md and log.md 4\. Walk me through ingesting my first source Ask me one question to get started: what is this wiki for? |
| :---- |

## **10.8. Kontekst historyczny — Memex Bush 1945**

Karpathy zamyka gista nawiązaniem historycznym. Pomysł jest pokrewny w duchu Memeksowi Vannevara Busha (1945) — osobistemu, kurowanemu magazynowi wiedzy ze skojarzonymi szlakami między dokumentami. Wizja Busha była bliższa temu niż temu, czym stał się web: prywatna, aktywnie kurowana, z połączeniami tak cennymi jak same dokumenty.

| Cytat Karpathy'ego — zamknięcie gista The part Bush couldn't solve was who does the maintenance. The LLM handles that. |
| :---- |

# **11\. Polecane źródła i tutoriale**

Poniżej najlepsze zasoby anglojęzyczne, z których korzystano przy tworzeniu tego raportu:

| Źródło | Temat i wartość |
| :---- | :---- |
| Edward Anil Joseph / Medium (marzec 2026\) | Connect Claude Code with Obsidian — Part 1&2. Kompletny przewodnik krok po kroku z konfiguracją MCP, dwoma podejściami (npm vs REST API), gotowe konfiguracje JSON. |
| StarMorph Blog (marzec 2026\) | Obsidian \+ Claude Code: The Complete Integration Guide. 5 strategii, symlinki, filtry, pluginy, Dataview queries, community insights. |
| MindStudio Blog (kwiecień 2026\) | How to Build an AI Second Brain with Claude Code and Obsidian. Koncepcja "drugi mózg", agent layer, synthesis i automation. |
| Morph / morphllm.com (luty 2026\) | Claude Code Best Practices 2026\. CLAUDE.md, context window management, subagents, hooks, parallel sessions. |
| smart-webtech.com (kwiecień 2026\) | Claude Code Workflows and Best Practices 2026\. Context window thresholds, hooks, slash commands, CLAUDE.md scoping. |
| Anthropic Docs / code.claude.com | Oficjalna dokumentacja Claude Code — best practices, /init, CLAUDE.md format, MCP servers. |
| GitHub: deivid11/obsidian-claude-code-plugin | Plugin z multi-backendem (Claude/OpenCode), streaming, task tracking, file modifications. |
| GitHub: AgriciDaniel/claude-obsidian | Starter vault z CLAUDE.md, MCP, Bases dashboard, semantic wiki pattern (Karpathy). |
| GitHub: shanraisshan/claude-code-best-practice | Kuratorowana biblioteka zasobów: SKILL.md, templates, community links, video tutorials. |
| Karpathy gist / llm-wiki.md (kwiecień 2026\) ★ GŁÓWNE ŹRÓDŁO | Oryginalny prompt i idea file Andreja Karpathy'ego. 5000+ gwiazdek. Definiuje architekturę 3-warstwową, 3 operacje (ingest/query/lint) i co-evolving schema. Link: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f |
| GitHub: Astro-Han/karpathy-llm-wiki | Agent Skills-compatible implementacja LLM Wiki z SKILL.md dla Claude Code. |
| GitHub: AgriciDaniel/claude-obsidian (oparty na Karpathym) | Plugin z /wiki, /save, /autoresearch, hooks SessionStart/Stop — semantic wiki pattern. |

# **Podsumowanie**

Integracja Claude Code z Obsidianem rozwiązuje fundamentalny problem pracy z AI: utratę kontekstu między sesjami. Obsidian staje się trwałą pamięcią agenta — przechowuje decyzje architektoniczne, session logs, runbooki i konwencje kodu. Claude Code czyta tę wiedzę i buduje na niej kolejne sesje.

Dla zespołu programistycznego kluczowe elementy to: dobrze skonfigurowany CLAUDE.md commitowany do repo, session logs zapisywane automatycznie po każdej sesji, Architecture Decision Records w vault oraz Hooks domykające reguły do 100% compliance.

Rekomendowany punkt startowy: Strategia A (obsidian-mcp) lub C (vault \= katalog roboczy), prosty CLAUDE.md (50-100 linii), kilka komend slash. Po 2 tygodniach — dodaj agents i skills tam, gdzie potrzeba jest udowodniona.

| Kluczowe metryki (społeczność 2026\) 66% developerów zgłasza "80% problem" — AI solutions almost right, but not quite 45% underuses custom commands — szacowana strata 8h/miesiąc produktywności CLAUDE.md compliance: \~70% dla reguł w pliku, \~100% z Hooks Próg /compact: 70% okna kontekstu — zalecany reset przez doświadczonych użytkowników |
| :---- |

