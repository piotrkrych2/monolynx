---
name: RBAC data migration — dodawanie nowego modułu do permissions
description: Wzorzec jsonb_set do aktualizacji uprawnień ról systemowych przy dodawaniu nowego modułu
type: project
---

Przy dodawaniu nowego modułu do PERMISSION_MODULES, migracja Alembic musi zawierać data migration aktualizującą role systemowe w bazie.

Wzorzec (w `upgrade()`):
```python
op.execute("""
    UPDATE roles
    SET permissions = jsonb_set(permissions, '{<modul>}', '["read", "write", "delete"]'::jsonb)
    WHERE is_system = true AND name = 'Owner'
""")
op.execute("""
    UPDATE roles
    SET permissions = jsonb_set(permissions, '{<modul>}', '["read", "write"]'::jsonb)
    WHERE is_system = true AND name = 'Admin'
""")
op.execute("""
    UPDATE roles
    SET permissions = jsonb_set(permissions, '{<modul>}', '[]'::jsonb)
    WHERE is_system = true AND name = 'Member'
""")
```

W `downgrade()` przed drop_table:
```python
op.execute("UPDATE roles SET permissions = permissions - '<modul>'")
```

Referencja: `alembic/versions/f1a2b3c4d5e6_add_roles_table_and_rbac.py` — wzorzec stałych `_OWNER_PERMS`/`_ADMIN_PERMS`/`_MEMBER_PERMS`.

**Why:** Istniejące projekty mają już role w DB — constants.py sam w sobie nie aktualizuje istniejących rekordów.

**How to apply:** Każda migracja dodająca nowy moduł do PERMISSION_MODULES musi zawierać ten blok SQL.
