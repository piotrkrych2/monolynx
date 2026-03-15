---
name: Edit tool reliability on large files
description: How to reliably use the Edit tool on mcp_server.py (3000+ lines) without "file modified" errors
type: feedback
---

`mcp_server.py` is a very large file (~3100+ lines). The Edit tool tracks a hash/version of the last read state. If you read the file multiple times in one session with different `offset` parameters, the internal cache can become stale and every subsequent Edit attempt fails with "File has been modified since read".

**How to apply:**
1. Always do a final targeted `Read` immediately before the `Edit` call — read exactly the section you plan to replace.
2. Copy the exact text from that final Read output into `old_string` — do not rely on text from earlier reads in the same session.
3. If Edit fails with "file modified", do NOT retry immediately. Do one fresh `Read` of the target section, then retry the Edit once with the freshly-read text.
4. Avoid doing many Read calls at different offsets before an Edit — each read may update the cached version and cause confusion.

**Why:** The Edit tool compares `old_string` against the current file content AND verifies the file hasn't changed since the last read. With large files and multiple reads, the cached "last read" timestamp/hash diverges from what was actually last read.
