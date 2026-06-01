---
paths:
  - "**/monolynx*/SKILL.md"
---

# SKILL.md - project_slug placeholder

W plikach `plugin/skills/*/SKILL.md` i `src/monolynx/static/skills/*/SKILL.md` **zawsze** używaj `<PROJECT_SLUG>` jako wartości `project_slug` w wywołaniach MCP.

Nigdy nie wpisuj hardkodowanej wartości (np. `'monolynx'`).

## Przykład poprawny

```
mcp__monolynx__get_wiki_page(project_slug='<PROJECT_SLUG>', page_id='...')
mcp__monolynx__search_wiki(project_slug='<PROJECT_SLUG>', query='...')
```

## Przykład błędny

```
mcp__monolynx__get_wiki_page(project_slug='monolynx', page_id='...')
```

## Regresja

MON-77 - technical-writer użył `project_slug='monolynx'` w `src/monolynx/static/skills/monolynx-work/SKILL.md`, kopiując styl istniejących linii w pliku zamiast użyć placeholdera.
