# Next Steps — Modul Scrum (MVP)

> Raport przygotowany przez zespol trzech agentow: **JIRA Specialist**, **Front-endowiec** i **Krytyk**.
> Data: 2026-02-20

---

## Podsumowanie

Modul Scrum w Open Sentry ma solidne fundamenty: CRUD ticketow, backlog, tablice Kanban (4 kolumny), sprinty z pelnym cyklem zycia i przypisywanie czlonkow zespolu. Trzech agentow niezaleznie przeanalizowalo modul — JIRA Specialist zaproponowal 10 funkcji, Front-endowiec 11 usprawnien UX/UI. Krytyk skonsolidowal obie listy, odrzucil 50% propozycji i wylonil **8 priorytetowych pozycji**.

---

## Skonsolidowana lista (werdykt Krytyka)

### P1 — Must-have (bez tego Scrum board to zabawka)

#### 1. Drag & drop na tablicy Kanban
- **Zlozonosc**: M (2-3 dni)
- **Zrodlo**: JIRA Specialist + Front-endowiec (zgodnosc obu)
- **Opis**: Tablica Kanban bez przeciagania to lista w kolumnach. Endpoint PATCH (`/tickets/{id}/status`) juz istnieje, HTMX zaladowany — potrzeba SortableJS (~8KB) + atrybuty HTMX. To jest **definicja** tablicy Kanban.
- **Uzasadnienie Krytyka**: To jedyna funkcja, ktorej brak jest natychmiast widoczny. Czysto frontendowa robota — backend gotowy.

#### 2. Filtrowanie i wyszukiwanie w backlogu
- **Zlozonosc**: S (1-2 dni)
- **Zrodlo**: JIRA Specialist
- **Opis**: Backlog z >20 ticketami bez filtrow jest niezdatny do uzytku. Filtry: status, priorytet, assignee, sprint + wyszukiwanie po tytule. HTMX reload. Proste query params na backendzie (dodanie `.where()` warunkow).
- **Uzasadnienie Krytyka**: S-ka z ogromna wartoscia. HTMX juz jest w stacku, wiec implementacja jest prosta.

#### 3. Przenoszenie ticketow do sprintu z backlogu
- **Zlozonosc**: S (0.5-1 dzien)
- **Zrodlo**: JIRA Specialist
- **Opis**: Podstawowa operacja sprint planning. Dropdown/przycisk na karcie ticketu w backlogu pozwalajacy szybko przypisac ticket do sprintu. Opcjonalnie bulk action: checkboxy + "Przenies zaznaczone do sprintu X". Jeden nowy endpoint.
- **Uzasadnienie Krytyka**: Bez tego musisz wejsc w edycje kazdego ticketu zeby przypisac sprint — absurdalny workflow.

#### 4. Licznik story points w sprincie i na tablicy
- **Zlozonosc**: S (0.5-1 dzien)
- **Zrodlo**: JIRA Specialist + Front-endowiec (zgodnosc obu)
- **Opis**: Dane juz istnieja (`story_points` na Ticket). Brakuje `SUM()` w query i wyswietlenia: suma SP w naglowku sprintu, per kolumna na boardzie, progress bar (done/total). Na stronie sprintow — "12 ticketow, 34 SP" obok kazdego sprintu.
- **Uzasadnienie Krytyka**: Trywialne do implementacji (pare SQL SUM + template). Zero nowych modeli, zero migracji. Duza wartosc informacyjna.

---

### P2 — Nice-to-have (wyraznie poprawiaja UX, warte zrobienia po P1)

#### 5. Toast notifications (flash messages)
- **Zlozonosc**: S (0.5-1 dzien)
- **Zrodlo**: Front-endowiec
- **Opis**: Brak jakiegokolwiek feedbacku po akcjach (tworzenie ticketu, zmiana statusu) to slaby UX. Prosty system: session flash + div z auto-dismiss JS (3-4s). Rozne kolory wg typu (sukces/blad/info). Podstawa pod wszystkie przyszle interakcje HTMX.
- **Uzasadnienie Krytyka**: Fundamentalny element UX. Teraz po wiekszosci akcji jest redirect i cisza — uzytkownik nie wie, czy akcja sie udala.

#### 6. Inline edycja statusu na backlogu
- **Zlozonosc**: S (1 dzien)
- **Zrodlo**: Front-endowiec
- **Opis**: Zmiana statusu wymaga wejscia w edycje ticketu. Dropdown HTMX na liscie backlogu (`hx-patch` z podmiana fragmentu HTML) eliminuje zbedna nawigacje. Wizualny feedback po zmianie.
- **Uzasadnienie Krytyka**: Dobry komplement do filtrowania (P1 #2). Redukuje klikniecia. Maly koszt, zauwalzalna poprawa workflow.

#### 7. Komentarze do ticketow
- **Zlozonosc**: M (2-3 dni)
- **Zrodlo**: JIRA Specialist
- **Opis**: Jedyny sposob komunikacji w kontekscie ticketu. Nowy model `TicketComment` (id, ticket_id, user_id, content, created_at). Formularz pod opisem ticketa + lista komentarzy. Bez edycji/usuwania na start.
- **Uzasadnienie Krytyka**: MVP moze zyc bez komentarzy (jest Slack/email), ale kazdy PM tego oczekuje. Wartosc rosnie z rozmiarem zespolu.

---

### P3 — Pozniej (dobre pomysly, zly moment)

#### 8. Typy ticketow (bug/task/story)
- **Zlozonosc**: S (1 dzien)
- **Zrodlo**: JIRA Specialist
- **Opis**: Nowe pole `ticket_type` w modelu Ticket (enum: task, bug, story; default task). Ikony roznicujace w backlogu i na tablicy. Filtrowanie po typie.
- **Uzasadnienie Krytyka**: Konwencja, nie wymog. Priorytet + opis wystarczaja do rozroznienia na MVP. Dodamy gdy backlog bedzie na tyle duzy, ze filtrowanie po typie stanie sie koniecznoscia.

---

## Odrzucone propozycje (10 z ~20)

| Propozycja | Zrodlo | Powod odrzucenia |
|-----------|--------|-----------------|
| **Etykiety/tagi** | JIRA Specialist | YAGNI. Relacja M2M + nowy model + UI do zarzadzania. Typy ticketow pokrywaja 80% potrzeby kategoryzacji. |
| **Historia zmian ticketu** | JIRA Specialist | Over-engineering. Event sourcing lite — nowy model, triggery, timeline UI. Przy zespole <5 osob wystarczy zapytac kto co zmienil. |
| **Quick view ticketu (modal)** | JIRA Specialist | Rozwiazanie bez problemu. Jedno klikniecie i jestes na ticket detail. Modal dodaje zlozonosc bez realnej wartosci. |
| **Sortowanie backlogu drag & drop** | JIRA Specialist | Pole `order` istnieje, ale drag w liscie to sporo JS za malo wartosci. Filtrowanie daje wiecej za mniej. |
| **Skeleton loading states** | Front-endowiec | To server-rendered app, nie React SPA. Strony renderuja sie w <100ms. Problem nie istnieje. |
| **Keyboard shortcuts** | Front-endowiec | Nice-to-have na etapie "mamy 1000 uzytkownikow". Nikt nie zapamietuje skrotow w nowej app. |
| **Empty states z ilustracjami** | Front-endowiec | Kosmetyka. Backlog juz ma basic empty state z linkiem. SVG ilustracje to praca designera, nie developera. |
| **Modal potwierdzenia usunieccia** | Front-endowiec | `confirm()` przegladarki dziala, jest dostepny i zrozumialy. Custom modal to dodatkowy JS za zero wartosci biznesowej. |
| **Kolorowe badge priorytetow** | Front-endowiec | Juz sa kolorowe kropki. Zmiana z kropki na badge to bikeshedding — priorytet jest czytelny. |
| **Responsywna nawigacja** | Front-endowiec | Narzedzie do zarzadzania projektami uzywane na desktopie. Nikt nie zarzadza sprintem na telefonie. |

---

## Plan realizacji (sugerowana kolejnosc Krytyka)

Krytyk celow przesunol drag & drop na pozycje #6 w kolejnosci implementacji mimo P1, bo wszystkie S-ki przed nim mozna zrobic szybciej i daja natychmiastowa wartosc.

```
Faza 1 — Quick wins (tydzien 1):
  1. [P2] Toast notifications (S, 0.5d) — bazowa infrastruktura dla feedbacku
  2. [P1] Licznik story points (S, 0.5d) — najszybszy quick win
  3. [P1] Przenoszenie ticketow do sprintu (S, 1d) — niezbedne dla planowania
  4. [P1] Filtrowanie backlogu (S, 1.5d) — niezbedne dla skali
  5. [P2] Inline edycja statusu (S, 1d) — quick win UX

Faza 2 — Core feature (tydzien 2):
  6. [P1] Drag & drop na tablicy (M, 2-3d) — najwiekszy impact

Faza 3 — Rozszerzenie (tydzien 3):
  7. [P2] Komentarze (M, 2-3d) — moze isc rownolegle z #6

Pozniej:
  8. [P3] Typy ticketow (S, 1d)
```

---

## Indywidualne raporty agentow

### JIRA Specialist — 10 propozycji

| # | Funkcja | Zlozonosc | Wartosc | Quick win? |
|---|---------|-----------|---------|------------|
| 1 | Drag & drop na tablicy | M | Wysoka | |
| 2 | Filtrowanie backlogu | S | Wysoka | yes |
| 3 | Bulk assign do sprintu | M | Wysoka | |
| 4 | Podsumowanie sprintu (SP) | S | Wysoka | yes |
| 5 | Szybka zmiana assignee | S-M | Srednia | |
| 6 | Komentarze | M | Srednia | |
| 7 | Typ ticketa (bug/task/story) | S | Srednia | yes |
| 8 | Reorder backlogu (drag) | M | Srednia | |
| 9 | Statystyki sprintow | S | Srednia | yes |
| 10 | Inline tworzenie ticketa | S | Srednia | yes |

**Rekomendacja**: Zaczac od quick wins (2, 4, 7, 9, 10), potem drag & drop (1) i bulk assign (3).

### Front-endowiec — 11 usprawnien UX/UI

| # | Usprawnienie | Zlozonosc | Wplyw |
|---|---|---|---|
| 1 | Aktywna strona w nav Scrum | S | wysoki |
| 2 | Drag & Drop Kanban | M | wysoki |
| 3 | Suma SP na tablicy/backlogu | S | wysoki |
| 4 | Szybka zmiana statusu na ticket detail (HTMX) | S | wysoki |
| 5 | Responsywnosc tablicy | M | sredni |
| 6 | Tooltip na priorytetach | S | sredni |
| 7 | Info o sprincie nad tablica | S | sredni |
| 8 | Nawigacja "wroc" w ticket detail | S | sredni |
| 9 | Liczba ticketow przy sprintach | S | sredni |
| 10 | Przycisk "Anuluj" w formularzu | S | niski-sredni |
| 11 | Animacja hover kart (CSS) | S | niski |

**Rekomendacja**: Quick wins (1, 3, 4, 6, 7, 8, 10, 11) — same S-ki, duzy efekt. Potem D&D (2) i responsywnosc (5).

### Krytyk — werdykt

- Odrzucil **10 z ~20 propozycji** (50%) jako over-engineering, kosmetyke lub rozwiazania nieistniejacych problemow
- Skonsolidowal duplikaty (drag & drop, liczniki SP — pojawialy sie na obu listach)
- Wylonil **4 pozycje P1**, **3 pozycje P2**, **1 pozycje P3**
- Kluczowa obserwacja: "Lepiej miec 5 quick wins shipped niz 1 M w polowie zrobiony"

---

## Kluczowe wnioski

1. **Zgodnosc miedzy agentami**: Drag & drop i liczniki SP pojawialy sie na obu listach niezaleznie — to potwierdza ich priorytet.
2. **4 pozycje P1 to ~4-5 dni pracy** (3x S + 1x M) — realistyczne na 1-2 tygodnie z testami.
3. **Backend jest gotowy** na wiele z tych zmian: endpoint PATCH istnieje, HTMX zaladowany, pole `order` i `story_points` juz w modelu.
4. **Najwieksza wartosc za najmniejszy koszt**: toast notifications, licznik SP i filtrowanie backlogu — kazde <1 dzien, a razem zmieniaja percepcje narzedzia.
5. **Najwieksza pojedyncza zmiana**: drag & drop na tablicy — przeksztalca statyczna strone w interaktywne narzedzie.
