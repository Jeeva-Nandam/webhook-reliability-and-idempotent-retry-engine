# Webhook Reliability & Idempotent Retry Engine

## Problem

External systems (payment providers, GitHub, SaaS apps) deliver webhook
events over an unreliable network. Delivery can be duplicated, delayed,
retried by the sender, or interrupted by downstream failures and worker
crashes. Naively processing whatever arrives risks duplicate side effects,
lost events, or a system that hammers an already-struggling downstream
service.

## Solution

A generic webhook ingestion and processing engine that:
- Stores every event durably with a database-enforced idempotency key.
- Processes events asynchronously so ingestion is never blocked on
  downstream latency.
- Retries transient failures with exponential backoff + jitter, up to a
  configurable limit.
- Moves permanently-failing events to a `DEAD` state for manual review/retry.
- Keeps a full, append-only history of every processing attempt.

Payments are used only as an example payload; nothing is payment-specific.

## Architecture

```
Provider --HTTP POST--> FastAPI --insert--> PostgreSQL
                            |
                        enqueue
                            v
                          Redis --> Celery Worker --> (simulated) downstream
                                          |
                                   update state / schedule retry
                                          v
                                     PostgreSQL
                                          ^
                                          |
                                  Celery Beat (sweeps due retries every 5s)
```

See `docs/HLD.md` and `docs/LLD.md` for the full component and module
breakdown.

## Tech Stack

FastAPI, Pydantic, SQLAlchemy 2.x, Alembic, PostgreSQL, Celery, Redis,
Pytest/HTTPX, Ruff/Black, Docker/Compose, GitHub Actions, React + Vite.

## Project Structure
```
webhook-reliability-engine/
├── docker-compose.yml
├── README.md
├── backend/
│   ├── .env
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── pyproject.toml
│   ├── pytest.ini
│   ├── requirements.txt
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   └── routes/
│   │   │       ├── __init__.py
│   │   │       ├── health.py
│   │   │       ├── metrics.py
│   │   │       ├── test_downstream.py
│   │   │       └── webhooks.py
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   ├── logging.py
│   │   │   └── security.py
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── database.py
│   │   │   └── models/
│   │   │       ├── __init__.py
│   │   │       ├── webhook_attempt.py
│   │   │       └── webhook_event.py
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   └── webhook.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── downstream_service.py
│   │   │   ├── retry_service.py
│   │   │   └── webhook_service.py
│   │   └── workers/
│   │       ├── __init__.py
│   │       ├── celery_app.py
│   │       └── webhook_tasks.py
│   ├── migrations/
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   │       └── 0001_initial_schema.py
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py
│       ├── integration/
│       │   ├── __init__.py
│       │   ├── test_idempotency.py
│       │   ├── test_retry_flow.py
│       │   └── test_webhook_api.py
│       └── unit/
│           ├── __init__.py
│           ├── test_retry_backoff.py
│           ├── test_signature.py
│           └── test_state_machine.py
├── docs/
│   ├── HLD.md
│   ├── LLD.md
│   ├── INTERVIEW_PREP.md
│   └── 01-ARCHITECTURE.md
└── frontend/
    ├── Dockerfile
    ├── index.html
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── App.jsx
        ├── index.css
        ├── main.jsx
        ├── components/
        │   ├── EventDetail.jsx
        │   ├── EventList.jsx
        │   ├── MetricsSummary.jsx
        │   └── StatusBadge.jsx
        ├── pages/
        │   └── Dashboard.jsx
        └── services/
            └── api.js
```
## System Flow

1. `POST /api/v1/webhooks` — verify HMAC signature, validate schema, insert
   (idempotently), enqueue a Celery task, return `202` immediately.
2. Celery worker claims the event (atomic compare-and-swap), calls the
   simulated downstream service, records the attempt.
3. Success → `SUCCESS`. Failure → `FAILED` + `next_retry_at` computed via
   exponential backoff, or `DEAD` if retries are exhausted.
4. Celery Beat periodically re-enqueues `FAILED` events whose
   `next_retry_at` has passed.
5. An administrator can manually retry a `DEAD` event via
   `POST /api/v1/webhooks/{event_id}/retry`.

## Database Schema

**`webhook_events`**: `id`, `event_id` (UNIQUE), `event_type`, `payload`
(JSONB), `status`, `retry_count`, `max_retries`, `last_attempt_at`,
`next_retry_at`, `processed_at`, `error_message`, `created_at`, `updated_at`.
Indexed on `event_id`, `status`, and the composite `(status, next_retry_at)`
used by the retry scheduler.

**`webhook_attempts`**: append-only log of every attempt — `attempt_number`,
`status`, `started_at`, `completed_at`, `response_code`, `error_message` —
kept separate from `webhook_events` so full attempt history is preserved
rather than overwritten.

## State Machine

```
PENDING -> PROCESSING -> SUCCESS            (terminal)
                       -> FAILED -> (retries left) -> PROCESSING
                                 -> (exhausted)     -> DEAD
DEAD -> PENDING   (manual retry only)
```

Transitions are validated in code (`app/db/models/webhook_event.py`,
`ALLOWED_TRANSITIONS`) — an invalid transition raises rather than silently
corrupting state.

## Retry Strategy

`delay = min(base_delay * 2^retry_count, max_delay) + jitter`

Exponential growth prevents hammering a struggling downstream service;
jitter prevents many events that failed simultaneously from all retrying
in the same instant (thundering herd). Retry scheduling is computed
explicitly in `app/services/retry_service.py` rather than relying on
Celery's built-in autoretry, so the math and state transitions stay visible.

## Idempotency Strategy

Idempotency is enforced by a PostgreSQL `UNIQUE(event_id)` constraint, not
by a `SELECT`-then-`INSERT` check (which has a race condition under
concurrent requests). The application attempts the insert directly; a
unique-violation means "this event already exists," and the existing row
is returned instead of creating a duplicate.

## Concurrency Handling

Two distinct concurrency problems:
- **Duplicate concurrent ingestion** of the same `event_id` → resolved by
  the database unique constraint.
- **Duplicate concurrent processing** of the same row by two workers →
  resolved by an atomic `UPDATE ... WHERE status IN ('PENDING','FAILED') ...
  RETURNING` compare-and-swap claim, so only one worker successfully
  transitions the row to `PROCESSING`.

## Security

HMAC-SHA256 signatures over the raw request body, verified with
`hmac.compare_digest` for timing-safe comparison. The secret lives in an
environment variable (`WEBHOOK_SECRET`), never committed to source control
— see `.env.example`.

## API Documentation

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/webhooks` | Ingest a webhook event |
| GET | `/api/v1/webhooks` | List events (`?status_filter=&page=&page_size=`) |
| GET | `/api/v1/webhooks/{event_id}` | Event detail + attempt history |
| POST | `/api/v1/webhooks/{event_id}/retry` | Manually retry a DEAD event |
| GET | `/api/v1/health` | Liveness check |
| GET | `/api/v1/metrics/summary` | Aggregate counts + success rate |
| POST | `/api/v1/test/process` | Simulated downstream (`success`/`error_500`/`timeout`/`random`) |

Interactive docs available at `/docs` once the backend is running (FastAPI
auto-generated Swagger UI).

## Running Locally

### With Docker Compose (recommended)

```bash
docker compose up --build
```

- Backend: http://localhost:8000 (docs at `/docs`)
- Frontend dashboard: http://localhost:5173
- Postgres: localhost:5432, Redis: localhost:6379

The `backend` service runs `alembic upgrade head` before starting Uvicorn,
so the schema is created automatically on first boot.

### Without Docker (backend only)

```bash
cd backend
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
# in another terminal:
celery -A app.workers.celery_app worker --loglevel=info
# in a third terminal (for scheduled retries):
celery -A app.workers.celery_app beat --loglevel=info
```

Requires a running PostgreSQL and Redis reachable at the URLs in `.env`.

## Testing

```bash
cd backend
pytest -q
```

The suite (33 tests) runs against an in-memory SQLite database and mocks
the Celery `.delay()` call, so it requires no external services — this is
what runs in CI. Docker Compose additionally exercises the system against
real Postgres/Redis end-to-end.

**Unit tests** (`tests/unit/`) test pure functions in isolation — state
machine transition rules, backoff math, HMAC verification — with no
database or HTTP involved.

**Integration tests** (`tests/integration/`) exercise the service layer and
FastAPI routes together with a real (in-memory) database, verifying
idempotency, the claim/compare-and-swap concurrency guard, the full
retry-to-DEAD lifecycle, and the HTTP API end-to-end.

## Failure Simulation

`POST /api/v1/test/process` (or the `downstream_mode` used internally by
the worker) supports `success`, `error_500`, `timeout`, and `random`
(≈60% success / 25% 500 / 15% timeout) so the retry system can be
demonstrated without a real third-party integration.

## Example Scenarios

- **Duplicate webhook**: POST the same `event_id` twice → only one row is
  created; the second response says so explicitly.
- **Concurrent duplicates**: two simultaneous POSTs for the same
  `event_id` → the database unique constraint guarantees only one insert
  succeeds.
- **Transient failure then recovery**: an event fails, is retried
  automatically after backoff, and eventually succeeds — visible in its
  attempt history.
- **Exhausted retries**: an event that keeps failing moves to `DEAD` after
  `max_retries` attempts and appears on the dashboard for manual retry.

## Future Improvements

- Per-event-type retry policies instead of a single global default.
- Downstream idempotency-key passthrough for a real integration.
- Dead-letter alerting (e.g. paging when DEAD count crosses a threshold).
- Rate limiting / backpressure on ingestion at very high volume.
- High-availability Celery Beat (currently a single scheduler instance).

## Engineering Challenges

- Getting the idempotency check to actually be race-safe under concurrency
  required abandoning the natural-seeming `SELECT`-then-`INSERT` pattern in
  favor of trusting the database constraint.
- Making the same business logic correctly callable from both an HTTP
  request handler and an asynchronous Celery task pushed the retry/state
  logic out of both layers and into a shared, dependency-light service
  module.
- Supporting the test suite without requiring a live Postgres/Redis for
  every run meant making the JSONB column and BigInteger primary keys
  dialect-portable (SQLite for fast tests, PostgreSQL in Docker/production).

---

For deep dives, see `docs/HLD.md`, `docs/LLD.md`, and
`docs/INTERVIEW_PREP.md`.
