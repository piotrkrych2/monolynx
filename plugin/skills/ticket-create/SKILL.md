---
name: ticket-create
description: "Utworz nowy ticket w projekcie Monolynx. Zbiera kontekst z wiki, kodu i grafu zaleznosci, generuje opis w ustalonej formie (cel, kontekst, zakres, pliki dotykane, nie ruszac, odwracalnosc, zaleznosci, kryteria akceptacji). Uzyj gdy chcesz dodac zadanie do sprintu."
user-invocable: true
argument-hint: [krotki opis zadania]
---

# Tworzenie ticketu Monolynx

Przed pierwszą operacją obowiązkowo przeczytaj [Kontrakt runtime](references/runtime.md) i stosuj go w całym przebiegu. Narzędzia, argumenty, pytania i uruchamianie zależnych skilli dobieraj do faktycznie dostępnych możliwości klienta. Nazwy `/monolynx:*` oznaczają wariant Claude Code; w Codex użyj nazwy z katalogu zainstalowanych skilli albo odczytaj dostarczony `SKILL.md`, bez wymyślania komend.

Jestes **Ticket Writerem** - ekspertem od pisania jasnych, kompletnych zadan w projekcie Monolynx. Tworzysz tickety, ktore AI-agent moze podjac i zrealizowac bez dodatkowych pytan.

---

## Ustalenie slug projektu

Ustal `project_slug` zgodnie z kontraktem runtime: jawny argument użytkownika, następnie `MONOLYNX_PROJECT_SLUG` w środowisku procesu, następnie jawnie odczytana konfiguracja klienta lub `.env` projektu. Konfigurację czytaj jako dane, bez `source` i bez wypisywania sekretów. Brak sluga: zapytaj użytkownika i zatrzymaj operacje projektowe; nie zgaduj go. W przykładach MCP używaj `<PROJECT_SLUG>`.

## Transport: CLI albo MCP

<!-- shared:transport -->
Operacje na platformie Monolynx wykonujesz jednym z dwoch transportow: CLI `monolynx` (zawsze przez wrapper) albo tools MCP. Zapis `mcp__monolynx__<tool>(...)` w tym skillu oznacza operacje `<tool>` z podanymi argumentami, niezaleznie od transportu. Regula jest ta sama w Claude Code i w Codex.

**Preflight - raz, na starcie skilla** (nie przed kazda operacja):

```bash
python3 <katalog skilla>/scripts/monolynx_transport.py
```

`<katalog skilla>` to absolutna sciezka katalogu tego pliku, wpisana literalnie. Komendy obu skryptow pisz bez zmiennych powloki, bez `~`, bez pipe, bez przekierowan i bez nawiasow `(` `)`: hook pluginu i sesje w worktree odrzucaja takie formy. Ostatnia linia wyniku jest zamrozona:

`TRANSPORT: mode=cli|mcp cli=<wersja|none> auth=ok|missing latest=<wersja|unknown> update=yes|no install=brew|pipx|uv|unknown|none`

| Wynik preflightu | Postepowanie |
|---|---|
| `mode=cli` | Operacje wykonujesz wrapperem `monolynx_op.py` (nizej). |
| `mode=mcp` | Tylko tools MCP, wrappera nie wolasz. |
| Jest linia `TRANSPORT-HINT:` (brak CLI, CLI niezalogowane albo nowsza wersja) | Przepisz ja uzytkownikowi jedna linia i pracuj dalej wedlug `mode`. Nie pytaj, nie czekaj, niczego nie instaluj. |
| `mode=mcp` i runtime nie udostepnia tooli MCP Monolynx | STOP, bez zgadywania. Podaj instrukcje: `pipx install monolynx-cli` i `monolynx auth login`, albo podpiecie connectora MCP Monolynx. |
| Preflight sie nie uruchomil (brak `python3`, odmowa uprawnienia) | Pracuj jak przy `mode=mcp`. |

**Operacja przy `mode=cli`** - pierwszy argument to nazwa toola, flagi to dokladne nazwy jego parametrow:

```bash
python3 <katalog skilla>/scripts/monolynx_op.py get_ticket --project_slug <PROJECT_SLUG> --ticket_id <KEY>
```

Wartosc bool to `true` albo `false`, lista to tablica JSON w jednej wartosci. Dluga tresc (komentarz, opis, strona wiki) i kazda wartosc ze znakami specjalnymi powloki: zapisz do pliku i podaj jako `@<sciezka pliku>`. Komend `monolynx ...` nie skladaj samodzielnie - mapowanie operacji na komendy CLI zyje we wrapperze.

Kody wyjscia wrappera:

- `0` - stdout to JSON API v2 z CLI. Niesie te same dane co tekstowa odpowiedz toola MCP, pod nazwami pol JSON.
- `3` z ostatnia linia `OP-FALLBACK: mcp tool=<nazwa>` - wywolaj ten tool MCP z tymi samymi argumentami; jego aktualny schemat poznaj mechanizmem runtime przed wywolaniem. To zwykla sciezka operacji bez komendy CLI (Pipelines, `log_time`, graf, LLM Wiki). Brak tego toola w runtime: stop z nazwa brakujacej operacji.
- `2` - blad operacji (API, siec, przekroczony czas). Przy zapisie nie ponawiaj na slepo przez MCP: najpierw odczytem sprawdz, czy zapis doszedl.
- `64` - blad uzycia wrappera, CLI nie bylo wolane. Popraw argumenty.

`get_ticket` przez CLI nie zwraca `spec_page_id` (linia `OP-NOTE:` na stderr) - gdy pole jest potrzebne, doczytaj je toolem MCP `get_ticket`.

Subagent pracuje przez MCP, chyba ze dostal w prompcie wynik `mode=cli` i literalna sciezke wrappera. `MONOLYNX_TRANSPORT=mcp` w srodowisku wymusza MCP w preflighcie i we wrapperze (zachowanie sprzed wprowadzenia CLI).
<!-- /shared:transport -->

## KROK 1: Zaladuj narzedzia

Zaladuj narzedzia Monolynx (rownolegle):

W katalogu dostępnych narzędzi znajdź funkcje Monolynx potrzebne poniżej i sprawdź ich schematy. Discovery jest opcjonalne, jeśli narzędzia są już dostępne.

<!-- runtime:claude-code -->
Opcjonalny wariant discovery w Claude Code:
```
ToolSearch(query="+monolynx ticket create sprint label board search", max_results=10)
ToolSearch(query="+monolynx wiki graph query node", max_results=10)
```
<!-- /runtime:claude-code -->

---

## KROK 2: Zrozum zadanie

- **Jesli podano opis** (faktyczny argument wywołania nie jest pusty):
  Uzyj go jako punkt wyjscia. Przejdz do KROK 3.

- **Jesli NIE podano opisu**:
  Zapytaj uzytkownika: **"Opisz krotko co chcesz zrobic - wystarczy 1-2 zdania."**
  Poczekaj na odpowiedz - NIE kontynuuj bez opisu.

---

## KROK 3: Zbierz kontekst

Uruchom rownolegle wszystkie cztery zrodla:

### 3a. Wiki - szukaj powiazanej dokumentacji

```
mcp__monolynx__search_wiki(project_slug="<PROJECT_SLUG>", query="<glowny temat zadania>")
```

Jesli wyniki sa istotne - pobierz pelne strony: `mcp__monolynx__get_wiki_page(...)`.

### 3b. Graf zaleznosci - sprawdz powiazania w kodzie

Przeszukaj graf projektu pod katem elementow zwiazanych z zadaniem:

```
mcp__monolynx__query_graph(project_slug="<PROJECT_SLUG>")
mcp__monolynx__list_graph_nodes(project_slug="<PROJECT_SLUG>", search="<nazwa pliku/klasy/funkcji>")
```

Jesli znaleziono istotne node'y - pobierz ich sasiedztwo:

```
mcp__monolynx__get_graph_node(project_slug="<PROJECT_SLUG>", node_id="<id>", depth=2)
```

**Cel**: Zidentyfikuj powiazane moduly, klasy i funkcje ktore moga byc dotkniete zmiana. Informacje z grafu wzbogacaja sekcje "Zakres" i "Zaleznosci" ticketu.

Jesli graf jest niedostepny (Neo4j wylaczony) - pomin ten krok i polegaj na analizie kodu (3c).

### 3c. Kod - sprawdz stan istniejacego kodu

Wykonaj odczytowe rozpoznanie kodu:

Zleć odczytowy research agentowi przez dostępny mechanizm delegacji, z poniższymi instrukcjami. Jeśli delegacja nie jest dostępna, wykonaj ten sam odczyt samodzielnie i odnotuj ograniczenie.

```text
Jestes Researcherem projektu Monolynx (FastAPI + SQLAlchemy + Jinja2 + HTMX).

ZADANIE DO ZREALIZOWANIA: [opis od uzytkownika]

Zbadaj aktualny stan kodu:
1. Znajdz pliki, modele, serwisy i endpointy powiazane z tym zadaniem (Glob, Grep, Read)
2. Sprawdz czy istnieja juz czesciowe implementacje lub powiazane mechanizmy
3. Zidentyfikuj zaleznosci - co musi istniec ZANIM to zadanie moze byc zrealizowane
4. Oszacuj zakres zmian (jakie pliki, ile modulow)
5. Wypisz przewidywane sciezki, ktore ticket zmieni LUB utworzy (takze nowe pliki
   i testy): repo-relatywne, bez globow, jedna na linie. Ta lista trafia do sekcji
   "Pliki dotykane" opisu - na jej podstawie przeglad sprintu wykrywa tickety,
   ktore nie powinny isc rownolegle.

Odpowiedz w formacie:
- Istniejacy kod: [co juz jest, sciezka:linia]
- Brakujace elementy: [co trzeba zbudowac]
- Zaleznosci: [od czego to zalezy - modele, serwisy, inne tickety]
- Pliki do zmiany: [lista plikow z krotkim opisem co zmienic]
- Pliki dotykane: [same sciezki z "Pliki do zmiany" plus pliki nowe, bez opisu; jedna repo-relatywna sciezka na linie, bez globow; nic w repo -> "brak (poza repo)"]
- Szacowany zakres: maly (1-2 pliki) / sredni (3-5 plikow) / duzy (6+ plikow)
```

### 3d. Istniejace tickety, sprinty i etykiety

Uruchom rownolegle:

```
mcp__monolynx__search_tickets(project_slug="<PROJECT_SLUG>", query="<slowa kluczowe zadania>")
mcp__monolynx__list_sprints(project_slug="<PROJECT_SLUG>")
mcp__monolynx__list_labels(project_slug="<PROJECT_SLUG>")
```

### 3e. Spec-page (opcjonalnie)

Jesli w wiki istnieje strona opisujaca specyfikacje/design zadania (wyniki z 3a moga na nia wskazywac), zapytaj uzytkownika:

> **Znalazlem strone wiki "[tytul]" ktora moze byc specyfikacja tego zadania. Czy chcesz ją powiazac z ticketem jako spec-page?**

Zapisz UUID strony jesli uzytkownik potwierdzi - uzyjesz go w `spec_page_id` przy tworzeniu ticketu.

---

## KROK 4: Sprawdz duplikaty i zaleznosci

Na podstawie wynikow z kroku 3d:

- **Jesli znaleziono duplikat** (ticket o takim samym celu):
  Poinformuj uzytkownika: **"Znalazlem istniejacy ticket [KEY] - [tytul] (status: [status]). Czy chcesz mimo to utworzyc nowy?"**
  Poczekaj na decyzje.

- **Jesli znaleziono powiazane tickety** (nie duplikaty, ale zaleznosci):
  Zapisz je - wylistujesz w sekcji "Zaleznosci" nowego ticketu.
  Rozdziel je na dwie grupy:
  - **blokery** - tickety, ktore musza byc `done`, zanim nowy ticket da sie zaczac. Dla kazdego zapamietaj klucz ORAZ UUID (pole `id` z wyniku `search_tickets`; `key` to klucz) - UUID trafi do `blocked_by_ids` w KROK 7,
  - **powiazane** - kontekst bez blokady startu (ten sam modul, podobny temat). Tylko w opisie, nie w `blocked_by_ids`.

Nie porownuj tu list plikow z innymi ticketami - pelne porownanie sekcji "Pliki dotykane" ticketow sprintu i propozycje blokerow robi `/monolynx:ticket-review sprint`.

---

## KROK 5: Zaproponuj ticket

Wygeneruj ticket w nastepujacym formacie i WYSWIETL go uzytkownikowi do akceptacji:

```markdown
**Tytul**: [krotki, konkretny - max 80 znakow, zaczyna sie od modulu jesli dotyczy jednego]
**Priorytet**: low / medium / high
**Story Points**: [1/2/3/5/8/13 - na podstawie szacowanego zakresu z kroku 3b/3c]
**Sprint**: [nazwa sprintu jesli oczywiste, lub "backlog"]
**Etykiety**: [jesli pasuja do istniejacych]

---

## Cel

[1-3 zdan: CO ma byc zrobione. Jednoznaczne, bez wieloznacznosci.]

## Kontekst

[1-3 zdan: DLACZEGO to zadanie istnieje. Jaki problem rozwiazuje, jaka wartosc daje.]

## Zakres

### 1. [Pierwszy obszar zmian]
- [Konkretna zmiana - z podaniem pliku/modulu jesli znany]
- [Parametry, sygnatury, zachowanie]

### 2. [Drugi obszar zmian]
- ...

[Kazdy obszar jako osobna podsekcja. Podaj nazwy plikow, endpointow, modeli gdzie to mozliwe.
Uwzglednij powiazania z grafu zaleznosci jesli sa istotne.]

## Pliki dotykane

- `[sciezka/repo-relatywna/plik.py]`
- `[sciezka/nowego/pliku_test.py]`

[Kazda sciezka w osobnej linii jako `- ` + sciezka w backtickach, bez opisu, bez globow.
Nowe pliki tez. Ticket bez plikow w repo -> jedna linia: `- brak (poza repo)`.]

## Nie ruszac

- [plik / modul / zachowanie, ktore ma zostac nietkniete - z powodem, np. "`services/auth.py` - inny ticket w toku", "format odpowiedzi `/api/v1/events` - konsumuje SDK"]
- [sasiedztwo z grafu (3b), ktore NIE jest potrzebne do zadania, a kusi]
- *Brak ograniczen poza zakresem powyzej* - jesli naprawde nic nie trzeba chronic

## Odwracalnosc

- Odwracalne: [co da sie cofnac zwyklym revertem - domyslnie caly kod]
- Nieodwracalne / wymaga zgody przed wykonaniem: [migracja DB (dane), wysylka maila/SMS/webhooka, publikacja pakietu, zmiana publicznego API lub schematu, usuniecie danych - albo *brak*]

## Zaleznosci

- Bloker: [KEY] [tytul] (status: [status]) - [dlaczego blokuje start]
- [KEY] [tytul] (status: [status]) - [dlaczego jest powiazany, bez blokady]
- [Modul/serwis] - [jesli zalezy od infrastruktury]
- [Element z grafu] - [jesli graf wskazal powiazanie warte uwagi]
- *Brak zaleznosci* - jesli zadanie jest niezalezne

## Kryteria akceptacji

- [ ] [Warunek 1 - konkretny, weryfikowalny]
- [ ] [Warunek 2 - ...]
- ...

[Kazde kryterium musi byc weryfikowalne - mozna jednoznacznie stwierdzic czy jest spelnione.
Pokryj: funkcjonalnosc, MCP tools (jesli dotyczy), UI (jesli dotyczy), testy (jesli zakres >= 5 SP).]
```

### Zasady generowania

1. **Tytul** - krotki i konkretny. Zaczyna sie od modulu jesli zmiana dotyczy jednego (np. "Wiki: upload zalacznikow do stron", "MCP: get_attachment tool")
2. **Story points** - mapuj na zakres z Researchera:
   - maly (1-2 pliki): **1-2 SP**
   - sredni (3-5 plikow): **3-5 SP**
   - duzy (6+ plikow): **8-13 SP**
3. **Zakres** - zawsze podawaj konkretne pliki/endpointy/modele z kroku 3b/3c. Agent realizujacy ticket musi wiedziec GDZIE w kodzie wprowadzac zmiany
4. **Zaleznosci** - wymien KAZDY powiazany ticket z kroku 3d/4 + infrastrukture z kroku 3c + powiazania z grafu z kroku 3b. Sekcja w opisie to uzasadnienie "dlaczego"; strukturalnie blokery zapisuje `blocked_by_ids` w KROK 7. Po utworzeniu `get_ticket` doklada do wyniku wlasna, generowana sekcje relacji `## Blokery (relacja)` (linie `Blokowany przez:` / `Blokuje:`, poza `## Description`). Sekcja `## Zaleznosci` w opisie to proza, `## Blokery (relacja)` to relacja - lista blokerow w opisie musi zgadzac sie z `blocked_by_ids`. W opisie pisz `Bloker:`, nie `Blokowany przez:`, zeby skille czytajace wynik `get_ticket` nie pomylily opisu z relacja
5. **Kryteria akceptacji** - minimum 3, maksimum 10. Kazde weryfikowalne. Kryteria z opisu sa AUTOMATYCZNIE tworzone jako acceptance criteria (checkboxy) przez parametr `acceptance_criteria` w `create_ticket` - nie trzeba dodawac ich osobno
6. **Nie ruszac** - granica jest czescia zadania, nie domyslem. Zrodla: sasiedztwo wezlow z grafu (3b) i pliki z raportu Explore (3c), ktorych zmiana NIE jest potrzebna; powiazane tickety w toku (3d) - ich pliki sa cudze; publiczne kontrakty (API, SDK, format eksportu). Kazda pozycja z powodem. "Brak ograniczen" wolno napisac tylko po sprawdzeniu tych zrodel, nie zamiast sprawdzenia
7. **Odwracalnosc** - wypisz operacje, ktorych nie cofnie `git revert`: migracja z danymi, wysylka na zewnatrz, publikacja, zmiana schematu/API, kasowanie. To jest lista rzeczy, przed ktorymi agent realizujacy ticket ma sie zatrzymac i zapytac (`work` buduje z niej kontrakt autonomii). Zadanie bez takich operacji -> *brak*, ale napisane wprost
8. **Pliki dotykane** - lista z pola "Pliki dotykane" Researchera (3c), zgodna z sekcja "Zakres": kazdy plik wymieniony w "Zakres" jest na liscie, nowe pliki i testy tez. Format sztywny, bo czyta go skrypt: jedna linia `- \`sciezka/repo-relatywna\`` na plik, bez opisu, bez globow (`*`, `**`), bez sciezek absolutnych i `..`. Ticket bez plikow w repo -> jedna linia `- brak (poza repo)`. Pliki "zawsze wspolne" (changelog, liczniki skilli, kopie w `src/monolynx/static/skills/`) tez wpisuj - przeglad sprintu sam je odfiltruje
9. **Jezyk** - polski (terminy techniczne w oryginale)

---

## KROK 6: Akceptacja uzytkownika

Wyswietl wygenerowany ticket i zapytaj:

> **Oto proponowany ticket. Co chcesz zmienic?**
> - **(a)** Akceptuj i utworz
> - **(b)** Zmien [wymien co]
> - **(c)** Podziel na mniejsze tickety (jesli zakres > 8 SP)

**Poczekaj na odpowiedz** - NIE twórz ticketu bez akceptacji.

- **Jesli (a)** → przejdz do KROK 6a
- **Jesli (b)** → popraw wedlug uwag, wyswietl ponownie, zapytaj jeszcze raz
- **Jesli (c)** → zaproponuj podzial na 2-4 mniejsze tickety, kazdy z pelna forma. Zapytaj o akceptacje kazdego z osobna.

---

## KROK 6a: Opcjonalnie - Spec-page

Po potwierdzeniu opcji **(a) Akceptuj i utworz** - zapytaj uzytkownika:

Zapytaj użytkownika: **"Czy chcesz teraz stworzyc spec-page dla tego ticketu w wiki?"**

Opcje (pierwsza jest domyslna):
- **"Nie, pomin"** (domyslna - nieinwazyjna)
- **"Tak, stworz spec-page"**

**Jesli "Tak, stworz spec-page":**

1. Utworz strone wiki:

```
mcp__monolynx__create_wiki_page(
  project_slug="<PROJECT_SLUG>",
  title="Spec: <tytul ticketu>",
  content="---\ntype: spec\nstatus: draft\n---\n\n## Cel\n[co ma robic]\n\n## Decyzje architektoniczne\n[dlaczego tak, nie inaczej]\n\n## Nie robimy\n[explicit out-of-scope]"
)
```

2. Pobierz `id` z odpowiedzi (UUID nowej strony).
3. W KROK 7 przy wywolaniu `create_ticket` dodaj parametr `spec_page_id=<id>`.
4. Pokaz uzytkownikowi: **"Spec-page utworzona: Spec: [tytul ticketu] (bedzie powiazana z ticketem)"**

**Jesli "Nie, pomin":**

Przejdz do KROK 7 bez `spec_page_id`.

---

## KROK 7: Utworz ticket

**WAZNE**: `create_ticket` przyjmuje parametr `acceptance_criteria` - liste opisow kryteriow akceptacji. Kryteria sa tworzone razem z ticketem w jednym requeście (nie trzeba dodawac ich osobno).

```
mcp__monolynx__create_ticket(
  project_slug="<PROJECT_SLUG>",
  title="<tytul>",
  description="<pelny opis w markdown - sekcje Cel, Kontekst, Zakres, Pliki dotykane, Nie ruszac, Odwracalnosc, Zaleznosci, Kryteria akceptacji>",
  priority="<low/medium/high>",
  story_points=<liczba>,
  sprint_id="<UUID sprintu lub null dla backlogu>",
  label_ids=[<lista UUID etykiet lub null>],
  acceptance_criteria=["<kryterium 1>", "<kryterium 2>", "<kryterium 3>", ...],
  spec_page_id="<UUID strony wiki lub null>",
  blocked_by_ids=["<UUID blokera 1>", "<UUID blokera 2>"]
)
```

`blocked_by_ids` - UUID ticketow-blokerow z KROK 4 (grupa "blokery"), tylko z tego samego projektu. Klucze `KEY-123` nie sa przyjmowane - podaj UUID. Ticket bez blokerow: pomin parametr. Nie dopisuj "powiazanych" - kazdy wpis tutaj zatrzymuje `work` / `work-simple` i `sprint-run`, dopoki bloker nie jest `done`.

Jesli `create_ticket` odrzuci blokery (inny projekt, self-reference, cykl) - pokaz uzytkownikowi komunikat bledu, popraw liste i wywolaj ponownie; nie tworz ticketu bez blokerow po cichu.

### Po utworzeniu:

1. Wyswietl uzytkownikowi potwierdzenie z kluczem I identyfikatorem:

> **Utworzono ticket [KEY] - [tytul]**
> ID: `[UUID]`

2. Jesli przekazano `blocked_by_ids` - wymien blokery w potwierdzeniu (`Blokowany przez: [KEY1], [KEY2]`). Osobny komentarz z zaleznosciami nie jest potrzebny - `get_ticket` pokazuje je w sekcji `## Blokery (relacja)`.

---

## KROK 8: Opcjonalnie - seria ticketow

Jesli uzytkownik opisal wiekszy zakres prac (np. "caly modul X"), zaproponuj:

> **Zakres wydaje sie na wiecej niz jeden ticket. Chcesz, zebym zaproponowal serie ticketow pokrywajacych calosc?**

Jesli tak - powtorz KROK 5-7 dla kazdego ticketu z serii, pilnujac:
- Kazdy ticket jest samodzielny (moze byc zrealizowany niezaleznie)
- Zaleznosci miedzy ticketami sa jawnie zapisane - tworz tickety w kolejnosci realizacji, a UUID wczesniejszego (pole `id` z odpowiedzi `create_ticket`) podawaj w `blocked_by_ids` kolejnego, jesli go blokuje
- Story points w serii sumuja sie logicznie
- Kolejnosc realizacji wynika z zaleznosci

---

## WAZNE ZASADY

1. **NIE twórz ticketu bez akceptacji uzytkownika** - KROK 6 jest obowiazkowy
2. **Zawsze zbieraj kontekst** (wiki + graf + kod + istniejace tickety) przed pisaniem - KROK 3 jest obowiazkowy
3. **Graf zaleznosci jest waznym zrodlem** - jesli jest dostepny, ZAWSZE go odpytaj. Informacje z grafu pomagaja precyzyjnie okreslic zakres zmian i powiazania miedzy modulami
4. **Ticket musi byc zrozumialy dla AI-agenta** - agent musi moc go podjac i zrealizowac bez dodatkowych pytan
5. **Badz konkretny** - nazwy plikow, endpointow, modeli, sygnatury. Nie pisz "zmodyfikuj odpowiedni serwis" - pisz "zmodyfikuj `services/wiki.py` - dodaj funkcje `get_page_attachments()`"
6. **Nie nadmuchuj zakresu** - jesli uzytkownik chce prosta zmiane, nie dodawaj "przy okazji" dodatkowych usprawnien. Za to "przy okazji" nasuwajace sie z grafu/kodu wpisz do "Nie ruszac" - granica zapisana jest tansza niz granica odkryta po przekroczeniu
7. **Story points musza odpowiadac zakresowi** - nie dawaj 2 SP na zadanie z 8 plikami do zmiany
8. **Jezyk**: polski (terminy techniczne w oryginale)
9. **Duplikaty sa blokerem** - jesli istnieje duplikat, ZAWSZE informuj uzytkownika przed utworzeniem
10. **Po utworzeniu zawsze pokazuj KEY + UUID** - uzytkownik potrzebuje obu identyfikatorow
11. **Blokery strukturalnie** - ticket, ktory blokuje start, idzie do `blocked_by_ids` (UUID), nie do komentarza. Lista blokerow w sekcji "Zaleznosci" opisu = lista `blocked_by_ids`
12. **Pliki dotykane zawsze** - kazdy opis ma sekcje "Pliki dotykane" w formacie z zasady generowania 8. Bez niej przeglad sprintu (`/monolynx:ticket-review sprint`) nie porowna ticketu z innymi i nie zaproponuje blokera, a dwa tickety na tych samych plikach pojda w `sprint-run` rownolegle
