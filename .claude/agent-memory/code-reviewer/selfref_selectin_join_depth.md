---
name: selfref-selectin-join-depth
description: lazy="selectin" na relacji self-referential (Ticket->Ticket) bez join_depth nie laduje niczego - fallback na lazy select, MissingGreenlet w async
metadata:
  type: project
---

`relationship(..., lazy="selectin")` na relacji self-referential BEZ `join_depth` nigdy nie laduje zachlannie z mapowania. SQLAlchemy 2.0.46 `orm/strategies.py` `PostLoader._setup_for_recursion` (ok. l.1343-1358): bez loadera w opcjach zapytania `effective_path_w_prop.contains_mapper(self.mapper)` jest True juz dla korzenia, wiec `run_loader=False` i atrybut spada na `lazy="select"`, a w sesji async dostep daje MissingGreenlet.

**Why:** MON-198 (Ticket.blocked_by / blocking). Testy przeszly, bo uzywaly jawnego `selectinload(...)`, a komentarz w tescie obchodzil MissingGreenlet. Researcher i developer zakladali, ze relacje "laduja sie przy kazdym Tickecie". Dostalo -15 za kryterium "bez N+1".

**How to apply:** przy kazdej relacji self-referential z `lazy="selectin"`/`"joined"` szukaj `join_depth`. Test dowodzacy kryterium ma czytac relacje po `select(Model)` BEZ `.options(...)`. Jawny `selectinload` w tescie maskuje wade. Zobacz [[review-method]].
