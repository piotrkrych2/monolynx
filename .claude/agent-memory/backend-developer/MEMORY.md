# Agent Memory — Backend Developer

## Memory Index

- [patterns_mcp_compact_output.md](patterns_mcp_compact_output.md) — MCP tools returning compact strings instead of list[dict] to save tokens
- [patterns_edit_tool.md](patterns_edit_tool.md) — How to reliably use the Edit tool on large files
- [patterns_xml_comment_double_hyphen.md](patterns_xml_comment_double_hyphen.md) — XML comment with "--" (CLI flags) in provenance header breaks the whole fixture, not just the comment
- [patterns_yaml_frontmatter_to_toml.md](patterns_yaml_frontmatter_to_toml.md) — copy an already-escaped YAML frontmatter string verbatim into TOML, never json.loads-decode then re-escape (double-escaping bug)
- [patterns_markdown_marker_parsing.md](patterns_markdown_marker_parsing.md) — stack-based parsing of paired shared:/runtime: HTML-comment markers in SKILL.md, GFM table trailing-content trick, minimal reflow rule, verify-without-running-the-validator trick
- [patterns_os_kill_self_test_landmine.md](patterns_os_kill_self_test_landmine.md) — os.kill(pid, SIGTERM) na os.getpid() w niemockowanym tescie zabija runner pytest (MON-185)
- [patterns_cli_entrypoint_gap.md](patterns_cli_entrypoint_gap.md) — adapter module with start()/stop() is dead code without a CLI subcommand calling it; verify the actual call site, not just green tests (MON-185)
- [feedback_no_self_report_when_team_lead_present.md](feedback_no_self_report_when_team_lead_present.md) — with a team-lead present, don't add_comment/log_time yourself; report via SendMessage CZAS PRACY line only, team-lead logs it centrally (MON-185)
- [feedback_recheck_before_repeating_flag.md](feedback_recheck_before_repeating_flag.md) — re-check file state (git diff) before repeating a previously-flagged issue in a later round; another agent may have already fixed it (MON-185)

## Reguly z review krytyka

- MON-167: Gdy zewnętrzne narzędzie identyfikuje ten sam obiekt dwoma polami (np. `claude agents --json`: `id` 8 znaków vs `sessionId` UUID), w kodzie używaj wyłącznie tego, którym wołasz to narzędzie (`claude logs|attach|rm <id>`), i tej samej wartości w nazwach plików pośrednich (`<id>.log`); rozjazd jest cichy i objawia się brakiem wyniku, nie błędem. Wejście z pliku generowanego przez LLM/skill to granica systemu: waliduj kształt (dict, wymagane klucze) i zwracaj kod 2 z komunikatem, nie traceback.
- MON-190: Każda wartość z wyjścia zewnętrznego narzędzia i każdy wyjątek subprocessu (także `subprocess.TimeoutExpired`, który nie dziedziczy po OSError) jest obsłużony przed użyciem zgodnie z obiecanym kodem wyjścia, nigdy tracebackiem.
- MON-184: Flagę środowiskową czytasz istniejącym helperem pliku (`flag()`), nigdy własnym inline-parserem o innej semantyce. Nowa gałąź decyzyjna hooka wymaga testu, a gdy `tests/**` jest poza Twoim przydziałem - jawnego zgłoszenia do Team Managera, żeby trafiła do zakresu agenta testowego; kod bez testu nie zamyka się sam. Twierdzenie o zgodności z dokumentacją zewnętrzną ("format confirmed", "literal quotes") wymaga, żeby cytat z URL i datą leżał w repozytorium - sama parafraza w docstringu to fałszywa atrybucja.
