---
name: resume
description: "Wznowienie pracy po /clear, kompakcji albo nastepnego dnia: odtwarza stan ticketu z trwalych zrodel (ticket, komentarze Team Managera, pipeline, kryteria akceptacji, git) i wypisuje 'gdzie jestesmy, co zostalo, nastepny krok'. Read-only - niczego nie zmienia. Uzyj zamiast trzymania jednego watku przez wiele dni."
user-invocable: true
argument-hint: [ticket-id lub klucz np. PROJ-12; puste = moje tickety in_progress]
---

# Resume - odtworzenie stanu pracy

Przed pierwszą operacją obowiązkowo przeczytaj [Kontrakt runtime](references/runtime.md) i stosuj go w całym przebiegu. Narzędzia, argumenty, pytania i uruchamianie zależnych skilli dobieraj do faktycznie dostępnych możliwości klienta. Nazwy `/monolynx:*` oznaczają wariant Claude Code; w Codex użyj nazwy z katalogu zainstalowanych skilli albo odczytaj dostarczony `SKILL.md`, bez wymyślania komend.

Kontekst pracy nie zyje w watku rozmowy - zyje w Monolynx (komentarze, pipeline,
kryteria) i w gicie. Ten skill sklada z nich jeden obraz i mowi, co dalej.
Dzieki temu `/clear` miedzy epizodami nic nie kosztuje.

**Skill jest read-only.** Nie zmienia statusu, nie komentuje, nie loguje czasu,
nie dotyka plikow. Prace podejmuje `/monolynx:work` / `/monolynx:work-simple`
(oba umieja wznowic od etapu, ktory ten skill wskaze).

---

## Ustalenie slug projektu

Ustal `project_slug` zgodnie z kontraktem runtime: jawny argument użytkownika, następnie `MONOLYNX_PROJECT_SLUG` w środowisku procesu, następnie jawnie odczytana konfiguracja klienta lub `.env` projektu. Konfigurację czytaj jako dane, bez `source` i bez wypisywania sekretów. Brak sluga: zapytaj użytkownika i zatrzymaj operacje projektowe; nie zgaduj go. W przykładach MCP używaj `<PROJECT_SLUG>`.

## KROK 1: Zaladuj narzedzia i wybierz ticket

W katalogu dostępnych narzędzi znajdź funkcje Monolynx potrzebne poniżej i sprawdź ich schematy. Discovery jest opcjonalne, jeśli narzędzia są już dostępne.

<!-- runtime:claude-code -->
Opcjonalny wariant discovery w Claude Code:
```
ToolSearch(query="+monolynx ticket board comment acceptance", max_results=10)
ToolSearch(query="+monolynx pipeline list get job log", max_results=10)
```
<!-- /runtime:claude-code -->

Nazwę każdego narzędzia i jego schemat odczytaj z katalogu runtime; przykłady MCP mapuj na faktycznie udostępnione funkcje.

Faktyczne argumenty wiadomości użytkownika lub wywołania:

- **Podano identyfikator** -> `get_ticket(project_slug, ticket_id)`. Klucz
  odrzucony -> `search_tickets(query=<klucz>)` i uzyj ID.
- **Brak** -> `get_board(project_slug)`, kolumna **In Progress** (wiersze maja
  `@assignee`). Znasz swoj e-mail -> wez tickety z Twoim assignee; nie znasz ->
  wszystkie z kolumny. Dokladnie jeden -> wez go. Kilka -> wypisz (KEY, tytul,
  SP) i zapytaj, ktory. Zero -> powiedz uzytkownikowi, ze nie ma nic w toku
  (kolumny todo/in_review wypisz jako kontekst); zaproponuj `/monolynx:work`
  z wyborem z tablicy. Zakoncz.
  NIE uzywaj `search_tickets(assignee_email="me")` - ten tool nie zna aliasu
  `me` (porownuje e-mail doslownie) i zwraca pusta liste bez bledu.

## KROK 2: Zbierz zrodla (rownolegle, wszystkie best-effort)

Kazde zrodlo moze byc puste albo niedostepne - wtedy odnotuj "brak" i idz
dalej. Nie przerywaj z powodu jednego braku.

1. **Komentarze**: `list_comments(project_slug, ticket_id)`. Szukaj wpisow
   Team Managera i agentow (w tej kolejnosci znaczenia):
   - "Team Manager - Plan pracy" / "Team Manager Lite - Plan" -> przydzial
     plikow, kontrakt autonomii, plan realizacji, tryb, baseline
     (`BASELINE_SHA`, pliki brudne przed startem), `pipeline_id` i `job_id`
     agentow;
   - "Team Manager Lite - Mini-research" / raport Researchera w logu joba;
   - "[agent] - Podsumowanie pracy" -> co ktory agent zrobil;
   - "Krytyk - Review" / "Code Review" -> score, werdykt, REGULA;
   - "Team Manager - Podsumowanie zadania" -> epizod byl domkniety (wtedy
     stan = in_review lub done, resume ma malo do roboty);
   - "Eskalacja", "SCOPE GREW", "STOP:" -> otwarte decyzje.
2. **Kryteria akceptacji**: `list_acceptance_criteria` -> odhaczone / nie.
3. **Pipeline**: `pipeline_id` jest w komentarzu planu (pole "Pipeline") ->
   od razu `get_pipeline(project_slug, pipeline_id)`. Brak w komentarzu
   (starszy plan) -> `list_pipelines(project_slug, pipeline_type="ticket_work")`
   nie ma filtra po tickecie: porownuj pole `ticket_key` w wynikach i iteruj
   po `page` (20 na strone, `total_pages` w odpowiedzi), az znajdziesz
   najnowszy pipeline tego ticketu. `get_pipeline` -> joby ze statusami
   i `attempt`. Ostatni job `success`/`failed`/`running` to ostatni domkniety
   lub otwarty etap. Potrzebujesz tresci raportu (np. raportu Researchera do
   wznowienia) -> `get_pipeline_job_log(project_slug, job_id)`.
4. **Git** (jesli repo dostepne):

   ```bash
   git branch --show-current; git status --porcelain; git log --oneline -5; git stash list | head -3
   ```

   Brudne pliki porownaj z przydzialem z komentarza planu: pliki z przydzialu
   = praca w toku; pliki spoza = cudza/wczesniejsza praca albo naruszenie.
   `git stash list` niepusty -> przypomnij (mogl zostac z Baseline review w KROK 5 `work` albo ze schowanego fixu w 6.5 w trybie defektu).

## KROK 3: Ustal etap

Mapa etapow `work` (dla `work-simple` analogicznie: FAZA 0-4):

| Slad | Etap domkniety | Nastepny krok |
|---|---|---|
| brak komentarza planu, brak pipeline | nic - praca nie ruszyla | `/monolynx:work <KEY>` od zera |
| komentarz planu, job `researcher` success, joby agentow `pending` | plan | `work` -> wznow od KROK 6 (spawn zespolu) |
| joby agentow `success`, brak `lint-test` | developerzy | `work` -> wznow od 6e (rekoncyliacja) i 6.5 (lint) |
| `lint-test` success, job krytyka `pending`/`running` | lint | `work` -> wznow od 6.6 (krytyk) |
| review z NEEDS WORK, `attempt` > 1 na jobie agenta, brak nowego review | iteracja w toku | `work` -> wznow od 6.6b (re-run / ponowna ocena) |
| review APPROVED, brak komentarza podsumowania | review | `work` -> wznow od 6.6c (pelny lint) i 6.7 |
| komentarz "Podsumowanie zadania", status nadal `in_progress` | praca skonczona, zamkniecie przerwane (sesja umarla miedzy 7b a 7e) | `work` -> dokoncz 7d-7e: commit wg flag, status `in_review`, domkniecie pipeline; bez re-runu zespolu |
| komentarz "Podsumowanie zadania", status in_review | epizod domkniety | commit/push jesli brudne pliki; po merge `/monolynx:wiki-sync-merge` |
| komentarz "Eskalacja" / "SCOPE GREW" | przerwany | decyzja uzytkownika: `work` (full) albo revert |

Slady sprzeczne (np. pipeline `running` od 2 dni, a komentarz podsumowania
istnieje) -> wypisz obie wersje i wskaz, ktorej ufasz (komentarze sa zapisywane
po pracy, pipeline moze zostac niedomkniety po smierci sesji).

## KROK 4: Raport

Wypisz - zwiezle, bez tabel dluzszych niz 8 wierszy:

> **Stan [KEY] - [tytul]** (status: [status], SP: [n], tryb: [zmiana/defekt/nieznany])
>
> - Branch: `[nazwa]`; brudne pliki: [n] ([z przydzialu: x, spoza: y]); stash: [tak/nie]
> - Plan z [data]: agenci [lista] -> pliki [lista]; kontrakt: stop przed [lista lub "brak w planie"]
> - Ostatni domkniety etap: **[etap]** ([dowod: job X success o HH:MM / komentarz Y])
> - Kryteria akceptacji: [odhaczone]/[wszystkie]; niezrealizowane: [lista]
> - Otwarte: [NEEDS WORK score N z uwagami / STOP: powod / eskalacja / brak]
> - Reguly krytyka z tego ticketu: [lista lub brak]
>
> **Nastepny krok:** `[komenda, np. /monolynx:work MON-12]` - [jedno zdanie:
> od ktorego etapu i czego nie powtarzac, np. "wznow od 6.6, Researcher
> i developerzy juz zrobieni, raport w logu joba researcher"]

Uzytkownik uruchamia `/monolynx:work` sam - ten skill go nie odpala (to jego
decyzja, czy wznawia teraz).

---

## WAZNE ZASADY

1. **Read-only, bez wyjatkow** - zadnych `update_*`, `add_comment`, `log_time`,
   zadnych zmian w plikach ani gicie; odczyty git ogranicz do
   `git branch/status/log/stash list`
2. **Kazde zrodlo best-effort** - brak pipeline'u, brak repo, brak komentarzy
   to informacja ("praca nie ruszyla" / "runtime bez gita"), nie blad
3. **Dowod przy etapie** - "ostatni domkniety etap" zawsze z jobem lub
   komentarzem, ktory to potwierdza; bez dowodu napisz "nieznany"
4. **Nie streszczaj komentarzy** - wyciagaj z nich fakty potrzebne do wznowienia
   (przydzial, kontrakt, score, otwarte STOP), reszte pomijaj
5. **Jezyk**: polski, z pelnymi diakrytykami w tresci dla uzytkownika
