---
name: Autogenerate noise — OAuth i wiki_embeddings
description: alembic autogenerate w tym projekcie zawsze dodaje niepotrzebne zmiany z tabel OAuth i wiki_embeddings — trzeba je ręcznie usunąć
type: feedback
---

Po każdym `alembic revision --autogenerate` autogenerate wykrywa fałszywe zmiany w:
- `oauth_access_tokens`, `oauth_authorization_codes`, `oauth_clients`, `oauth_refresh_tokens` — nullable i unique constraint
- `oauth_refresh_tokens.is_revoked` — nullable
- `project_members.role` — nullable
- `wiki_embeddings.created_at` — nullable i index HNSW

**Why:** Rozbieżność między modelem SQLAlchemy a stanem DB (prawdopodobnie historyczne migracje nie były idealnie zsynchronizowane z modelami). Autogenerate wychwytuje te różnice przy każdym uruchomieniu.

**How to apply:** Po wygenerowaniu migracji ZAWSZE przeczytaj plik i usuń wszystkie bloki dotyczące tabel OAuth, wiki_embeddings i project_members.role — o ile migracja ich nie dotyczy. Zostaw tylko faktycznie nowe tabele/kolumny z bieżącego zakresu pracy.
