---
name: migration-head-verify
description: Przy review migracji Alembic ZAWSZE weryfikuj down_revision = faktyczny head i unikalnosc revision ID
metadata:
  type: feedback
---

Przy review kazdej migracji Alembic sprawdz DWIE rzeczy niezaleznie od tego co mowi ticket:

1. **Unikalnosc revision ID** - czy `revision = "X"` nie koliduje z istniejacym plikiem. Grep: `grep -rl '"X"' alembic/versions/`. Kolizja = Alembic nie zaladuje grafu (multiple revisions / KeyError).
2. **down_revision = faktyczny head** - policz heads sam, nie wierz ticketowi. Head = revision ktory nie jest niczyim down_revision. Skrypt python: zbierz wszystkie (revision, down_revision) ze WSZYSTKICH formatow (`revision: str = '...'` ORAZ `revision = "..."`), head = revision \ set(down_revisions).

**Why:** MON-73 Warstwa 1 - developer wzial down_revision z ticketu (`f1a2b3c4d5e6`), ale ten head byl nieaktualny (settlements `17420ab13509` i work_plan `c571fb82cd74` juz nad nim). Dodatkowo uzyl placeholder ID `a1b2c3d4e5f6` ktory juz istnial (time_tracking). Migracja byla nieuruchamialna -> blocker, 58/100 NEEDS WORK.

**How to apply:** W repo monolynx alembic miesza dwa formaty deklaracji rewizji (`revision: str = '...'` w starszych, `revision = "..."` w nowszych) - parser musi lapac oba. Placeholder ID typu a1b2c3.../f1a2b3... sa szczegolnie podatne na kolizje bo wygladaja jak fabrykowane.

Related: [[plugin_packaging]]
