---
name: feedback-recheck-before-repeating-flag
description: before repeating a previously-flagged issue in a report, check the current file state (git diff) - another agent working in parallel may have already fixed it
metadata:
  type: feedback
---

MON-185: I flagged the same issue (`TestStop` in `test_sprint_run_backend.py`
using unmocked `pid=os.getpid()`, landmine for `os.kill`) in my iteration-1 AND
iteration-2 reports to team-lead/krytyk. Team-lead's final message: "Zgloszenie
o `TestStop` powtorzyles w raportach po tym, jak qa-tester juz je naprawil -
przy pracy rownoleglej sprawdzaj biezacy stan pliku (`git diff`), zanim
powtorzysz zgloszenie z poprzedniej rundy."

**Why:** in a multi-agent team session, other agents (qa-tester in this case)
work on their own files concurrently between my rounds. A finding from an
earlier round can already be resolved by the time I write the next report -
repeating it as still-open is stale information that wastes the reader's
attention and can look like I didn't check.

**How to apply:** before restating a previously-flagged issue about a file I
don't own (e.g. a test file outside my assigned scope, like `tests/**` here),
run `git diff`/re-read the specific lines first to confirm the issue is still
present, rather than copy-pasting the flag from my own earlier message. This
is a narrower case of [[project_pytest_shared_testdb_collisions]] (verify
before assuming something is still true in a concurrently-edited repo).
