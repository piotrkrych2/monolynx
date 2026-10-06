# How to use Monolynx (a guide for AI assistants)

> You are an AI assistant (Claude, ChatGPT, Gemini, or any MCP-capable agent). This page tells you how to drive software development with Monolynx. Read it fully before you start. If a human asks you to "work in Monolynx", "use Monolynx", or connects you to a Monolynx MCP server, follow the workflow below.

Monolynx is an AI-first project platform. It exposes every module (Scrum, Wiki with RAG search, error tracking, monitoring, code dependency graph, pipelines, and more) over the Model Context Protocol (MCP), so you can operate it as a full member of the team using natural language. The point of Monolynx is not a dashboard for humans - it is a shared, persistent memory and task system that you and the human edit together.

---

## Step 1: Connect (this unlocks the full power)

**Full power needs two things together: the marketplace plugin (for the skills and agents) AND the MCP connection (for the tools). They are not alternatives.** The plugin is what delivers the `/monolynx:*` skills, the role goodies, and the wired-up MCP server in one install. A bare MCP connection gives you the tools but none of the skills.

### Full setup - Claude Code plugin from the marketplace (this is the real thing)

Plugin dostarcza skille, definicje agentów Claude Code i połączenie ze zdalnym MCP. Zainstaluj go i zaloguj się przez OAuth:

```bash
/plugin marketplace add https://gitlab.com/piotrkrych/monolynx.git
/plugin install monolynx@monolynx
```

W Claude Code otwórz `/mcp` i dokończ logowanie. Token API `osk_...` jest alternatywą przy ręcznej konfiguracji. Plugin wskazuje `https://monolynx.com/mcp`; własna instancja wymaga osobnego połączenia MCP.

**Tell the skills which project they operate on.** The skills resolve the target project slug in a fixed order (highest priority first):

1. Slug podany jawnie przez użytkownika.
2. `MONOLYNX_PROJECT_SLUG` ze środowiska procesu klienta. Claude Code: `env` w `.claude/settings.json`; Codex: `[shell_environment_policy.set]` w `.codex/config.toml`.
3. Jeśli sluga brakuje, zapytaj użytkownika. Sam `.env` nie eksportuje zmiennych do klienta; nie zgaduj projektu.

So the full first-time sequence a developer runs is:

1. Add the marketplace: `/plugin marketplace add https://gitlab.com/piotrkrych/monolynx.git`
2. Install the plugin: `/plugin install monolynx@monolynx`
3. Zaloguj się do MCP przez OAuth.
4. Ustaw `MONOLYNX_PROJECT_SLUG` w konfiguracji środowiska klienta opisanej powyżej.
5. Start using the `/monolynx:*` skills.

After this you have **marketplace + MCP**: the `/monolynx:*` skills (`monolynx:ticket-create`, `monolynx:work`, ...) plus all 122 MCP tools. This is full power.

If you are an agent asked to set this up inside a user's repository, follow the imperative checklist at <https://monolynx.com/agent-bootstrap.md> - it walks you through SETUP, NOTIFY, and COLLABORATE phases, including verifying the connection with `list_projects`.

### Codex: pełny plugin

Z katalogu sklonowanego repozytorium uruchom:

```bash
codex plugin marketplace add .
codex plugin add monolynx@monolynx
```

Codex czyta `plugin/.codex-plugin/plugin.json`, Claude Code swój manifest, a oba klienty współdzielą `plugin/skills`, marketplace i MCP. Rozpocznij nową sesję i wybierz `$monolynx:next`. Agentów i zaufania do hooków nie należy utożsamiać z samym odczytem skilli. Dyspozytor `sprint-run` nadal wymaga CLI Claude Code.

### Pojedyncze skille przez MCP: Claude Code, Codex i Cursor

Instalator `install_monolynx_skills` obsługuje trzy katalogi docelowe:

| `target` | Directory | Runtime |
|---|---|---|
| `claude` (default) | `.claude/skills/` | Claude Code |
| `codex` | `.agents/skills/` | Codex CLI |
| `cursor` | `.cursor/skills/` | Cursor |

```
install_monolynx_skills(project_slug="my-project", target="codex")
install_monolynx_skills(project_slug="my-project", target="codex", skill_names=["monolynx-work"])
```

Pierwsze wywołanie zwraca katalog. Drugie zwraca `skills: [{name, files: [{relative_path, content}]}]`. Zapisz wszystkie pliki, zachowując podkatalogi `scripts/` i `references/` oraz pliki pomocnicze. Nie usuwaj wcześniejszych instalacji ani konfiguracji użytkownika. Standalone ma nazwę `monolynx-work`, plugin namespace `monolynx:work`.

Bez pełnego pluginu skonfiguruj MCP osobno: w Codex `codex mcp add monolynx --url https://monolynx.com/mcp`, następnie `codex mcp login monolynx`; w Claude Code `claude mcp add --transport http monolynx https://monolynx.com/mcp` i logowanie przez `/mcp`. Cursor zachowuje `.cursor/mcp.json`. Role Codex pochodzą z dostępnych agentów i `AGENTS.md` projektu.

Slug podany przez użytkownika ma pierwszeństwo przed `MONOLYNX_PROJECT_SLUG`. W Codex ustaw tę zmienną w `[shell_environment_policy.set]` projektowego `.codex/config.toml`, w Claude Code w `env` pliku `.claude/settings.json`. Brak sluga wymaga pytania; nie przyjmuj domyślnego `monolynx`.

### Fallback - chat clients (Claude.ai, ChatGPT): MCP connector only

Claude.ai and ChatGPT cannot install Claude Code plugins, so they cannot get the skills. They can only add the raw HTTPS MCP connector:

1. Open your client's Settings, then Connectors.
2. Click "Add custom connector".
3. Connector URL: `https://your-instance/mcp` (replace `your-instance` with the actual host, e.g. `https://monolynx.com/mcp`).
4. Authorize with your Monolynx Bearer token (`osk_...`).

This gives you all 122 MCP tools (read/write tickets, sprints, wiki, dependency graph, pipelines) but **no `/monolynx:*` skills**. In this mode you must replicate the skill workflow manually - call the MCP tools in the same order the skills would (see Steps 3 and 4). It works, but the orchestrated, repeatable flow lives in the plugin. For serious development use Claude Code with the plugin.

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

To execute a ticket, use **`monolynx:work <ticket-id>`** (Claude Code). For small tickets (up to 3 story points - hotfixes, cleanups, verifying finished work) use **`monolynx:work-simple`**, which runs one developer plus the reviewer instead of a full team.

`monolynx:work` runs a full Team Manager flow:

1. Resolves the project slug and validates the git branch (working on `main`/`master` always asks first).
2. Opens a pipeline (observability) of type `ticket_work` with steps research -> coding -> wrap-up.
3. Runs a **Researcher** that reads the ticket, the spec page, the constitution, the `toolchain` page, the wiki, the code, and the dependency graph, then writes a report.
4. Proposes an **autonomy contract** in one question: what "done" looks like, which files stay untouched, and where the agent stops (a migration, a new dependency, a change outside the ticket scope). ENTER accepts it; the contract and the git baseline are written to the ticket as a plan comment, so a later `monolynx:resume` can pick the work up.
5. Selects a minimal team of project agents plus a **mandatory code-reviewer** (quality gate) and assigns each agent **specific files**. Agents whose file sets overlap run sequentially; the rest run in parallel.
6. **Defect mode:** for a bug ticket the reproducing test is written first and must be observed red before the fix goes in (the skill stashes the fix, runs the test, restores it).
7. The reviewer scores each agent against a fixed rubric (hard point deductions, pass mark 82). Below the mark, that agent iterates - up to 3 times.
8. **Reconciliation:** every acceptance criterion is matched against what was actually delivered; anything unmatched is listed as "to clarify" instead of being silently ticked.
9. **Runs lint and tests before closing.** Red tests block the move to `in_review`.
10. Each agent reports its own working time; comments, `log_time` and acceptance criteria are written back to the ticket.
11. Finishes the pipeline and moves the ticket to `in_review`.

Work that is not a ticket (configuration, an investigation, a small change) starts with **`monolynx:brief <what you want>`**: one question that pins down "done", what not to touch and where to stop, then ENTER and the work starts in the same turn. The plugin's `brief_guard` hook runs it for you when the first prompt of a session is a bare imperative such as "fix the build" (`MONOLYNX_BRIEF_GUARD=auto|block|off`). After `/clear`, a context compaction or the next day, **`monolynx:resume <ticket-id>`** rebuilds the state from the ticket comments, pipeline, acceptance criteria and git, and prints where you are, what is left and the next step.

Lint and test commands come from the project's `toolchain` wiki page - create it once with **`monolynx:project-toolchain`**, which detects the stack (Makefile, Python, Node, Rust, Go, PHP, Ruby, Java) and asks you to confirm. Without that page the skills ask whether to continue unverified. The same page carries an optional `## Mutacje` section with a mutation testing tool, which **`monolynx:mutation-check`** reads.

By default the skills **print commands instead of running them**. Environment variables in `.claude/settings.json` change that deliberately:

| Variable | Default | Effect |
|---|---|---|
| `MONOLYNX_PROJECT_SLUG` | - | Project slug. Without it the skills refuse to guess |
| `MONOLYNX_BRANCH_MODE` | `ticket` | `ticket` - branch must contain the ticket number; `sprint` - any branch but main, no questions; `off` - no validation |
| `MONOLYNX_AUTOTEST` | `false` | `true` - the skill runs lint and tests itself |
| `MONOLYNX_AUTOCOMMIT` | `false` | `true` - commits after tests pass |
| `MONOLYNX_AUTOPUSH` | `false` | `true` - pushes without asking |

In a chat client without skills, you can still call the underlying MCP tools in the same order: `get_ticket`, `update_ticket(status="in_progress")`, `search_wiki`/`query_graph`, do the work, `add_comment`, `log_time`, `update_acceptance_criterion`, `update_ticket(status="in_review")`.

**The two skills that matter most are `monolynx:ticket-create` and `monolynx:work`.** Everything else (`setup`, `brief`, `resume`, `project-toolchain`, `mutation-check`, `search`, `ticket-review`, `wiki-ingest`, `wiki-lint`, `sprint-end`, `retro`, `graph-sync`) supports that core loop.

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
| Find the next step | `monolynx:next` - read-only router: checks git, ticket, sprint, sessions, MRs and configuration; recommends 1-3 commands with reasons and ends with `NEXT: <command>`. Does not run them or modify project state |
| Check what a project is missing | `monolynx:setup` - read-only checklist (no arguments): project slug, `toolchain` wiki page, LLM Wiki, code graph and mutation testing in CI, `MONOLYNX_*` flags. Marks each item `OK` or `BRAK` (missing) and, with your consent, runs the fixing skill (`project-toolchain`, `wiki-init`, `create-graph-ci-script`, `create-mutation-ci-script`); a project without CI can run `graph-sync` itself instead of the graph CI step. Autonomy flags are only reported, never set |
| Understand the project | `search_wiki`, `get_wiki_page` (read the `constitution` page if present) |
| Add a task | `monolynx:ticket-create` (or replicate its steps with MCP tools) |
| Review a ticket before starting | `monolynx:ticket-review` |
| Do a task | `monolynx:work <ticket-id>` (small, up to 3 SP: `monolynx:work-simple`) |
| Do something that is not a ticket | `monolynx:brief <request>` - one question for the contract (done / do not touch / stop before), ENTER, work starts. The `brief_guard` hook runs it automatically for a bare first prompt |
| Pick up work after `/clear` or the next day | `monolynx:resume <ticket-id>` (read-only: where we are, what is left, next step) |
| Set up lint and test commands | `monolynx:project-toolchain` (run once per project) |
| Find the test cases your suite is missing | `monolynx:mutation-check [module]` - runs the mutation tool from the `toolchain` wiki page under a hard time budget (by default only the diff against the baseline branch), then turns surviving mutants into acceptance criteria on a ticket or into backlog tickets. Diff mode reports the survivor count, not a percentage; the score is never a gate |
| Run a whole sprint in background sessions | `monolynx:sprint-run [slots]` - one idempotent tick: starts a named background `monolynx:work` session per ticket (own worktree), collects finished ones, reports those waiting for a human. Loop it with `/loop 15m /monolynx:sprint-run` or the plugin script `sprint_run.sh`. Requires `MONOLYNX_AUTOTEST`, `MONOLYNX_AUTOCOMMIT` and `MONOLYNX_AUTOPUSH` |
| Merge MRs and PRs in a set order | `monolynx:mr-queue [!iid \| #pr \| URL ...]` - one idempotent tick on the first MR/PR in the queue: conflict -> cancel the running MR pipeline, merge the target branch in a temporary worktree and push; red CI -> fix in a temporary worktree and push; no approval -> ask you; ready -> merge and wait for green CI on the target branch before the next one. Without arguments it builds the queue from open MRs/PRs (stacked ones after their base, drafts skipped). Loop it with `/loop 10m /monolynx:mr-queue`; merges without a prompt only with `MONOLYNX_AUTOMERGE=true` |
| Close a sprint | `monolynx:sprint-end` (INGEST work logs, LINT, retro, close the sprint) |
| Turn repeated corrections into rules | `monolynx:retro` (reviewer remarks, STOPs and escalations grouped per ticket, with a rule / rubric / constitution proposal for each) |
| Define your team | `.claude/agents/*.md` (Claude Code) or `AGENTS.md` (Codex) - per project, not shipped by Monolynx |
| Enable the LLM Wiki method | `monolynx:wiki-init` (run once per project) |
| Grow the wiki | `monolynx:wiki-ingest`, `monolynx:wiki-sync-merge` (post-merge) |
| Keep the wiki healthy | `monolynx:wiki-lint` |
| Generate a code-graph CI script | `monolynx:create-graph-ci-script` |
| Add a non-blocking mutation-testing CI step | `monolynx:create-mutation-ci-script` - diff mode on MR/PR, report artifact, baseline ratchet (does not install the mutation tool) |
| See which skill to pick | `monolynx:help` - a map of all skills in 6 groups (Setup, Planning, Work, Integration, Sprint, Wiki) plus the manual and autopilot work paths |

Full set of 23 skills: `monolynx:next`, `monolynx:setup`, `monolynx:brief`, `monolynx:work`, `monolynx:work-simple`, `monolynx:resume`, `monolynx:ticket-create`, `monolynx:ticket-review`, `monolynx:sprint-run`, `monolynx:mr-queue`, `monolynx:sprint-end`, `monolynx:retro`, `monolynx:project-toolchain`, `monolynx:mutation-check`, `monolynx:search`, `monolynx:wiki-init`, `monolynx:wiki-ingest`, `monolynx:wiki-lint`, `monolynx:wiki-sync-merge`, `monolynx:help`, `monolynx:create-graph-ci-script`, `monolynx:graph-sync`, `monolynx:create-mutation-ci-script`.

Full module and tool reference: <https://monolynx.com/llms.txt>

## Self-hosting Monolynx itself

Monolynx is open source: <https://gitlab.com/piotrkrych/monolynx>. To run your own instance: clone the repository, copy `.env.example` to `.env`, then `make dev` (Docker Compose with PostgreSQL + pgvector, Neo4j, MinIO, and the FastAPI app on port 8000), `make migrate`, and `make createsuperuser`. Production uses `docker-compose.prod.yml` with a separate monitor worker. Optional integrations degrade gracefully (`OPENAI_API_KEY` for RAG search, `ENABLE_GRAPH_DB` for the dependency graph, `SMTP_HOST` for email). See the repository README for details. Once your instance is up, point `mcp_endpoint` at `https://your-instance/mcp` and everything above applies unchanged.
