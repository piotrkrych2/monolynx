---
name: plugin-packaging
description: Claude Code plugin packaging for Monolynx (MON-72) — structure, validation, and the user_config-in-bash bug
metadata:
  type: project
---

# Monolynx as Claude Code plugin (MON-72)

Plugin lives in repo at `/plugin/` + `/.claude-plugin/marketplace.json`. All UNTRACKED (git status shows dirs, `git diff` does NOT show contents — must Read files directly).

**Why:** packaging skills+agents+remote MCP into one installable plugin instead of manual `install_monolynx_skills` + `.mcp.json` edit.

**How to apply when reviewing plugin work:**
- `claude` CLI IS available at `/Users/piotrkrych/.local/bin/claude` (v2.1.145). Run `claude plugin validate ./plugin` and `claude plugin validate .` for real AC verification — both passed for MON-72.
- Manifest interpolation `${user_config.X}` is valid ONLY in plugin config files (.mcp.json), processed by Claude Code. It is NOT shell-expandable.

## CRITICAL bug pattern: ${user_config.X} inside SKILL.md bash block
- Found in all 4 ticket/search/work skills: `PROJECT_SLUG="${PROJECT_SLUG:-${user_config.project_slug:-monolynx}}"`.
- Bash treats `user_config.project_slug` as invalid param name → `bad substitution` error. The user_config tier of the fallback chain is DEAD inside bash.
- env/.env tiers work; final `monolynx` default works. Fix: drop user_config from bash (resolve at model level) OR plain `${PROJECT_SLUG:-monolynx}`.

## Em-dash rule scope gap
- Team Manager cleaned em-dash from MANIFESTS only. Skill + agent BODIES still full of `—` (work=52, ticket-create=46, ticket-review=35, create-graph-ci=29, agents 13-26 each).
- These are copied verbatim from existing repo skills/agents — em-dash predates this ticket. Human-facing (skill descriptions shown in /help, agent prompts). Violates hard project rule but is inherited debt, not introduced. Flag as medium, not blocker.

## Verified-clean items (MON-72)
- No real `osk_` token leaked anywhere; `.mcp.json` uses only `${user_config.mcp_token}`. (`osk_xxx` placeholder in create-graph-ci is fine.)
- All 3 manifests valid JSON. plugin.json version 1.0.0 semver. userConfig: mcp_token sensitive+required, mcp_endpoint default https://monolynx.com/mcp, project_slug present.
- 6 skills, names WITHOUT `monolynx-` prefix (work, ticket-create, ticket-review, search, help, create-graph-ci-script). frontmatter name: matches dir.
- 7 agents, NO hooks/mcpServers/permissionMode. BUT all retain `memory: project` field (Claude Code-specific; harmless for validate but non-standard for portable plugin agents — minor).
- mcp_server.py git diff EMPTY (unchanged). static/skills/ + static/starter-pack/ exist (README decision grounded).
- Server name "monolynx" in .mcp.json matches `mcp__monolynx__*` in all skills.

## Diacritics inconsistency
- Skill descriptions inconsistent: `search` has full PL diacritics, `help`/`work`/`ticket-*` lack them ("Wyswietl", "instrukcje"). Inherited from source skills.

## plugin/ vs static/ sync — recurring desync trap (MON-75)
- Every skill exists twice: `plugin/skills/<name>/SKILL.md` (NO `name:` field) and `src/monolynx/static/skills/monolynx-<name>/SKILL.md` (WITH `name:`). Body MUST be identical except frontmatter.
- Review method: strip frontmatter from both via `awk 'NR==1&&/^---/{f=1;next} f&&/^---/{f=0;next} !f'` then `diff`. Catches partial syncs.
- MON-75 trap: agent edited static work-simple fully but pluginonly partially — plugin retained 2 stale "wiki update" mentions (frontmatter description + intro line) that static had corrected. Plugin is source-of-truth per CLAUDE.md, yet was the LESS complete copy. Always diff BOTH directions, don't assume plugin is ahead.

## MON-75 review (post-merge wiki write-back)
- 84/100 REQUEST CHANGES. backend 84, devops 84.
- BLOCKERS: (1) plugin/static work-simple desync — plugin SKILL.md lines 2+9 still say "wiki update zostaje" contradicting reworked sekcja 4.1; (2) cicd/wiki_post_merge.py DONE_STATUSES includes "in_review" — recreates the premature write-back the ticket removes (in_review = NOT yet merged to main).
- MINORS: literowka "wikilinlach"->"wikilinkach" (both wiki-sync-merge copies); print_manifest prints /monolynx:wiki-ingest but text says wiki-sync-merge.
- Good pattern: new CI job wiki-post-merge mirrors sync-graph exactly (image, stage deploy, rules main+on_success), logic in cicd/*.py not inline (makefile-cli rule respected), non-fatal exit 0, retry+backoff, skip without token.
- Context: MON-74 (alias parser) still in_review → plain [[slug]]-only rule in wiki-sync-merge is correct/current. work/SKILL.md never had write-back (read-only search only) — "confirm no write-back" = legit no-op.
