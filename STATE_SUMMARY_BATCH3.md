# Batch 3 Final State Summary

## Completed (T006, T007)

### Files Modified
- `app/services/scraper_srv.py`
  - Added `except errors.ChatAdminRequiredError` catch block after `FloodWaitError`
  - Logs restricted bot detection and skips Telethon history dump (T006 early fallback)
  - Preserved rest of file structure (Git restore + minimal edit)

- `app/workers/tasks/scanner_tasks.py`
  - Added `scanner.retry_cold` Celery task and async helper function (T007)
  - Task queries `discovered_credentials` for retryable tokens gated ≥6h ago
  - Marks tokens as non-retryable, calls `enrich_credential.delay(id)`
  - Restored Batch 2 T002 fix (undefined `result_msg`)

- `app/workers/celery_app.py`
  - Added beat schedule entry: `retry-cold-12hours` (every 12 hours at minute 50)

### Validation
- All changed files pass `python -m py_compile` (no syntax errors)
- `ruff format` applied to ensure style consistency
- T006/T007 code additions minimal and isolated

## Pending (T008 - T013)

Per `tasks.md`:
- T008: RLS restriction and safe projection (requires DB change)
- T009: Monitor/admin endpoint auth gating
- T010: Observability真实性 (audit/metrics/breaker integration)
- T011: CSV import behavior correction
- T012: Test alignment with current contract
- T013: Frontend documentation rewrite (remove unrelated templates)

## Artifacts Updated
- `tasks.md`: T006 → Complete, T007 → Complete
- `bugfix.md`: B011 → Fixed (earlier update retained)

## Git Status
```
M app/services/scraper_srv.py    # T006 restricted bot early fallback
M app/workers/celery_app.py       # T007 beat schedule
M app/workers/tasks/scanner_tasks.py  # T002 fix (result_msg) + T007 retry task
?? bugfix.md
?? design.md
?? tasks.md
```

## Next Recommended Step
Proceed with T008 (RLS/projection) in a separate code window with DB changes acknowledged; alternatively, mark T008 as deferred and continue with T009-T013 if they are code-only.
