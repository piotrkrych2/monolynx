---
name: patterns-self-referential-m2m-viewonly
description: Self-referential many-to-many SQLAlchemy relationship (e.g. Ticket blocker/blocked_by) via secondary table with two opposite-direction relationships
metadata:
  type: reference
---

Pattern for a self-referential M2M on the same model (first used: MON-198, `Ticket.blocked_by` / `Ticket.blocking` via `ticket_dependencies`):

```python
blocked_by: Mapped[list[Ticket]] = relationship(
    "Ticket",
    secondary="ticket_dependencies",
    primaryjoin="Ticket.id == TicketDependency.ticket_id",
    secondaryjoin="Ticket.id == TicketDependency.blocker_id",
    viewonly=True,
    lazy="selectin",
    join_depth=1,
)
blocking: Mapped[list[Ticket]] = relationship(
    "Ticket",
    secondary="ticket_dependencies",
    primaryjoin="Ticket.id == TicketDependency.blocker_id",
    secondaryjoin="Ticket.id == TicketDependency.ticket_id",
    viewonly=True,
    lazy="selectin",
    join_depth=1,
)
```

- SQLAlchemy cannot auto-detect which FK column of the secondary table is "self" vs "target" for a self-referential M2M — `primaryjoin`/`secondaryjoin` are mandatory (unlike a normal M2M like `Ticket.labels`).
- **`join_depth` is mandatory too, not optional, for `lazy="selectin"` on a self-referential relationship.** Without it, the automatic (non-`.options()`) eager load has no way to bound recursion depth, so the loader disables itself even for the root object (`run_loader=False` in `PostLoader._setup_for_recursion`) and silently falls back to `lazy="select"` — which raises `MissingGreenlet` the moment the attribute is touched in an async session outside an explicit `.options(selectinload(...))` call on that query. Caught by code review on MON-198 (round 1, score 85) because the qa-tester's model-layer test loads via explicit `.options(selectinload(Ticket.blocked_by))`, which masked the bug — the gap only shows up on a plain `select(Ticket)` with no options, e.g. inside `services/ticket_dependencies.py` itself. `join_depth=1` is enough for a one-hop blocker/blocked relationship (no transitive traversal needed here).
- `viewonly=True` on both sides avoids the "relationship X will copy column Y ... conflicts with relationship(s) Z" SAWarning that two M2M relationships sharing the same secondary table would otherwise trigger — no `overlaps=` needed. Writes go through a dedicated service function operating on the association-object model directly (here `services/ticket_dependencies.set_blockers`), never through the relationship collections.
- Project has no `sqlalchemy2-stubs`/sqlalchemy mypy plugin (only `pydantic.mypy` — see `pyproject.toml` `[tool.mypy] plugins`), so the string args to `relationship()` are untyped from mypy's perspective; no import of the secondary/association model is needed in the file defining the relationship for typing purposes, only for the association model's own definition.
- The association-object model (`TicketDependency`) itself follows the plain `models/label.py::TicketLabel` composite-PK style: no synthetic `id`, both PK columns are `ForeignKey(..., ondelete="CASCADE")`, no explicit `UUID(as_uuid=True)` type (inferred from the FK target). Check the sibling M2M's association table for whether it indexes the second PK column before adding one yourself — `ticket_labels` (the closest precedent) does NOT index `label_id`, so `ticket_dependencies` matches by NOT indexing `blocker_id`.

See also [[patterns_partial_unique_index_no_project_scope]] for another model/migration precedent-following exercise in this codebase.
