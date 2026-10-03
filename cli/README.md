# monolynx-cli

[![PyPI](https://img.shields.io/pypi/v/monolynx-cli)](https://pypi.org/project/monolynx-cli/)

Command-line client for the Monolynx platform: manage projects, tickets, sprints and wiki pages, and read the code dependency graph, from your terminal.

## Installation

Requirements: Python 3.10 or newer.

With [pipx](https://pipx.pypa.io/) (recommended, installs the CLI in its own isolated environment):

```bash
pipx install monolynx-cli
```

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install monolynx-cli
```

With pip:

```bash
pip install monolynx-cli
```

With Homebrew (macOS and Linux), from the `monolynx/tap` tap. Either install in one step, which adds the tap for you:

```bash
brew install monolynx/tap/monolynx
```

or add the tap first and then install by the short name:

```bash
brew tap monolynx/tap
brew install monolynx
```

The formula is not in homebrew-core, so the short `brew install monolynx` works only after `brew tap monolynx/tap`.

Check the installation:

```bash
monolynx --version
```

## Quickstart

1. Sign in. The default flow opens your browser and signs you in with OAuth:

   ```bash
   monolynx auth login
   ```

   On a CI runner or a headless machine, use an API token generated in the Monolynx dashboard (`/dashboard/profile/tokens`) instead:

   ```bash
   monolynx auth login --token osk_your_token
   ```

2. List the projects you have access to:

   ```bash
   monolynx project list
   ```

   ```text
   ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━┓
   ┃ id                                   ┃ name       ┃ slug       ┃ code ┃ role   ┃
   ┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━┩
   │ 6f1c2a9e-3b4d-4c8e-9a1f-2d7b5e8c0a31 │ My Project │ my-project │ MP   │ admin  │
   └──────────────────────────────────────┴────────────┴────────────┴──────┴────────┘
   ```

   Columns trimmed for brevity: the CLI prints every field the API returns, so the real table also has `description` and `created_at`.

3. List the tickets of a project. Ticket commands need a project, passed as a global option before the command group:

   ```bash
   monolynx --project my-project ticket list
   ```

   ```text
   ┏━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━┓
   ┃ key    ┃ title                        ┃ status      ┃ priority ┃ story_points ┃
   ┡━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━┩
   │ MP-12  │ Export the monthly report    │ in_progress │ high     │ 3            │
   │ MP-11  │ Fix login redirect           │ todo        │ medium   │ 2            │
   └────────┴──────────────────────────────┴─────────────┴──────────┴──────────────┘
   Strona 1/1, razem: 2
   ```

   Columns trimmed for brevity: the real table has 14 columns (`id`, `key`, `title`, `description`, `status`, `priority`, `story_points`, `sprint_id`, `assignee_email`, `due_date`, `label_ids`, `created_via_ai`, `created_at`, `updated_at`). The last line is the page footer, printed on stderr.

   To avoid typing `--project` every time, store the project in your profile (replace `default` with your profile name if you use another one):

   ```bash
   monolynx config set profile.default.project my-project
   monolynx ticket list
   ```

### How signing in works

`monolynx auth login` without options uses OAuth 2.1 (Authorization Code with PKCE):

1. The CLI reads the server metadata from `<endpoint>/.well-known/oauth-authorization-server`.
2. It starts a short-lived local server on `127.0.0.1` (random port, path `/callback`) and registers itself with Dynamic Client Registration. Registration runs on every login, because the server matches the redirect address including the port.
3. It opens your browser on the Monolynx consent page. You have 300 seconds to approve.
4. It exchanges the code for tokens and checks them with `GET /api/v2/me`. Only then does it store them in the profile (`auth_type = "oauth"`, `access_token`, `refresh_token`, `expires_at`, `client_id`).

A timeout, a denied consent or a mismatched `state` parameter ends the login with exit code 3 and leaves the configuration unchanged.

Login options:

```bash
monolynx auth login --no-browser
monolynx auth login --token osk_your_token
monolynx auth login --endpoint https://monolynx.example.com --profile work
```

- `--no-browser` prints the authorization URL on stderr instead of opening a browser (the CLI does the same when opening the browser fails). The browser must still run on the same machine as the CLI, because the redirect goes to `127.0.0.1`. Over SSH without port forwarding, use `--token`.
- `--token` stores an API token without a browser (`auth_type = "token"`).
- `--endpoint` and `--profile` work with both flows.

The OAuth access token is valid for 30 days. Every command that calls the API keeps the session alive. `project`, `member`, `ticket`, `sprint`, `wiki` and `graph` commands do it in two ways, `auth whoami` only reactively:

- Proactively (not in `auth whoami`): when the profile's `expires_at` is less than 24 hours away (or already past), the CLI refreshes the tokens once before the first request of the command and sends the request with the new access token. The server rotates the refresh token and both new tokens are saved in the profile, so regular use extends the session. If this refresh fails, the command goes on with the current token.
- Reactively: when the server answers 401, the CLI refreshes the tokens once and repeats the request. A failed refresh ends with a message asking you to run `monolynx auth login` and exit code 3.

Neither applies to `--token` profiles or to `MONOLYNX_TOKEN`.

`monolynx auth whoami` shows the signed-in user; for an OAuth profile it also shows `expires_at`. Tokens are always masked, never printed in full. `monolynx auth logout` removes the stored tokens from the profile.

## Command reference

Help texts, prompts and messages of the CLI are in Polish. Run `monolynx --help`, `monolynx <group> --help` or `monolynx <group> <command> --help` for the built-in help.

Arguments in angle brackets are positional; `[...]` marks an optional one. Options are given after the command name, global options before the command group.

### Global options

| Option | Default | Description |
|---|---|---|
| `-o`, `--output` | `table` in a terminal, `json` otherwise | Output format: `table`, `json`, `yaml` or `csv`. |
| `--no-color` | off | Disable table colors. The `NO_COLOR` environment variable (any non-empty value) does the same. |
| `-q`, `--quiet` | off | Silence progress messages on stderr. Data on stdout and error messages are unaffected. |
| `--profile` | active profile | Configuration profile to use. |
| `--project` | none | Project slug. |
| `--debug` | off | Log HTTP requests and responses on stderr (token always masked, body cut to 500 characters). |
| `--timeout` | `30.0` | HTTP request timeout in seconds, must be greater than zero. |
| `--version` | | Print the version and exit. |

```bash
monolynx -o json auth whoami
monolynx --quiet -o csv config list
monolynx --profile work --project my-project ticket list
monolynx --timeout 10 --debug auth whoami
```

### `auth`

| Command | Arguments and options | Description |
|---|---|---|
| `auth login` | `--endpoint`, `--token`, `--profile`, `--no-browser` | Sign in with OAuth in the browser, or with an API token, and save the session in the profile. |
| `auth logout` | `--profile` | Remove the stored secrets (`token`, `access_token`, `refresh_token`, `expires_at`) of the profile. |
| `auth whoami` | `--profile` | Show the signed-in user. |

The local `--profile` of an `auth` command takes precedence over the global `--profile`.

### `config`

| Command | Arguments and options | Description |
|---|---|---|
| `config get` | `<key>` | Print the value under a dotted key (secrets masked). |
| `config set` | `<key> <value>` | Set the value under a dotted key. |
| `config list` | | Print the whole configuration as `key`/`value` rows (secrets masked). |

Valid keys are `general.active_profile` and `profile.<name>.<field>`, see [Configuration and profiles](#configuration-and-profiles). These commands edit the file directly and ignore `--profile` and `--project`.

### `project`

| Command | Arguments and options | Description |
|---|---|---|
| `project list` | | List the projects you have access to (all pages). |
| `project get` | `<slug>` | Show project details: your role, member and ticket counts, active sprint. |
| `project create` | `--name` (required), `--slug`, `--description`, `--code` | Create a project. Without `--slug` and `--code` the server derives them from the name. |
| `project update` | `<slug>`, `--name`, `--description`, `--new-slug` | Change a project; only the given options are sent. |
| `project delete` | `<slug>`, `-y`/`--yes` | Delete a project (asks for confirmation unless `--yes`). |
| `project summary` | `<slug>` | Show the project summary: unresolved errors, monitors, uptime, active sprint, backlog. |

### `member`

| Command | Arguments and options | Description |
|---|---|---|
| `member list` | `--project` | List project members (all pages). |
| `member invite` | `<email>`, `--project`, `--role` (`member` or `admin`, default `member`) | Invite a person to the project. |
| `member remove` | `<email>`, `--project`, `-y`/`--yes` | Remove a member (asks for confirmation unless `--yes`). |

The local `--project` of a `member` command takes precedence over the global `--project`, `MONOLYNX_PROJECT` and the profile.

### `ticket`

All `ticket` commands work on the project resolved from `--project`, `MONOLYNX_PROJECT` or the profile. `<ticket>` is a ticket UUID or key, for example `MON-42`.

| Command | Arguments and options | Description |
|---|---|---|
| `ticket list` | `--status`, `--priority`, `--search`, `--sprint`, `--due-before`, `--due-after`, `--overdue`, `--label`, `--page`, `--compact` | List project tickets with filters (one page); every ticket carries its blockers (`blocked_by`). `--compact` leaves out the ticket descriptions. `--search` matches the title only; use `ticket search` to match the description too. |
| `ticket search` | `[query]`, `--status`, `--priority`, `--assignee`, `--sprint`, `--due-before`, `--due-after`, `--page` | Search tickets by phrase (title or description) and filters (one page). |
| `ticket get` | `<ticket>` | Show ticket details, including `spec_page_id` (`null` when the ticket has no spec page). |
| `ticket create` | `--title` (required), `--description`, `--description-file`, `--priority`, `--sp`/`--story-points`, `--sprint`, `--assignee`, `--due`, `--label`, `--ac`, `--spec-page`, `--blocked-by`, `--brief` | Create a ticket. `--label`, `--ac` and `--blocked-by` can be repeated; `--blocked-by` takes the UUID of the blocking ticket (not the key). `--brief` prints only the identifying fields, status and relations. |
| `ticket update` | `<ticket>`, `--title`, `--description`, `--description-file`, `--status`, `--priority`, `--sp`/`--story-points`, `--sprint`, `--assignee`, `--due`, `--label`, `--blocked-by`, `--no-blockers`, `--brief` | Update a ticket; only the given fields are sent. `--label` and `--blocked-by` replace the current values; `--blocked-by` takes the UUID of the blocking ticket (not the key). `--brief` prints only the identifying fields, status and relations. |
| `ticket delete` | `<ticket>`, `-y`/`--yes` | Delete a ticket (asks for confirmation unless `--yes`). |
| `ticket bulk-update` | `<tickets>...`, `--status`, `--priority`, `--assignee`, `--sprint`, `--due` | Update many tickets in one request. |
| `ticket comment list` | `<ticket>` | List ticket comments (all pages, flat list). |
| `ticket comment add` | `<ticket>`, `--body` (required) | Add a comment (markdown). |
| `ticket label list` | | List project labels (all pages, flat list). |
| `ticket label create` | `--name` (required), `--color` | Create a project label, for example `--color "#ff0000"`. |
| `ticket ac list` | `<ticket>` | List acceptance criteria (all pages, flat list). |
| `ticket ac add` | `<ticket> <description>` | Add an acceptance criterion. |
| `ticket ac update` | `<ticket> <criterion_id>`, `--description`, `--done`/`--not-done` | Change the description or mark a criterion as done or not done. |
| `ticket ac delete` | `<ticket> <criterion_id>` | Delete an acceptance criterion. |

Notes:

- Dates use the `YYYY-MM-DD` format. Ticket statuses are `backlog`, `todo`, `in_progress`, `in_review`, `done`. The server rejects an unknown status or priority in `ticket create`, `ticket update` and `ticket bulk-update` (exit code 1), but silently ignores it in the `--status` and `--priority` filters of `ticket list` and `ticket search`: the filter is not applied and the command succeeds, so check the spelling.
- Identifiers in the URL path (ticket, sprint, page, criterion, project slug) are URL-encoded. A bare `.` or `..` (or an empty value) is refused with exit code 2 before any request is sent.
- `--description -` and `--body -` read the text from stdin.
- In `ticket update` and `ticket bulk-update`, an empty string for `--sprint`, `--assignee` or `--due` clears the value.
- `ticket list` shows blockers per ticket. With `-o json` and `-o yaml`, `blocked_by` is the full list of objects (`id`, `key`, `title`, `status`), empty when the ticket has no blockers. With `-o table` and `-o csv` it is one `blocked_by` column holding the blocker keys joined with `, ` (empty when there are none). `ticket search` results carry no `blocked_by`.
- `ticket list` and `ticket get` check the server response before rendering. A list item without a `blocked_by` list, or ticket details without the `spec_page_id` key, end with the error `Nieprawidłowa odpowiedź serwera` and exit code 1. This happens against a server older than the CLI, which does not send these fields yet; the CLI refuses to render rather than show a ticket as having no blockers or no spec page.
- `ticket list --compact` sends `compact=true`, and the server returns the list items without the `description` field; every other field stays, including `blocked_by`. Without the flag the parameter is not sent and the items carry `description` as before.
- `ticket create --brief` and `ticket update --brief` print exactly seven fields, in this order: `id`, `key`, `title`, `status`, `sprint_id`, `blocked_by`, `blocks`. The request and the server response are unchanged; the CLI filters the output. With `-o json` and `-o yaml`, `blocked_by` and `blocks` are full lists of objects (`id`, `key`, `title`, `status`); with `-o table` and `-o csv` each is one column holding the ticket keys joined with `, `, as in `ticket list`.
- `--brief` checks the server response before rendering. A response that is not an object, has an empty or missing `id` or `key`, lacks the `title`, `status` or `sprint_id` key (`sprint_id` may be `null`), or whose `blocked_by` or `blocks` is not a list of objects with a non-empty `key` ends with the error `Nieprawidłowa odpowiedź serwera` and exit code 1. The write may already have reached the server, so check the ticket before repeating the command.

```bash
monolynx --project my-project ticket create --title "Fix login redirect" --priority high --sp 2 --ac "Redirect keeps the next parameter"
monolynx --project my-project ticket update MP-11 --status in_progress
git log -1 --format=%B | monolynx --project my-project ticket comment add MP-11 --body -
```

### `sprint`

| Command | Arguments and options | Description |
|---|---|---|
| `sprint list` | `--status` | List all project sprints (all pages, flat list), optionally filtered by status (for example `planning`, `active`, `completed`). |
| `sprint get` | `<sprint_id>` | Show sprint details. |
| `sprint create` | `--name` (required), `--start` (required), `--end`, `--goal` | Create a sprint. Dates use `YYYY-MM-DD`. |
| `sprint update` | `<sprint_id>`, `--name`, `--goal`, `--start`, `--end` | Change the name, goal or dates of a sprint. |
| `sprint start` | `<sprint_id>` | Start a sprint (only one sprint can be active at a time). |
| `sprint complete` | `<sprint_id>` | Complete a sprint; unfinished tickets go back to the backlog. |
| `sprint board` | | Show the Kanban board of the active sprint. |
| `sprint burndown` | `[sprint_id]` | Show the burndown of a sprint (the active one by default). |

### `wiki`

| Command | Arguments and options | Description |
|---|---|---|
| `wiki list` | `--tree` | List all wiki pages of the project; `--tree` indents titles by depth. |
| `wiki get` | `<page_id>`, `--raw` | Show a page with its content; `--raw` prints only the markdown. |
| `wiki create` | `--title` (required), `--content`, `--file`, `--parent`, `--position`, `--public`/`--no-public` | Create a page. Content comes from `--content`, `--file` or stdin (`--content -`). |
| `wiki update` | `<page_id>`, `--title`, `--content`, `--file`, `--position`, `--public`/`--no-public` | Update a page; only the given fields are sent. |
| `wiki edit` | `<page_id>` | Edit the page content in `$EDITOR` (`vi` by default); saves only when the content changed. |
| `wiki delete` | `<page_id>`, `--yes` | Delete a page together with all its subpages. This cannot be undone. |
| `wiki search` | `<query>`, `--limit` (default `10`) | Semantic search over wiki pages. |
| `wiki config get` | | Show the LLM Wiki method configuration of the project. |
| `wiki config set` | `--enabled`/`--disabled` | Turn the LLM Wiki method on or off (requires the `settings:write` permission). |

`--public` publishes the page at `/blog/{slug}`, visible to anyone without signing in. `wiki delete` has no `-y` short form. `--content -` with an empty standard input, an unreadable or non-UTF-8 `--file`, and a missing project are usage errors (exit code 2, no request sent).

### `graph`

Read-only commands for the code dependency graph of the Connections module. All of them work on the project resolved from `--project`, `MONOLYNX_PROJECT` or the profile, and need the `connections:read` permission.

| Command | Arguments and options | Description |
|---|---|---|
| `graph nodes` | `--type`, `--search`, `--limit` (1 to 1000) | List graph nodes, optionally filtered by type and by a fragment of the name (case-insensitive). The server default for `--limit` is 100. |
| `graph node` | `<node_id>`, `--depth` (min. 1, the server caps it at 5), `--relation-type`, `--node-type` | Show a node with its neighborhood: the node itself, the neighboring nodes, the edges and the depth of every node. `--relation-type` and `--node-type` can be repeated. |
| `graph query` | `--node-type`, `--search`, `--limit` (1 to 1000) | Fetch the graph or a subgraph of the project (nodes and edges). `--search` matches a fragment of the name or file path and returns the matching nodes with their direct neighbors. The server default for `--limit` is 200. |
| `graph path` | `--source-id` (required), `--target-id` (required) | Find the shortest path between two nodes. |
| `graph stats` | | Show graph statistics: the number of nodes and edges, in total and per type. |

Notes:

- `<node_id>` is the `id` field from `graph nodes`. It may contain `/`; the CLI URL-encodes it.
- Node types are `File`, `Class`, `Method`, `Function`, `Const` and `Module`. Relation types are `CONTAINS`, `CALLS`, `IMPORTS`, `INHERITS`, `USES` and `IMPLEMENTS`.
- `--depth` is the neighborhood depth: at least 1, and the server caps larger values at 5 (the server default is 1). A `--depth` below 1 or a `--limit` outside 1 to 1000 (`graph nodes`, `graph query`) is a usage error: exit code 2, no request sent. Options you leave out are not sent, so the defaults stay on the server.
- `graph nodes` prints a flat list. `graph query` and `graph path` print an object with `nodes` and `edges`; `graph node` adds `node` and `depth_map` (node id to depth). `graph stats` prints `total_nodes`, `total_edges`, `nodes_by_type` and `edges_by_type`. When there is no path, `graph path` succeeds with empty `nodes` and `edges`.
- Every command checks the server response before rendering. A missing required field or a wrong type ends with the error `Nieprawidłowa odpowiedź serwera` and exit code 1, never with an empty result and exit code 0.
- A project with the graph database turned off, or an unreachable graph database, ends with a 503 error (exit code 1 after the retries). An unknown node in `graph node` is a 404 (exit code 1).
- There are no commands that write to the graph. The graph is filled by the graphify sync (`cicd/sync_graph.py`), see the Connections module in the dashboard.

```bash
monolynx --project my-project graph nodes --type Class --search Sprint
monolynx --project my-project graph node "<node_id>" --depth 2 --relation-type CALLS --relation-type IMPORTS
monolynx -o json --project my-project graph stats | jq '.nodes_by_type'
```

### `version`

| Command | Arguments and options | Description |
|---|---|---|
| `version` | | Show the installed CLI version as `{"current": ...}`. Makes no network request. |
| `version --check` | `--check` | Also ask PyPI for the newest version and print `current`, `latest`, `update_available` and `upgrade_command`. |

`upgrade_command` depends on how the CLI is installed: `brew upgrade monolynx`, `pipx upgrade monolynx-cli`, `uv tool upgrade monolynx-cli`, or `pip install -U monolynx-cli` when the installer is not recognized. Without a network (timeout, no connection, PyPI error) the command still exits with code 0 and prints `latest` as `null` and `update_available` as `false`. It needs no sign-in and no configuration, and sends no token anywhere.

```bash
monolynx -o json version --check
```

### Reserved groups

`monitoring`, `issues`, `time`, `plan`, `rozliczenia`, `pipeline`, `heartbeat` and `role` appear in `monolynx --help` but are reserved: they have no commands yet (not yet available).

### Confirmations

`project delete`, `member remove`, `ticket delete` and `wiki delete` ask for confirmation in a terminal. Outside a terminal they refuse to run without `--yes` (exit code 2). Declining the prompt ends with exit code 1.

## Configuration and profiles

The CLI keeps its settings in `config.toml` inside the per-user config directory reported by [platformdirs](https://pypi.org/project/platformdirs/) for the application name `monolynx`:

| System | Path |
|---|---|
| Linux | `~/.config/monolynx/config.toml` |
| macOS | `~/Library/Application Support/monolynx/config.toml` |
| Windows | the per-user config directory reported by platformdirs |

The file is written atomically with permissions `0600` (the directory gets `0700`; not applied on Windows). `auth login` creates it for you. Example with two profiles:

```toml
[general]
active_profile = "prod"

[profile.prod]
endpoint = "https://monolynx.com"
project = "my-project"
auth_type = "oauth"
access_token = "..."
refresh_token = "..."
expires_at = "2026-10-29T12:00:00+00:00"
client_id = "..."

[profile.staging]
endpoint = "https://staging.monolynx.example.com"
project = "my-project"
auth_type = "token"
token = "osk_..."
```

Profile fields: `endpoint`, `project`, `auth_type` (`token` or `oauth`), `token`, `access_token`, `refresh_token`, `expires_at`, `client_id`. `config get` and `config list` always mask `token`, `access_token` and `refresh_token`.

Switch profiles:

```bash
monolynx --profile staging ticket list
MONOLYNX_PROFILE=staging monolynx ticket list
monolynx config set general.active_profile staging
```

The profile is chosen in this order: `--profile`, then `MONOLYNX_PROFILE`, then `general.active_profile`, then `default`.

Only `auth login` has an `--endpoint` option. For other commands, set the endpoint in the profile or through `MONOLYNX_ENDPOINT`:

```bash
monolynx config set profile.staging.endpoint https://staging.monolynx.example.com
```

## Environment variables

| Name | Meaning | Default |
|---|---|---|
| `MONOLYNX_ENDPOINT` | API address of the Monolynx server. | `https://monolynx.com` |
| `MONOLYNX_TOKEN` | API token used instead of the tokens stored in the profile. | none |
| `MONOLYNX_PROJECT` | Project slug. | none |
| `MONOLYNX_PROFILE` | Configuration profile. | `general.active_profile`, otherwise `default` |
| `NO_COLOR` | Any non-empty value disables table colors, like `--no-color`. | unset |

Resolution order for every setting: command-line option, then environment variable, then config file, then default. The options are `--profile` and `--project` (global), and `--endpoint` and `--token` (only in `auth login`).

`MONOLYNX_TOKEN` takes precedence over the tokens stored in the profile and is never refreshed. Use it in CI together with `MONOLYNX_ENDPOINT` and `MONOLYNX_PROJECT`, without a config file.

## Exit codes

| Code | Meaning | Example causes |
|---|---|---|
| `0` | Success | |
| `1` | API or local error | 4xx other than 401/403 (for example 404 or 422), 429 or 5xx after all retries; corrupted config file; declined confirmation; `config get` for a key without a value |
| `2` | Usage error | Unknown option or bad argument; no project resolved; destructive command without `--yes` outside a terminal; `--timeout` not greater than zero; invalid `config` key; `update` without any field to change; empty stdin for `--content -`; a bare `.` or `..` as an identifier in a URL path |
| `3` | Authentication error | 401 or 403; failed token refresh; no token stored; OAuth login timeout, denied consent or mismatched `state` |
| `4` | Network error | Timeout, connection refused, DNS failure |

Errors are printed on stderr as `Błąd: <status> <title>: <detail>`. `--quiet` does not silence them.

Retries and timeouts:

- `GET` and `DELETE` are retried on 429 and 5xx; `POST` and `PATCH` only on 429, because retrying a 5xx could create a duplicate.
- At most 4 attempts, with backoff of 0.5 s, 1 s and 2 s. On 429 a `Retry-After` value in seconds takes precedence, capped at 60 s.
- Network errors are not retried.
- `--timeout` sets the timeout of a single request (30 s by default).
- `--debug` prints each request (method, URL, headers) and response (status, body cut to 500 characters) on stderr, with the token masked.

## Piping and scripting

Without `-o`, the output format is `table` when stdout is a terminal and `json` otherwise, so piping into `jq` works without `-o json`. Global options such as `-o` go before the command group.

`ticket list` and `ticket search` return one page as a pagination envelope (use `--page` for the next pages):

```json
{
  "items": [{"key": "MP-12", "title": "Export the monthly report"}],
  "page": 1,
  "per_page": 20,
  "total": 1,
  "total_pages": 1
}
```

Read the rows through `.items[]` (the example above is shortened; each item has all ticket fields):

```bash
monolynx -o json --project my-project ticket list | jq -r '.items[] | .key'
monolynx -o json --project my-project ticket list --page 2 | jq '.total_pages'
```

`project list`, `member list`, `wiki list`, `sprint list`, `ticket comment list`, `ticket label list` and `ticket ac list` fetch all pages themselves and return a flat list, so there is no `.items`:

```bash
monolynx -o json project list | jq -r '.[].slug'
monolynx -o json --project my-project sprint list --status active | jq -r '.[].name'
monolynx -o json --project my-project ticket comment list MP-11 | jq -r '.[].content'
```

Status messages such as `Zalogowano jako ...`, `Wylogowano z profilu ...` and `Ustawiono ...` (`auth login`, `auth logout`, `config set`) go to stderr and `--quiet` silences them, so stdout stays empty for these commands.

In `table` and `csv` formats the page footer (`Strona X/Y, razem: Z`) goes to stderr, so stdout holds data only.

A script that reacts to exit codes. It captures the CLI output first and pipes it to `jq` only afterwards, because `$?` after a pipeline holds the exit code of the last command (`jq`), not of `monolynx`:

```bash
#!/usr/bin/env bash
set -u

json=$(monolynx -o json --project my-project ticket list --status todo)
status=$?
case $status in
  0) printf '%s\n' "$json" | jq -r '.items[] | .key' ;;
  3) echo "Not signed in or token expired: run 'monolynx auth login'." >&2; exit 3 ;;
  4) echo "Monolynx is unreachable, try again later." >&2; exit 4 ;;
  *) echo "monolynx failed with exit code $status." >&2; exit "$status" ;;
esac
```

## The `mnx` alias

The package installs two equivalent commands, `monolynx` and `mnx`. Every command works with either:

```bash
mnx ticket list
mnx -o json project list
```

### Shell completion

`monolynx completion <shell>` prints a completion script for `bash`, `zsh` or `fish` on stdout. It needs no sign-in and no configuration. An unknown shell ends with exit code 2.

The script is bound to the name the program was invoked as, so generate it separately for each command you want completed. `monolynx completion zsh` prints a script for `monolynx`, `mnx completion zsh` a script for `mnx`. Any other invocation name (for example `python -m monolynx_cli`) gets the script for `monolynx`.

Load the script into the current session (add the line to your shell startup file to make it permanent):

```bash
source <(monolynx completion bash)
```

```zsh
autoload -Uz compinit && compinit
source <(monolynx completion zsh)
```

```fish
monolynx completion fish | source
```

Or save it to the place your shell reads completions from. The zsh script starts with `#compdef`, so it works as a file in a directory on `$fpath` (the file must be named `_monolynx`):

```bash
# bash
mkdir -p ~/.local/share/bash-completion/completions
monolynx completion bash > ~/.local/share/bash-completion/completions/monolynx
```

```zsh
# zsh: add "fpath+=~/.zfunc; autoload -Uz compinit; compinit" to ~/.zshrc, before compinit runs
mkdir -p ~/.zfunc
monolynx completion zsh > ~/.zfunc/_monolynx
```

```fish
# fish
mkdir -p ~/.config/fish/completions
monolynx completion fish > ~/.config/fish/completions/monolynx.fish
```

For the `mnx` alias, do the same with `mnx completion <shell>` and save the result under the name `mnx` (`_mnx` for zsh, `mnx.fish` for fish):

```bash
mnx completion fish > ~/.config/fish/completions/mnx.fish
```

Alternatively, let the CLI install completion for your current shell with `monolynx --install-completion`, or print the script with `monolynx --show-completion` (to copy it or customize the installation). Both detect the shell automatically; `--install-completion` writes the script for `monolynx` only, so run `mnx --install-completion` for the alias. Open a new shell session afterwards.

## Documentation and issues

- Documentation: <https://gitlab.com/piotrkrych/monolynx/-/blob/main/cli/README.md>
- Issues: <https://gitlab.com/piotrkrych/monolynx/-/issues>
- Source and homepage: <https://gitlab.com/piotrkrych/monolynx>

## License

MIT. See the `LICENSE` file distributed with the package.

## For command authors

Commands never print result data with `print()`, `typer.echo()` or `rich.print()`. A command builds a structure (`list[dict]` for records, `dict` for a single record or a pagination envelope with an `items` key) and hands it over as its last step:

```python
from monolynx_cli.output import emit

emit(ctx, data)
```

`emit(ctx, data)` reads the global options (`monolynx_cli.options.GlobalOptions`) and calls `render(data, fmt, *, no_color, quiet)`, the only function that writes result data to stdout, in `table`, `json`, `yaml` or `csv`. Progress messages (not data, not errors) go through `echo_stderr(msg, *, quiet=False, no_color=False)`, which respects `--quiet`. Error messages go to stderr with `typer.echo(..., err=True)` and are never silenced.

HTTP calls go through `monolynx_cli.client.MonolynxClient`, never through `httpx` directly. The client adds the `Authorization: Bearer`, `User-Agent: monolynx-cli/<version>` and `Accept: application/json` headers, applies the retry policy and returns parsed JSON (`None` for 204). Commands do not catch client exceptions: a `MonolynxError` goes up to the global handler in `monolynx_cli.main`, which prints it and exits with the code from [Exit codes](#exit-codes).

```python
opts = get_options(ctx)
with MonolynxClient(settings.endpoint, settings.bearer, timeout=opts.timeout, debug=opts.debug) as client:
    emit(ctx, client.get("/api/v2/projects"))
```

Development install and tests, from the repository root:

```bash
pip install -e "cli/[test]"
pytest cli/tests
```
