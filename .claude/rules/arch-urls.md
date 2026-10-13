---
paths:
  - "src/monolynx/main.py"
  - "src/monolynx/dashboard/**"
  - "src/monolynx/api/**"
  - "src/monolynx/blog.py"
  - "src/monolynx/blog_ai.py"
---

# URL structure (full list)

Detail moved out of CLAUDE.md, which keeps only the prefix map.

```
/auth/login, /auth/logout
/auth/accept-invite/{token}                    - set password from invitation
/dashboard/                                    - project list
/dashboard/create-project                      - new project form
/dashboard/profile/tokens                      - user API tokens list
/dashboard/profile/tokens/create               - generate new token (POST)
/dashboard/profile/tokens/{id}/revoke          - revoke token (POST)
/dashboard/profile/mcp-guide                   - MCP setup instructions
/dashboard/users                               - user list (superuser only)
/dashboard/users/create                        - invite new user (superuser only)
/dashboard/users/{id}/resend-invite            - resend invitation email (POST)
/dashboard/users/{id}/blog-permission          - grant or revoke the blog permission, form field enabled=true|false (POST, superuser only, 303; MON-270)
/dashboard/{slug}/500ki/issues                 - error issue list
/dashboard/{slug}/500ki/issues/{id}            - error issue detail
/dashboard/{slug}/500ki/setup-guide            - SDK installation instructions
/dashboard/{slug}/scrum/backlog                - ticket list
/dashboard/{slug}/scrum/board                  - Kanban board (active sprint)
/dashboard/{slug}/scrum/tickets/create         - new ticket
/dashboard/{slug}/scrum/tickets/{id}           - ticket detail
/dashboard/{slug}/scrum/tickets/{id}/edit      - edit ticket
/dashboard/{slug}/scrum/tickets/{id}/delete    - delete ticket (POST)
/dashboard/{slug}/scrum/tickets/{id}/status    - HTMX status update (PATCH)
/dashboard/{slug}/scrum/tickets/{id}/comments  - add comment (POST)
/dashboard/{slug}/scrum/sprints                - sprint list + create form
/dashboard/{slug}/scrum/sprints/{id}/start     - start sprint (POST)
/dashboard/{slug}/scrum/sprints/{id}/complete  - complete sprint (POST)
/dashboard/{slug}/monitoring/                   - monitor list
/dashboard/{slug}/monitoring/create             - create monitor form
/dashboard/{slug}/monitoring/{id}               - monitor detail with check history + pagination
/dashboard/{slug}/monitoring/{id}/toggle        - enable/disable monitor (POST)
/dashboard/{slug}/monitoring/{id}/delete        - delete monitor (POST)
/dashboard/{slug}/settings                     - project settings + members
/dashboard/{slug}/settings/delete              - soft delete project (POST)
/dashboard/{slug}/settings/members/add         - add member (POST)
/dashboard/{slug}/settings/members/{id}/remove - remove member (POST)
/dashboard/{slug}/settings/members/{id}/role   - change role (POST)
/api/v1/events                                 - ingest events (POST, API key auth)
/api/v1/issues/{id}/status                     - update issue status (PATCH; requires `X-Monolynx-Key` of the issue's project or a session with `500ki:write`)
/api/v1/health                                 - health check
/api/v2/...                                    - REST API v2 for the CLI and integrations (Bearer token, errors as application/problem+json); routes listed below
/dashboard/{slug}/connections/                   - graph visualization (Cytoscape.js)
/dashboard/{slug}/connections/nodes              - node list with type/search filtering
/dashboard/{slug}/connections/nodes/create       - create node form (GET) + create (POST)
/dashboard/{slug}/connections/nodes/{id}/delete  - delete node (POST)
/dashboard/{slug}/connections/edges/create       - create edge form (GET) + create (POST)
/dashboard/{slug}/connections/api/graph          - graph data JSON API (for Cytoscape.js)
/dashboard/{slug}/wiki/                         - wiki page tree
/dashboard/{slug}/wiki/search?q=               - semantic wiki search (RAG)
/dashboard/{slug}/wiki/pages/create            - new wiki page
/dashboard/{slug}/wiki/pages/{id}              - wiki page detail (rendered markdown)
/dashboard/{slug}/wiki/pages/{id}/edit         - edit wiki page (EasyMDE editor)
/dashboard/{slug}/wiki/pages/{id}/create       - new child page
/dashboard/{slug}/wiki/pages/{id}/delete       - delete page + children (POST); a public page or one with a public descendant also needs blog:write, else 403 (MON-272)
/dashboard/{slug}/wiki/pages/{id}/blog-preview - draft preview of a post in the public blog look (GET, session + wiki:read, works for a private page, X-Robots-Tag: noindex, writes nothing; MON-264)
/dashboard/{slug}/wiki/upload?page_id=         - image upload for EasyMDE (POST); optional page_id stores the file as an attachment of that page and returns its public /blog/... address for a public page, the dashboard attachment address for a draft (MON-264); page_id of a public page also needs blog:write, else 403 (MON-272)
/dashboard/{slug}/wiki/attachments/{filename}  - serve wiki attachments
/blog/?tag=&lang=&ui=&page=                    - public blog: post list by publication date, 20 per page (no auth); ?tag= and ?lang= (pl/en) are filters kept by pagination, ?ui= (pl/en) is the interface language only (without it: a valid ?lang=, then en), invalid tag = empty list; ETag + 304 (MON-260, MON-271)
/blog/index.md                                 - public post list as markdown (GET, ?lang= and ?tag= filters, no pagination, ETag + 304; MON-258)
/blog/feed.xml, /blog/rss.xml, /blog/feed.json - Atom, RSS and JSON Feed of the 20 newest public posts with full content (GET, ?lang=, ETag + 304; MON-259); post content read through an in-process cache keyed by page and updated_at (MON-272)
/llms-full.txt                                 - all public posts as one markdown document, size-capped, cached in process memory under a metadata fingerprint (GET, text/plain; MON-259, MON-271); /llms.txt gets a live `## Blog` section
/blog-authoring.md                             - blog authoring guide for any model as markdown: block catalog, lint rules, markdown twin, connector operations (GET, no auth; MON-265)
/blog/{slug}.md                                - markdown twin of a post with YAML frontmatter (GET, ETag + 304); a previous slug answers 301 to the current .md; private, unknown or storage error is 404
/blog/{slug}                                   - public post by BlogPost.slug; a previous slug answers 301 to the current one; private or unknown is 404; Accept: text/markdown returns the markdown twin (Vary: Accept); ?lang= (pl/en) sets the interface language, default is the post's lang; HTML with ETag + 304 (MON-260)
/blog/{slug}/attachments/{attachment_id}/{filename}  - public post attachment (a previous slug of a public post answers 301 to the current address, nosniff, inline only for PNG/JPEG/GIF/WebP)
/dashboard/reports                              - global work reports (cross-project)
/dashboard/reports/pdf                          - PDF export of work reports
/dashboard/plan/                                : Work Plan view (Gantt or calendar mode)
/dashboard/plan/api/data                        : JSON entries in date range (GET, max 90 days)
/dashboard/plan/api/tickets/search              : ticket autocomplete (GET, limit 20)
/dashboard/plan/entries                         : create entry (POST)
/dashboard/plan/entries/{entry_id}              : update entry (PATCH)
/dashboard/plan/entries/{entry_id}              : delete entry (DELETE, owner only)
/dashboard/{slug}/pipelines/                     - pipeline list (HTML, HTMX polling)
/dashboard/{slug}/pipelines/api/list             - pipeline list JSON (status/type filter, page)
/dashboard/{slug}/pipelines/api/{pipeline_id}    - full pipeline tree JSON (steps + jobs)
/dashboard/{slug}/pipelines/partial/list         - HTML partial of list rows (15s polling)
/dashboard/{slug}/pipelines/{pipeline_id}        - pipeline detail (step columns + job cards)
/dashboard/{slug}/pipelines/{pipeline_id}/partial/tree  - HTML partial of step/job tree (15s polling)
/dashboard/{slug}/pipelines/{pipeline_id}/jobs/{job_id} - job detail (metadata + markdown log from wiki)
/api/v2, /api/v2/                              - API v2 placeholder root, no trailing-slash redirect (no auth, {"api": "v2"})
/api/v2/docs                                   - API v2 Swagger UI (v2 routes only)
/api/v2/openapi.json                           - API v2 OpenAPI schema (v2 routes only)
/api/v2/me                                     - authenticated user's id + email (Bearer, MON-125)
/api/v2/projects                               - list (GET, paginated) / create (POST) projects
/api/v2/projects/{slug}                        - get (GET) / update (PATCH) / soft delete (DELETE) project
/api/v2/projects/{slug}/members                - list (GET) / invite (POST, sends invitation email)
/api/v2/projects/{slug}/members/{member_id}    - remove member (DELETE, member_id = user_id from list)
/api/v2/projects/{slug}/summary                - project summary (GET)
/api/v2/projects/{slug}/labels                 - list (GET) / create (POST) labels
/api/v2/projects/{slug}/tickets                - list (GET, per_page=20) / create (POST) tickets
/api/v2/projects/{slug}/tickets/search         - search tickets (GET, per_page=20)
/api/v2/projects/{slug}/tickets/bulk           - bulk update tickets (PATCH, max 100 ids)
/api/v2/projects/{slug}/tickets/{ticket_id}    - get (GET) / update (PATCH) / delete (DELETE); UUID or key
/api/v2/projects/{slug}/tickets/{ticket_id}/comments  - list (GET) / add (POST) comments
/api/v2/projects/{slug}/tickets/{ticket_id}/acceptance-criteria                 - list (GET) / add (POST)
/api/v2/projects/{slug}/tickets/{ticket_id}/acceptance-criteria/{criterion_id}  - update (PATCH) / delete (DELETE)
/api/v2/projects/{slug}/board                  - Kanban board of the active sprint (GET, not paginated)
/api/v2/projects/{slug}/sprints                - list (GET) / create (POST) sprints
/api/v2/projects/{slug}/sprints/{sprint_id}    - get (GET) / update (PATCH) sprint
/api/v2/projects/{slug}/sprints/{sprint_id}/start     - start sprint (POST)
/api/v2/projects/{slug}/sprints/{sprint_id}/complete  - complete sprint (POST)
/api/v2/projects/{slug}/burndown?sprint_id=    - burndown data (GET, no sprint_id = active sprint)
/api/v2/projects/{slug}/wiki/pages             - flat page list (GET) / create page (POST)
/api/v2/projects/{slug}/wiki/pages/{page_id}   - get (GET) / update (PATCH) / delete with children (DELETE; wiki:delete, plus blog:write for a public page or one with a public descendant, else 403, MON-272)
/api/v2/projects/{slug}/wiki/search?query=&limit=  - semantic wiki search (GET, not paginated, limit default 10)
/api/v2/projects/{slug}/wiki/config            - get (GET) / set (PATCH) LLM Wiki config
/api/v2/projects/{slug}/blog/posts?status=     - blog posts with metadata, drafts included (GET, paginated, status published|draft; needs wiki:read)
/api/v2/projects/{slug}/blog/posts/{page_id}   - get post with metadata and markdown content (GET, wiki:read) / set post metadata (PATCH, wiki:write + blog:write); 404 for a page without a blog post row
/api/v2/projects/{slug}/blog/posts/{page_id}/lint  - lint a post for AI: score 0-100 and issues {rule, level, line, message} (GET, wiki:read; MON-262); 200 also when the post has lint errors, 503 on a storage error
/api/v2/projects/{slug}/blog/posts/{page_id}/publish    - publish a post behind the lint gate (POST, wiki:write + blog:write; optional body {force}; MON-263); 422 on lint errors without force and on a slug collision, 503 on a storage error
/api/v2/projects/{slug}/blog/posts/{page_id}/unpublish  - unpublish a post: /blog/{slug} and its .md answer 404, date and slug stay (POST, wiki:write + blog:write, no body; MON-263)
/api/v2/projects/{slug}/graph/nodes?type=&search=&limit=  - graph node list (GET, bare list, limit 1..1000, default 100; needs connections:read)
/api/v2/projects/{slug}/graph/nodes/{node_id}  - node with neighborhood (GET, depth >= 1 capped at 5 / relation_types / node_types; node_id may contain "/"; 422 out of range)
/api/v2/projects/{slug}/graph/query?node_type=&search=&limit=  - graph or subgraph {nodes, edges} (GET, limit 1..1000, default 200; 422 out of range)
/api/v2/projects/{slug}/graph/path?source_id=&target_id=  - shortest path {nodes, edges} (GET, both params required)
/api/v2/projects/{slug}/graph/stats            - node and edge counts, total and per type (GET); graph routes are read-only, 503 when the graph DB is off or down
/api/v2/{unmatched}                            - catch-all, GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS -> 404 problem+json
/mcp                                           - MCP server (Bearer token auth)
```
