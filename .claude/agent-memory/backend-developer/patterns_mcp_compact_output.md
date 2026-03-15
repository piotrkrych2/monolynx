---
name: MCP compact output pattern
description: MCP tools that return compact strings instead of list[dict] to save LLM tokens — pattern and existing helpers
type: project
---

Some MCP tools in `src/monolynx/mcp_server.py` return compact strings instead of `list[dict]` to reduce token usage.

**Existing helpers:**
- `_format_graph_dsl(data)` — converts graph nodes/edges dict to Arrow DSL text (line ~308)
- `_format_board(sprint, project_code, columns)` — Kanban board as text (line ~155)
- `_format_sprint_detail(sprint, project_code, tickets)` — sprint detail as text (line ~274)
- `_format_monitors_table(monitors_data)` — monitor list as table (line ~409)
- `_format_wiki_tree(pages_data)` — wiki page tree with indentation (added 2026-03-15, line ~3077)

**Wiki tree format (`_format_wiki_tree`):**
```
N pages

ID                                   | Title              | Updated
<36-char uuid>                       | Title              | YYYY-MM-DD
<36-char uuid>                       |   Child Title      | YYYY-MM-DD
```
- Indentation: 2 spaces per depth level, prepended to title
- Date: ISO date truncated to first 10 chars (`updated_at[:10]`)
- Empty wiki returns `"0 pages"` (no header)

**Why:** Compact strings use far fewer tokens than serialized dicts, especially for long lists. The LLM gets all necessary IDs for follow-up calls (get/update/delete) while the format remains human-readable.

**How to apply:** When adding new "list" MCP tools, consider returning a formatted string if the data is tabular or tree-structured. Add a `_format_<name>` helper function just before the `@mcp.tool()` decorator.
