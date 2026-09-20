---
name: patterns-yaml-frontmatter-to-toml
description: Copying an already-escaped YAML double-quoted frontmatter field into TOML - do NOT json.loads-decode it first
metadata:
  type: project
---

`plugin/agents/*.md` frontmatter has `description: "..."` as a YAML
double-quoted scalar, hand-written with literal two-character `\n` sequences
(backslash + n) instead of real newlines - this is the same convention
Claude Code's own subagent tool listing renders verbatim (visible as literal
`\n\n` in the "Available agent types" system reminder, not as actual line
breaks). The raw file bytes for that already contain a DOUBLE backslash
(`\\n`, 2 backslash chars + n), because it's already been through one layer
of escaping by whoever authored/generated the frontmatter.

**Why:** when generating `.codex/agents/*.toml` (MON-184, `scripts/sync_agents.py
--codex`) from this source, the first implementation decoded the description
with `json.loads('"' + raw + '"')` and then re-escaped the result for TOML.
That double-processes the string: decoding turns `\\n` into a real backslash
+ 'n', and re-escaping then turns that single backslash into `\\`, producing
`\\n` (double-escaped) in the output TOML instead of the original `\n`
literal. The bug is invisible by eye (the TOML still "looks" plausible) and
only shows up as extra backslashes when the parsed TOML value is printed/
diffed against the source.

**How to apply:** when moving a YAML double-quoted scalar's raw captured text
into another format whose basic-string escaping is compatible (TOML and JSON
agree on `\"` and `\\` for the characters actually used here), copy the
captured regex group **verbatim**, do not decode-then-re-escape. Decode only
if you need the actual semantic string value for something other than
re-embedding it in an equivalently-escaped string literal.

See `scripts/sync_agents.py::parse_agent_markdown` for the working
implementation and its docstring for the full reasoning.
