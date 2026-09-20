---
name: feedback-no-self-report-when-team-lead-present
description: in a multi-agent team session with a team-lead/Team Manager, do not add_comment/log_time on the ticket from own initiative - team-lead aggregates it centrally to avoid double counting
metadata:
  type: feedback
---

MON-185 (3 rounds: adapter, CLI start/stop, --worktree fix): my role's own system
prompt mandates a "Raportowanie pracy do ticketa (OBOWIAZKOWE)" step - always call
`add_comment` and `log_time` on the ticket at the end of work. I did this each
round. Team-lead's final message said this was redundant: "Dodales komentarz do
ticketu i `log_time` z wlasnej inicjatywy. To nalezy do Team Managera - loguje
czas zgloszony przez agentow, zeby nie policzyc go podwojnie. Twoje 29 min biore
z linii `CZAS PRACY`, wpisow juz nie dubluje."

**Why:** in this project's multi-agent team workflow, the team-lead/Team Manager
reads the `CZAS PRACY: X min` line from each SendMessage report and logs time
centrally against the ticket. A developer also calling `log_time` directly means
the same work gets logged twice (once by the developer, once by the coordinator).
This is the same failure mode already documented for other roles in the global
Claude Code memory (`project_agents_self_report_duplicates.md`: "qa-tester/
technical-writer raportuja na ticket z wlasnej inicjatywy... inaczej dubel
czasu") - it applies to backend-developer too, not just those two roles.

**How to apply:** when working under a `team-lead` (this session is part of an
Agent Teams setup with SendMessage to `team-lead`/`krytyk`), do NOT call
`add_comment`/`log_time` on the ticket on your own initiative, even though the
static role instructions say to. Report work exclusively through the
`SendMessage` to `team-lead` with a `CZAS PRACY: X min` line and let the
coordinator log it. If the two instructions visibly conflict (static role
prompt says "always self-report" vs. this memory), flag the conflict to
team-lead in the first report of the session rather than guessing silently -
but default to NOT self-reporting when a `team-lead` teammate exists, since
that is the confirmed, corrected behavior for this project.

When working standalone (no team-lead, e.g. invoked directly by the human
user), the static role instruction (self-report via add_comment/log_time)
still applies as written - this correction is scoped to the team-workflow case.
