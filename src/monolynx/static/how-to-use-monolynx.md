# How to use Monolynx (a guide for AI assistants)

> You are an AI assistant (Claude, ChatGPT, Gemini, or any MCP-capable agent). This page tells you how to drive software development with Monolynx. Read it fully before you start. If a human asks you to "work in Monolynx", "use Monolynx", or connects you to a Monolynx MCP server, follow the workflow below.

Monolynx is an AI-first project platform. It exposes every module (Scrum, Wiki with RAG search, error tracking, monitoring, code dependency graph, pipelines, and more) over the Model Context Protocol (MCP), so you can operate it as a full member of the team using natural language. The point of Monolynx is not a dashboard for humans - it is a shared, persistent memory and task system that you and the human edit together.

---

## Step 1: Connect (this unlocks the full power)

**Full power needs two things together: the marketplace plugin (for the skills and agents) AND the MCP connection (for the tools). They are not alternatives.** The plugin is what delivers the `/monolynx:*` skills, the role goodies, and the wired-up MCP server in one install. A bare MCP connection gives you the tools but none of the skills.

### Full setup - Claude Code plugin from the marketplace (this is the real thing)

The plugin bundles the skills, the agents scaffolding, and the remote MCP server. Install it and set your token:

```bash
/plugin marketplace add https://gitlab.com/piotrkrych/monolynx.git
/plugin install monolynx@monolynx
```

In the plugin config set:

- `mcp_token` - your Monolynx API token (format `osk_...`), generated in the dashboard under "API Tokens". This is the MCP connection.
- `mcp_endpoint` - your instance MCP URL (defaults to `https://monolynx.com/mcp`).
- `project_slug` - optional default project (fallback only, see below).

**Tell the skills which project they operate on.** The skills resolve the target project slug in a fixed order (highest priority first):

1. `MONOLYNX_PROJECT_SLUG` from the `.env` file of the repository you are working in (per-repo setting, highest priority). Set it there so the same plugin works across many projects. In Claude Code you can also expose it through the `env` field in `.claude/settings.json`.
2. `user_config.project_slug` from the plugin config (a global fallback for your most-used project).
3. `"monolynx"` as the final default.

So the full first-time sequence a developer runs is:

1. Add the marketplace: `/plugin marketplace add https://gitlab.com/piotrkrych/monolynx.git`
2. Install the plugin: `/plugin install monolynx@monolynx`
3. Set `mcp_token` and `mcp_endpoint` in the plugin config.
4. Set `MONOLYNX_PROJECT_SLUG` in the repo's `.env` (or the plugin `project_slug`).
5. Start using the `/monolynx:*` skills.

After this you have **marketplace + MCP**: the `/monolynx:*` skills (`monolynx:ticket-create`, `monolynx:work`, ...) plus all 117 MCP tools. This is full power.

If you are an agent asked to set this up inside a user's repository, follow the imperative checklist at <https://monolynx.com/agent-bootstrap.md> - it walks you through SETUP, NOTIFY, and COLLABORATE phases, including verifying the connection with `list_projects`.

### Fallback - chat clients (Claude.ai, ChatGPT): MCP connector only

Claude.ai and ChatGPT cannot install Claude Code plugins, so they cannot get the skills. They can only add the raw HTTPS MCP connector:

1. Open your client's Settings, then Connectors.
2. Click "Add custom connector".
3. Connector URL: `https://your-instance/mcp` (replace `your-instance` with the actual host, e.g. `https://monolynx.com/mcp`).
4. Authorize with your Monolynx Bearer token (`osk_...`).

This gives you all 117 MCP tools (read/write tickets, sprints, wiki, dependency graph, pipelines) but **no `/monolynx:*` skills**. In this mode you must replicate the skill workflow manually - call the MCP tools in the same order the skills would (see Steps 3 and 4). It works, but the orchestrated, repeatable flow lives in the plugin. For serious development use Claude Code with the plugin.

---

## Step 2: The wiki is the most important thing - always

**The project wiki is the single source of truth. Treat it as the project's long-term memory, not as optional documentation.**

Before you do anything substantive:

1. **Search the wiki first.** Call `search_wiki` (semantic RAG search) for the topic you are about to touch. Read the relevant pages with `get_wiki_page` before writing code or creating tickets. The wiki tells you the architecture, conventions, and prior decisions.
2. **Write knowledge back.** When your work produces a decision, a new convention, or a non-obvious fact, record it in the wiki (`create_wiki_page` / `update_wiki_page`). The wiki grows with every task - it is a curated artifact, not a dump.
3. **Look for a `constitution` page and any spec pages.** If the project has a wiki page with slug `constitution`, it is project-level law - load it. If a ticket references a spec page, that spec is your primary context.

A project where the wiki is rich and current lets any assistant pick up any task with no human hand-holding. That is the whole goal. Do not skip the wiki to "save time" - skipping it is how you produce work that contradicts existing decisions.

### Bootstrap the LLM Wiki method once, at the start

Before you lean on the wiki heavily, enable the **LLM Wiki method** for the project with **`monolynx:wiki-init`** (idempotent, safe to re-run). It flips the `wiki_llm_enabled` flag and creates the system pages: `wiki-schema` (the editable rulebook), `wiki-index` (the catalog) and `wiki-log` (the running journal). With the method on, the other wiki skills do real work: `monolynx:wiki-ingest` integrates a new source (file, URL or pasted text) into linked pages, `monolynx:wiki-sync-merge` runs the post-merge INGEST after tickets/PRs land on main, and `monolynx:wiki-lint` audits the wiki for orphans, dead links, contradictions and gaps. Run `wiki-init` first; without it those skills return a clear no-op.

---

## Step 3: Create tickets through the skill, never ad-hoc

Do **not** call `create_ticket` raw with a one-line description. Use the **`monolynx:ticket-create`** skill (Claude Code), or replicate its steps manually in a chat client.

Why the skill matters:

- It gathers context from **wiki + dependency graph + actual code + existing tickets** before writing anything.
- It produces a ticket in a consistent form: **Goal, Context, Scope (with concrete files/endpoints/models), Dependencies, Acceptance criteria**.
- Acceptance criteria become real checkboxes on the ticket, so the work has a verifiable definition of done.
- It detects duplicates and links dependencies.

A ticket written this way can be picked up and finished by an AI agent **without any further questions**. A one-line ticket cannot. If you are in a chat client without the skill, do the same manually: `search_wiki`, `query_graph`, read the code, `search_tickets` for duplicates, then `create_ticket` with a full description and `acceptance_criteria`.

---

## Step 4: Do the work through `monolynx:work`

To execute a ticket, use **`monolynx:work <ticket-id>`** (Claude Code). For small tickets (< 8 story points - hotfixes, cleanups, verifying finished work) use **`monolynx:work-simple`**.

`monolynx:work` runs a full Team Manager flow:

1. Resolves the project slug and validates you are on the right git branch.
2. Opens a pipeline (observability) of type `ticket_work` with steps research -> coding -> wrap-up.
3. Runs a **Researcher** that reads the ticket, the spec page, the constitution, the wiki, the code, and the dependency graph, then writes a report.
4. Selects a minimal team of project agents plus a **mandatory code-reviewer** (quality gate), and runs them in parallel.
5. Each agent reports back: a comment on the ticket, logged time, and acceptance criteria checked off.
6. Verifies all acceptance criteria, finishes the pipeline, and moves the ticket to `in_review`.

In a chat client without skills, you can still call the underlying MCP tools in the same order: `get_ticket`, `update_ticket(status="in_progress")`, `search_wiki`/`query_graph`, do the work, `add_comment`, `log_time`, `update_acceptance_criterion`, `update_ticket(status="in_review")`.

**The two skills that matter most are `monolynx:ticket-create` and `monolynx:work`.** Everything else (`search`, `ticket-review`, `wiki-ingest`, `wiki-lint`, `sprint-end`) supports that core loop.

---

## Step 5: Agents are per-project - they are NOT part of Monolynx

Monolynx does not ship the developer agents. The skills **discover whatever agents exist in the current project** and pick a minimal team for each ticket. You define those agents in your own repo, tuned to your stack and conventions:

- **Claude Code**: agent definitions live in `.claude/agents/*.md`. Create them with the `/agents` command. Typical set: a backend dev, a frontend dev, a database specialist, a QA/tester, and a code-reviewer (the reviewer is always required).
- **Codex**: there is no per-agent subagent directory; describe your roles and conventions in `AGENTS.md` at the repo root. The skill reads `AGENTS.md` to understand the available roles and the project's stack.

The skills prefer the project's own agents as the source of truth for stack and conventions. If the project has no matching agent definitions, create them first (or let the skill fall back to generic roles). Rich, project-specific agents plus a rich wiki are what make the workflow autonomous.

---

## The closed loop (why this works)

```
wiki (source of truth)  ->  ticket-create (context-rich ticket)  ->  work (team + reviewer)  ->  pipeline (observability)  ->  wiki (knowledge written back)
```

Every task starts from the wiki and ends by enriching it. Tickets carry verifiable acceptance criteria. Work is done by project-defined agents under a mandatory reviewer. Pipelines record what happened. The next assistant - you, tomorrow, or a different model - inherits all of it. That is how Monolynx lets an AI assistant act as a real team member instead of a one-off code generator.

---

## Quick reference

| You want to... | Use |
|---|---|
| Get full power (skills + tools) | Marketplace plugin in Claude Code: `/plugin marketplace add ...` then `/plugin install monolynx@monolynx` + set `mcp_token`. This is marketplace + MCP together. |
| Connect a chat client (Claude.ai / ChatGPT) | MCP connector only: `https://your-instance/mcp` + Bearer token. Tools but no skills - replicate the flow manually. |
| Understand the project | `search_wiki`, `get_wiki_page` (read the `constitution` page if present) |
| Add a task | `monolynx:ticket-create` (or replicate its steps with MCP tools) |
| Review a ticket before starting | `monolynx:ticket-review` |
| Do a task | `monolynx:work <ticket-id>` (small, < 8 SP: `monolynx:work-simple`) |
| Close a sprint | `monolynx:sprint-end` (INGEST work logs, LINT, close the sprint) |
| Define your team | `.claude/agents/*.md` (Claude Code) or `AGENTS.md` (Codex) - per project, not shipped by Monolynx |
| Enable the LLM Wiki method | `monolynx:wiki-init` (run once per project) |
| Grow the wiki | `monolynx:wiki-ingest`, `monolynx:wiki-sync-merge` (post-merge) |
| Keep the wiki healthy | `monolynx:wiki-lint` |
| Generate a code-graph CI script | `monolynx:create-graph-ci-script` |
| See how the skills work | `monolynx:help` |

Full set of 12 skills: `work`, `work-simple`, `ticket-create`, `ticket-review`, `sprint-end`, `search`, `wiki-init`, `wiki-ingest`, `wiki-lint`, `wiki-sync-merge`, `help`, `create-graph-ci-script`.

Full module and tool reference: <https://monolynx.com/llms.txt>

## Self-hosting Monolynx itself

Monolynx is open source: <https://gitlab.com/piotrkrych/monolynx>. To run your own instance: clone the repository, copy `.env.example` to `.env`, then `make dev` (Docker Compose with PostgreSQL + pgvector, Neo4j, MinIO, and the FastAPI app on port 8000), `make migrate`, and `make createsuperuser`. Production uses `docker-compose.prod.yml` with a separate monitor worker. Optional integrations degrade gracefully (`OPENAI_API_KEY` for RAG search, `ENABLE_GRAPH_DB` for the dependency graph, `SMTP_HOST` for email). See the repository README for details. Once your instance is up, point `mcp_endpoint` at `https://your-instance/mcp` and everything above applies unchanged.
