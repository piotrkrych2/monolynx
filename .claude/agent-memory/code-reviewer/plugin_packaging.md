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
