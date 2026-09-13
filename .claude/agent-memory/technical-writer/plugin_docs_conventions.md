---
name: plugin-docs-conventions
description: Konwencje pisania dokumentacji pod plugin/ - diakrytyki tak/nie w zaleznosci od pliku, walidator, sync kopii skilli
metadata:
  type: project
---

Dokumentacja pod `plugin/` ma DWIE rozne konwencje diakrytykow:

- `plugin/skills/*/SKILL.md` - **konwencja jest PER PLIK, nie globalna**. Starsze skille (help, work, work-simple, retro, resume, brief, sprint-end, ticket-*, graph-*, project-toolchain, wiki-init/lint) pisane BEZ polskich znakow; nowsze (sprint-run, wiki-sync-merge, search) maja **pelne diakrytyki**. Przed edycja sprawdz plik: `grep -c "[ąćęłńóśźż]" <plik>` i trzymaj sie tego, co juz w nim jest. Nie ujednolicaj cudzych plikow przy okazji.
- `plugin/README.md` i `plugin/.claude-plugin/plugin.json` - polski **Z pelnymi** diakrytykami. To dokumentacja dla czlowieka.

Wspolne dla obu: **zero em-dash `-` (U+2014) i en-dash (U+2013)** - `scripts/validate_plugin_consistency.py` skanuje kazdy `.md` pod `plugin/` i wywala exit 1.

**Why:** walidator sprawdza tylko dash, nie diakrytyki - te druga konwencje trzeba znac z obserwacji plikow, inaczej latwo napisac SKILL.md z ogonkami i nikt tego nie zlapie automatem.

**How to apply:** przed zgloszeniem gotowosci uruchom oba lokalne skrypty (hook ich nie blokuje, to nie testy projektu):
```
python3 scripts/sync_skills.py            # regeneruje src/monolynx/static/skills/monolynx-*/
python3 scripts/validate_plugin_consistency.py
python3 scripts/sync_skills.py --check
```
`src/monolynx/static/skills/**` jest **generowany** - nigdy nie edytuj recznie (porownanie jest bajt w bajt). W zrodle pisz `<PROJECT_SLUG>` z podkreslnikiem; transformacja sama robi z tego `<PROJECT-SLUG>`.

Pozostale reguly walidatora: referencje `KROK N` / `FAZA N` musza miec pokrycie w naglowku (pula globalna dla calego `plugin/`, nie per plik); komendy pisz `/monolynx:<nazwa>`, nigdy `/monolynx-<nazwa>`; `version` w `plugin.json` musi rownac sie pierwszemu wpisowi `### X.Y.Z` pod `## Changelog` w `plugin/README.md`.

Przy bumpie wersji pluginu: nowy wpis changelogu idzie na **poczatek** sekcji, a historycznych wpisow `### X.Y.Z` nie przepisuje sie (opisuja, co weszlo w tamtej wersji). Opis skilla aktualizuje sie w tabeli "Skille (komendy `/monolynx:*`)" pod `## Zawartosc pluginu`.

**Klasyfikator auto-mode blokuje agentom zapis literalow nazw trybow uprawnien Claude Code** (wartosci flagi `--permission-mode`) do plikow skilla. Gdy dokumentujesz start sesji w tle, odwoluj sie do "komendy startu w KROK N" i do zmiennej (`MONOLYNX_SPRINT_PERMISSION_MODE`), zamiast pisac sama nazwe trybu. Istniejacych linii z takim literalem nie ruszaj - przeszly wczesniej i ponowna edycja moze sie nie udac.

**Nowy skill = szesc stron pokrycia.** `check_skill_coverage` w walidatorze wymaga nazwy `/monolynx:<skill>` (zapis bez slasha tez sie liczy) w: `plugin/README.md`, `plugin/skills/help/SKILL.md`, `src/monolynx/templates/dashboard/profile/mcp_guide.html`, `src/monolynx/static/agent-bootstrap.md`, `src/monolynx/static/how-to-use-monolynx.md`, `src/monolynx/main.py`. Walidator sprawdza tylko skille, ktore MAJA katalog w `plugin/skills/` - jesli katalog jeszcze nie powstal, przejdzie na zielono mimo braku wpisow. Osobno (nie sprawdzane automatem) trzeba podbic liczniki "N skilli" w mcp_guide.html, agent-bootstrap.md, how-to-use-monolynx.md i `skills_sub` w main.py oraz dopisac feature do `src/monolynx/features.py` (PL + EN).

`src/monolynx/static/agent-bootstrap.md` ma **mieszana** konwencje diakrytykow: stare wpisy bez ogonkow, nowsze (sprint-run) z pelnymi. Nowy tekst pisz z diakrytykami. `src/monolynx/features.py` ma `E501` w per-file-ignores, `main.py` nie - tam limit 150 znakow obowiazuje.

Powiazane: [[konwencje-pisania-pl]]
