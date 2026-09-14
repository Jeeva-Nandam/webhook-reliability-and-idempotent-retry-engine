# Low-Level Design

## Modules and Responsibilities

**`app/core/security.py` — `verify_signature` / `compute_signature`**
Pure functions. HMAC-SHA256 over the raw request body using a shared secret.
Uses `hmac.compare_digest` for timing-safe comparison. No dependencies on
FastAPI or the database — trivially unit-testable.

**`app/services/webhook_service.py` — the core business logic module**
- `ingest_event`: idempotent insert (insert-then-catch-IntegrityError
  pattern, not check-then-act).
- `claim_event_for_processing`: atomic compare-and-swap claim via
  `UPDATE ... WHERE status IN (...) RETURNING`.
- `mark_success` / `mark_failed_and_schedule_retry` / `manual_retry`: state
  transitions, each validated against `ALLOWED_TRANSITIONS`.
- `record_attempt`: appends to `webhook_attempts`.
- `list_events`, `get_due_retries`, `get_metrics_summary`: read paths.

This module has zero FastAPI or Celery imports — it only depends on
SQLAlchemy. That's what makes it callable identically from an API route, a
Celery task, and a plain pytest test.

**`app/services/retry_service.py` — backoff math**
Pure functions (`compute_backoff_seconds`, `compute_next_retry_at`,
`has_retries_remaining`). No I/O, no side effects — deterministic given a
seeded RNG, trivially unit-tested.

**`app/services/downstream_service.py` — `call_downstream`**
Stands in for a real external integration (Stripe, etc). Encapsulated
behind a single function so swapping in a real HTTP client later touches
one file.

**`app/workers/webhook_tasks.py` — `process_webhook_event`, `sweep_due_retries`**
Orchestrates the above: claim → call downstream → record attempt → update
state. Thin — almost all logic it calls lives in the services layer, so the
task itself is mostly sequencing and logging.

**`app/api/routes/webhooks.py` — thin HTTP handlers**
Parses the request, calls `security` + `webhook_service`, translates
results to HTTP status codes / Pydantic response models. Contains no
business rules of its own beyond "if service returns X, respond with HTTP Y."

## Class/Module Relationships

```
FastAPI route (webhooks.py)
      │ calls
      ▼
webhook_service.py ──────► db/models (WebhookEvent, WebhookAttempt)
      ▲                          ▲
      │ calls                    │ ORM
Celery task (webhook_tasks.py) ──┘
      │ calls
      ▼
retry_service.py (pure functions)
downstream_service.py (simulated I/O)
```

Both the HTTP route and the Celery task depend on `webhook_service`, never
on each other. This means the retry/idempotency logic is defined exactly
once and exercised identically whether triggered by an HTTP request or a
background sweep.
