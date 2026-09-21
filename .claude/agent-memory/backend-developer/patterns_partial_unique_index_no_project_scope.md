---
name: patterns-partial-unique-index-no-project-scope
description: Monolynx models scope slugs per-project (uq_wiki_page_project_slug), but a public-facing feature needing a global unique slug across projects needs its own separate partial unique index, not a reuse of the per-project uniqueness helper
metadata:
  type: reference
---

`WikiPage.slug` is unique per-project (`UniqueConstraint("project_id", "slug", ...)`),
enforced proactively in `services/wiki.py::_ensure_unique_slug` (appends `-1`, `-2`, ... on
collision) before insert. When a feature needs a slug that is unique **globally** (MON-201:
`is_public=True` pages are served at `/blog/{slug}` with no `project_slug` in the URL), that
per-project helper does NOT catch a cross-project collision — you need a second, separate
partial unique index scoped to the condition, e.g.:

```python
# model __table_args__
Index("uq_wiki_public_slug", "slug", unique=True, postgresql_where=text("is_public"))
```

```python
# alembic migration (write by hand, not autogenerate)
op.create_index("uq_wiki_public_slug", "wiki_pages", ["slug"], unique=True,
                 postgresql_where=sa.text("is_public"))
```

Since the proactive per-project check can't see this constraint, the service layer must
catch the resulting `IntegrityError` on `db.commit()` and re-raise `ValueError` with a
readable Polish message — never let it surface as a 500. Applies to both the create path
and any update path that can flip the flag to `True` on an existing row.

Finding the current alembic head without running alembic/Docker: for every file in
`alembic/versions/`, extract `revision` and `down_revision`; the head is the one `revision`
value that never appears as anyone's `down_revision`. Watch for multi-head merges
(`down_revision: str | None = ('rev_a', 'rev_b')` tuple syntax) — those are legitimate,
not a script bug.

See [[patterns_nh3_sanitization]] for the sibling MON-201 pattern (public markdown
sanitization for the same feature).
