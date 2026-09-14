# Webhook Reliability & Idempotent Retry Engine
## Phase 1 — Architecture & Design

---

## 1. Project Overview

This is a **generic webhook ingestion platform**. It receives HTTP events from
external systems, guarantees each event is processed *at most conceptually
once* (we'll be precise about that phrase later), and automatically retries
events that fail due to transient errors — without ever hammering a downstream
service that is already struggling.

Payments are just the example payload. The same engine would work for GitHub
push events, Slack events, Shopify order events, etc. Nothing in the design
should be payment-specific.

## 2. Problem Statement

Webhooks are delivered over an unreliable network, by a sender that has its
own retry logic, into a receiver that may crash mid-processing. That
combination produces three concrete failure modes we must design for:

1. **Duplicate delivery** — the sender retries and we receive the same
   `event_id` twice (or receive two truly simultaneous copies).
2. **Transient downstream failure** — our processing step calls another
   service (or does work) that fails temporarily (timeout, 500, connection
   reset) but would succeed if retried later.
3. **Permanent failure** — some events will never succeed no matter how many
   times we retry (bad data, downstream service permanently rejecting it).
   These need to stop retrying and become visible for a human to inspect.

The system's job is to make all three of these boring and observable instead
of silent data-loss or duplicate-side-effect incidents.

## 3. High-Level Architecture

```
 External Provider
        │  HTTP POST /api/v1/webhooks
        ▼
 ┌─────────────────────────────┐
 │   FastAPI (API layer)       │
 │  - validate schema          │
 │  - verify HMAC signature    │
 │  - INSERT with UNIQUE       │
 │    constraint on event_id   │
 │  - enqueue Celery task      │
 │  - return 200/201 fast      │
 └───────────────┬─────────────┘
                 │ writes row
                 ▼
        ┌─────────────────┐
        │   PostgreSQL     │◄────────────┐
        │  webhook_events  │             │ reads/writes
        │  webhook_attempts│             │ event state
        └─────────────────┘             │
                 ▲                       │
                 │ enqueue task          │
                 ▼                       │
        ┌─────────────────┐             │
        │   Redis (broker) │             │
        └────────┬─────────┘             │
                 ▼                       │
        ┌─────────────────────────┐      │
        │   Celery Worker         │──────┘
        │  - load event by id     │
        │  - mark PROCESSING      │
        │  - call downstream      │
        │  - on success → SUCCESS │
        │  - on failure → FAILED, │
        │    schedule retry with  │
        │    backoff, or DEAD if  │
        │    max retries hit      │
        └─────────────────────────┘
```

**Component responsibilities:**

| Component | Responsibility | Responsibility it does NOT have |
|---|---|---|
| FastAPI | Fast validation, auth, durable write, enqueue | Does not process the event itself |
| PostgreSQL | Single source of truth for event state | Does not decide *when* to retry (worker does, but reads schedule from here) |
| Redis | Transport for "a task is ready to run" | Does not store the authoritative event state |
| Celery worker | All actual business logic / processing / retry scheduling | Does not accept HTTP traffic directly |

Why split API and worker at all? Because HTTP requests should return in
milliseconds. If the API thread did the downstream call itself, a slow or
down downstream service would pile up open connections on your webhook
endpoint and could cause the *sender* to see timeouts and retry, compounding
the problem. Decoupling "accept and store" from "process" is the single
most important architectural decision in this project.

## 4. Application / Request Flow

**Ingestion (synchronous, fast path):**
1. Provider POSTs to `/api/v1/webhooks`.
2. FastAPI verifies the `X-Webhook-Signature` header (HMAC).
3. Pydantic validates the JSON shape.
4. FastAPI attempts `INSERT INTO webhook_events (...)`. The `event_id` column
   has a `UNIQUE` constraint, so a duplicate insert raises an
   `IntegrityError` — we catch it and treat it as "already known", not an
   error.
5. If the insert succeeded, FastAPI enqueues a Celery task with the new row's
   primary key.
6. FastAPI returns `202 Accepted` immediately. It never waits on the worker.

**Processing (asynchronous, worker path):**
1. Celery worker pulls task from Redis.
2. Worker loads the row, transitions `PENDING → PROCESSING`.
3. Worker calls the (simulated) downstream service.
4. Success → `PROCESSING → SUCCESS`, record `processed_at`.
5. Failure → `PROCESSING → FAILED`, increment `retry_count`, compute
   `next_retry_at` using exponential backoff, and either re-queue for later
   or, if `retry_count >= max_retries`, transition to `DEAD`.

## 5. Database Schema

**`webhook_events`**

| column | type | notes |
|---|---|---|
| id | BIGSERIAL PK | internal surrogate key |
| event_id | TEXT, **UNIQUE, NOT NULL** | external idempotency key from the sender |
| event_type | TEXT NOT NULL | e.g. `payment.succeeded` |
| payload | JSONB NOT NULL | raw event body |
| status | TEXT NOT NULL | `PENDING`/`PROCESSING`/`SUCCESS`/`FAILED`/`DEAD` |
| retry_count | INTEGER NOT NULL DEFAULT 0 | attempts so far |
| max_retries | INTEGER NOT NULL DEFAULT 5 | ceiling before DEAD |
| last_attempt_at | TIMESTAMPTZ | when we last tried |
| next_retry_at | TIMESTAMPTZ | when the worker should try again |
| processed_at | TIMESTAMPTZ | set on SUCCESS |
| error_message | TEXT | last failure reason |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Indexes:
- `UNIQUE(event_id)` — this *is* our idempotency guarantee, enforced at the
  database level, not the application level (more in §7).
- `INDEX(status)` — the worker/scheduler frequently asks "give me all FAILED
  events" or "all PENDING events"; without this it's a full table scan.
- `INDEX(next_retry_at)` — the retry scheduler asks "which events are due for
  retry right now?" (`WHERE status = 'FAILED' AND next_retry_at <= now()`); a
  composite index on `(status, next_retry_at)` serves this directly.

**`webhook_attempts`** (added in Phase 12, mentioned now for completeness)

| column | type | notes |
|---|---|---|
| id | BIGSERIAL PK | |
| webhook_event_id | BIGINT FK → webhook_events.id | |
| attempt_number | INTEGER | 1, 2, 3... |
| status | TEXT | outcome of that specific attempt |
| started_at / completed_at | TIMESTAMPTZ | duration = completed - started |
| response_code | INTEGER | from the simulated downstream call |
| error_message | TEXT | |

We keep this as a *separate append-only table* rather than overwriting one
"last error" field, because a single mutable field destroys history — you
couldn't answer "why did this eventually succeed after 4 failures?" or
compute mean-time-to-recovery. Append-only attempt logs are how real payment
systems (Stripe, etc.) let you debug a specific event's whole timeline.

## 6. Event State Machine

```
PENDING ──► PROCESSING ──► SUCCESS   (terminal, happy path)
               │
               └────────► FAILED
                             │
                    retry_count < max_retries?
                        │            │
                       yes           no
                        │            │
                        ▼            ▼
                  next_retry_at   DEAD (terminal)
                  set, re-queued
                        │
                        ▼
                  PROCESSING (attempt N+1)
```

Rules we will enforce in code (not just in our heads):
- Only `PENDING` or `FAILED` rows may transition to `PROCESSING`.
- Only `PROCESSING` may transition to `SUCCESS` or `FAILED`.
- Only `FAILED` may transition to `DEAD`, and only when `retry_count >=
  max_retries`.
- `DEAD` may transition back to `PENDING` **only** via the explicit manual
  retry endpoint (§16 of your spec) — never automatically.
- `SUCCESS` and `DEAD` are terminal for the automatic system.

**INTERVIEW CONCEPT: explicit state machines.** Encoding this as an
enum + a transition-validating function (rather than just setting
`status = "SUCCESS"` wherever convenient) prevents an entire class of bugs
where a crashed/duplicated worker run corrupts state. We'll implement this as
a small `can_transition(from_state, to_state) -> bool` function used by every
write path.

## 7. Idempotency Design

**INTERVIEW CONCEPT: idempotency vs retry** — these are two different
questions and this project deliberately keeps them as two separate
mechanisms:
- Idempotency (API layer): "Have I already stored this event_id?"
- Retry (worker layer): "This attempt failed — should I try processing it
  again?"

The naive idempotency check is:
```sql
SELECT * FROM webhook_events WHERE event_id = 'evt_123';
-- if not found, INSERT
```
This has a **race condition**. Two concurrent requests for the same
`event_id` can both run the `SELECT`, both see "not found," and both proceed
to `INSERT`. Row locks from a `SELECT` don't protect you here because
nothing was locked — the row didn't exist yet.

The fix is to never trust the check-then-act pattern for correctness. We
rely on the database's `UNIQUE(event_id)` constraint instead:
1. Just attempt the `INSERT` directly (optionally after an optimistic
   `SELECT` purely as a fast-path optimization, not for correctness).
2. If the insert succeeds → this is a new event, proceed to enqueue it.
3. If the insert raises a unique-violation (`IntegrityError` /
   Postgres error code `23505`) → this is a duplicate, so just fetch the
   existing row and return its current status. No second Celery task is
   queued.

This means correctness comes from PostgreSQL's B-tree unique index, which is
enforced atomically regardless of how many concurrent connections try to
insert the same key — this is the one guarantee a database gives you that
application code cannot replicate without it.

We'll also touch on **why retries themselves can create duplicate side
effects downstream** even when our own idempotency is airtight (§9, case 9)
— e.g., our worker calls a payment gateway, the gateway charges the card,
but the response is lost to a network blip. Our worker sees "failure" and
retries, charging twice. This is why *downstream* idempotency keys
(passing our `event_id` as an idempotency key to the downstream call) matter
just as much as our own. We'll cover this in depth in the reliability
discussion phase.

## 8. Concurrency Strategy

Two concurrency problems, two different fixes:

1. **Concurrent duplicate webhook POSTs** (same `event_id` arriving twice at
   once) → solved by the `UNIQUE` constraint above, not by locking.
2. **Concurrent workers picking up the same event for processing** (e.g. a
   retry gets re-queued while an old task for the same event is still
   running, or two worker processes both poll for "due" retries) → solved by
   a conditional update pattern:
   ```sql
   UPDATE webhook_events
   SET status = 'PROCESSING', last_attempt_at = now()
   WHERE id = :id AND status IN ('PENDING', 'FAILED')
   RETURNING *;
   ```
   If this `UPDATE ... RETURNING` returns zero rows, some other worker
   already claimed it — this worker simply exits without processing. This
   is a **compare-and-swap** pattern implemented with plain SQL, using the
   `WHERE status IN (...)` clause as the "compare" and the `SET status =
   'PROCESSING'` as the "swap," all inside one atomic statement. It avoids
   needing explicit application-level locks (`SELECT ... FOR UPDATE`) for
   this case, though we'll discuss `FOR UPDATE` too since it's a common
   interview topic.

## 9. Retry / Backoff Design

`delay = min(base_delay * 2 ** retry_count, max_delay)`, with optional
random jitter added so that a batch of events that all failed at the same
moment (e.g., downstream had a 30-second outage) don't all wake up and
retry in the exact same instant, which would just recreate the thundering
herd we're trying to avoid. We compute `next_retry_at` at failure time and
store it in the row; a Celery Beat schedule (or periodic task) periodically
queries `WHERE status='FAILED' AND next_retry_at <= now()` and re-enqueues
those rows.

## 10. Folder Structure

```
webhook-reliability-engine/
├── backend/
│   ├── app/
│   │   ├── api/routes/        # thin HTTP handlers only
│   │   ├── core/               # config, security (HMAC), logging setup
│   │   ├── db/                 # SQLAlchemy engine/session + models
│   │   ├── schemas/             # Pydantic request/response models
│   │   ├── services/            # business logic (webhook_service, retry_service, downstream_service)
│   │   ├── workers/             # celery_app.py, webhook_tasks.py
│   │   └── main.py
│   ├── migrations/              # Alembic
│   ├── tests/{unit,integration}
│   ├── Dockerfile, requirements.txt, .env.example
├── frontend/                    # React dashboard (later)
├── docker-compose.yml
├── .github/workflows/ci.yml
└── README.md
```

Rationale: **routes** stay thin (parse request → call service → return
response) so business logic is unit-testable without spinning up HTTP.
**services** hold the actual rules (idempotency check, state transitions,
retry math) independent of FastAPI or Celery, so the same logic is callable
from an API route, a Celery task, or a test. **db/models** vs **schemas** is
the classic separation between your persistence shape (SQLAlchemy) and your
wire shape (Pydantic) — they will look similar but serve different masters
and will diverge over time (e.g. you won't expose `error_message` internals
the same way to every API consumer).

## 11. API Design Summary

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/webhooks` | ingest event |
| GET | `/api/v1/webhooks` | list, filter by status, paginate |
| GET | `/api/v1/webhooks/{event_id}` | detail + attempt history |
| POST | `/api/v1/webhooks/{event_id}/retry` | manual retry of a DEAD event |
| GET | `/api/v1/health` | liveness |
| GET | `/api/v1/metrics/summary` | counts + success rate |
| POST | `/api/v1/test/process` | simulated downstream (success/500/timeout/random) |

## 12. Development Roadmap (19 phases, from your spec)

We'll go in this order: architecture (done) → FastAPI skeleton → Postgres +
SQLAlchemy models → idempotency → state machine enforcement → naive
synchronous background processing (to feel the pain point) → introduce
Celery + Redis → exponential backoff retries → failure simulator →
concurrency hardening → HMAC security → attempt-history table →
observability/metrics → tests → Docker/Compose → React dashboard → GitHub
Actions → HLD/LLD docs → final README + interview prep.

---

### Check your understanding before we move to Phase 2

Quick question: suppose two identical webhook POSTs for `event_id=evt_1`
hit two different FastAPI worker processes at the *exact* same millisecond.
Walk through, step by step, what happens in the database and why only one
of them ends up enqueuing a Celery task. Try answering in your own words —
I'll correct/refine it before we start writing the FastAPI skeleton in
Phase 2.
