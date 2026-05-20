---
name: project-plugin-vs-install-skills
description: Dwie ścieżki dystrybucji skilli Monolynx (plugin Claude Code vs install_monolynx_skills) i ich relacja
metadata:
  type: project
---

Monolynx ma DWIE równoległe ścieżki dystrybucji skilli/agentów, obie świadomie utrzymywane (MON-72):

1. **Plugin Claude Code** (`plugin/`) — preferowana ścieżka dla CLI. Bundluje skille (`/monolynx:*`), 7 agentów i zdalny dostęp MCP. Marketplace manifest w root (`.claude-plugin/marketplace.json`, source `./plugin`), manifest pluginu w `plugin/.claude-plugin/plugin.json`. `.mcp.json` pluginu deklaruje tylko dostęp HTTP+Bearer do istniejącego serwera, NIE kopiuje serwera.

2. **`install_monolynx_skills`** (narzędzie MCP w `mcp_server.py`) — fallback/manualna ścieżka dla claude.ai web i środowisk bez pluginów. Czyta skille z `_STATIC_SKILLS_DIR = src/monolynx/static/skills/`; jest też `src/monolynx/static/starter-pack/`. Zwraca treść SKILL.md z podmienionymi placeholderami `<PROJECT-SLUG>`/`<PROJECT-ID>` do ręcznego zapisu w `.claude/skills/`.

**Why:** decyzja świadoma — nie usuwać install_monolynx_skills ani kopii w static, bo nie wszyscy mają dostęp do mechanizmu pluginów.
**How to apply:** dokumentując plugin, zawsze opisuj obie ścieżki i podkreślaj że `mcp_server.py` pozostaje bez zmian funkcjonalnych (plugin tylko deklaruje dostęp). Slug resolution w skillach: `MONOLYNX_PROJECT_SLUG` z `.env` -> `user_config.project_slug` -> `"monolynx"`.
