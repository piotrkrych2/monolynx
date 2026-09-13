# Agent Memory — Backend Developer

## Memory Index

- [patterns_mcp_compact_output.md](patterns_mcp_compact_output.md) — MCP tools returning compact strings instead of list[dict] to save tokens
- [patterns_edit_tool.md](patterns_edit_tool.md) — How to reliably use the Edit tool on large files
- [patterns_xml_comment_double_hyphen.md](patterns_xml_comment_double_hyphen.md) — XML comment with "--" (CLI flags) in provenance header breaks the whole fixture, not just the comment

## Reguly z review krytyka

- MON-167: Gdy zewnętrzne narzędzie identyfikuje ten sam obiekt dwoma polami (np. `claude agents --json`: `id` 8 znaków vs `sessionId` UUID), w kodzie używaj wyłącznie tego, którym wołasz to narzędzie (`claude logs|attach|rm <id>`), i tej samej wartości w nazwach plików pośrednich (`<id>.log`); rozjazd jest cichy i objawia się brakiem wyniku, nie błędem. Wejście z pliku generowanego przez LLM/skill to granica systemu: waliduj kształt (dict, wymagane klucze) i zwracaj kod 2 z komunikatem, nie traceback.
