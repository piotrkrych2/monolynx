---
name: feedback-storage-calls-need-exception-mapping
description: Every external storage call (MinIO/S3) in a new HTTP handler must be wrapped in try/except mapped to the endpoint's contracted HTTP status, especially on unauthenticated endpoints
metadata:
  type: feedback
---

Code review finding on MON-201 (`src/monolynx/blog.py`, public `/blog/{slug}`): calling
`get_page_content(page)` (which calls MinIO's `get_object` under the hood, via
`services/wiki.py` -> `services/minio_client.py::get_markdown`) with no try/except let a
storage failure (deleted object, MinIO down, bad `minio_path`) escape as an uncaught
exception -> bare 500 on a public, unauthenticated endpoint.

**Why:** every other caller of `get_page_content()` in the codebase sits behind a session +
RBAC, so an uncaught storage exception there is an internal-only 500 a logged-in user can
report. On an anonymous public endpoint, an uncaught exception is the *only* signal a random
visitor gets — no context, no way to distinguish "page doesn't exist" from "storage is down".

**How to apply:** wrap the storage call, `logger.exception(...)`, then `raise
HTTPException(status_code=<contracted status>, detail=...)`. On `/blog/{slug}` the contract
maps both "page not found" and "storage failed" to 404 (not 503) — an anonymous reader must
not be able to distinguish the two cases, same reasoning as 404-not-403 for private pages.
Pick the status the endpoint's own contract already uses for its primary not-found case,
don't invent a new one.

This applies to ANY new HTTP handler wrapping a MinIO/S3/external-storage call — check this
before shipping, not just for blog-like read paths. See [[patterns_nh3_sanitization]] and
[[patterns_partial_unique_index_no_project_scope]] for the sibling MON-201 patterns.
