---
name: patterns-fastapi-v2-gotchas
description: FastAPI NoReturn route registration bug, ruff UP046 forcing PEP 695 pydantic generics (MON-140)
metadata:
  type: reference
---

Three reusable findings from building the `api/v2` skeleton (MON-140, `src/monolynx/api/v2/`).

**1. `-> NoReturn` on a FastAPI route breaks registration even with `include_in_schema=False`.**
A catch-all endpoint that always raises (e.g. `async def catch_all(...) -> NoReturn: raise ApiV2Error(...)`)
makes FastAPI's route registration crash with `fastapi.exceptions.FastAPIError: Invalid args for
response field!` when it tries to build a pydantic response field from the return annotation.
`include_in_schema=False` does NOT prevent this - it only hides the route from the OpenAPI schema,
it doesn't stop FastAPI from trying to build the response model at route-registration time. Fix:
add `response_model=None` explicitly to the route decorator alongside the `-> NoReturn` annotation.

**2. `Generic[T]` on a pydantic `BaseModel` trips ruff `UP046`** under `target-version = "py312"`
(this project's `pyproject.toml`). Ruff wants PEP 695 syntax instead:
`class PaginatedResponse[T](BaseModel): ...` (no `TypeVar`/`Generic` import needed). Verified pydantic
2.11 subscripts (`PaginatedResponse[Item](...)`) and validates item types correctly with this syntax -
it's a safe drop-in, not just a lint-silencer.

**3. Do NOT sanity-check FastAPI wiring in a local host venv.** CLAUDE.md forbids running
application Python outside Docker ("Never run Python commands locally"). In MON-140 a throwaway
host venv caught finding 1 above, but the sanctioned route is to report the risk to the Team
Manager, who runs the tests in the worktree variant from the `toolchain` wiki page (TM decision,
MON-140).

See also [patterns_self_referential_m2m_viewonly](patterns_self_referential_m2m_viewonly.md) for
another instance of "verify a modern-library behavior claim against the actual installed version
before asserting it in a report."
