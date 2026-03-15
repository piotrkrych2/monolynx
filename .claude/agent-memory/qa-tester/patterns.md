---
name: MCP Server test patterns
description: Established patterns for testing MCP tools in test_mcp_server.py
type: project
---

## MCP tool test structure

Every MCP tool test class follows this pattern:

```python
@pytest.mark.unit
class TestSomeTool:
    async def test_happy_path(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await some_tool(ctx, mcp_project.slug, ...)
        assert result["field"] == expected
```

## Key fixtures
- `mcp_user` — test User created with unique email (uuid hex suffix)
- `mcp_project` — test Project with unique slug (uuid hex suffix)
- `mcp_member` — ProjectMember linking user to project with role="owner"
- `mock_factory` — replaces `async_session_factory`; uses `db_session` but replaces `commit()` with `flush()` so outer transaction rollback still works
- `mock_verify` — AsyncMock returning `mcp_user`; replaces `verify_mcp_token`

## Monitoring-specific gotcha
- For any monitor happy-path test, also patch `monolynx.mcp_server._is_url_safe` with `return_value=None`
- SSRF error test: patch `_is_url_safe` with `return_value="some error string"`, expect `ValueError("Niedozwolony URL")`

## EXPECTED_TOOLS list
- In `test_mcp_server.py` there is a `EXPECTED_TOOLS` list used by `TestMcpToolRegistration`
- When a new MCP tool is added to `mcp_server.py`, it MUST also be added to `EXPECTED_TOOLS`
- The docstring on `test_list_tools_returns_all_tools` has the count — update it too

## Validation error messages (create_monitor)
- Empty name: "Nazwa monitora nie moze byc pusta"
- Bad URL scheme: "URL musi zaczynac sie od http:// lub https://"
- SSRF blocked: "Niedozwolony URL"
- Bad interval_unit: "interval_unit musi byc jednym z"
- interval_value out of range: "interval_value musi byc liczba od 1 do 60"
- Monitor limit: "Osiagnieto limit 20 monitorow na projekt"
