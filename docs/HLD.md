# High-Level Design

## Components

- **FastAPI service** — stateless HTTP layer. Validates, authenticates
  (HMAC), performs the idempotent insert, enqueues a Celery task, returns
  immediately. Horizontally scalable (any number of replicas behind a load
  balancer) because it holds no in-memory state.
- **PostgreSQL** — single source of truth for event lifecycle state. The
  `UNIQUE(event_id)` constraint is the actual idempotency mechanism.
- **Redis** — message broker for Celery, and result backend. Ephemeral by
  design: if Redis data is lost, the durable state in Postgres is
  unaffected, though in-flight retries would need to be re-swept.
- **Celery workers** — stateless processes that pull tasks and run
  `process_webhook_event`. Horizontally scalable by adding more worker
  processes/containers.
- **Celery Beat** — a single scheduler process that periodically enqueues
  due retries. Only one Beat instance should run at a time (it is the one
  intentionally-non-horizontally-scaled component in this design).

## Data Flow

1. Provider → FastAPI (`POST /webhooks`) → Postgres insert → Redis enqueue
   → 202 response.
2. Redis → Celery worker → Postgres claim (`PENDING/FAILED → PROCESSING`)
   → simulated downstream call → Postgres update (`SUCCESS` or `FAILED` +
   `next_retry_at`).
3. Celery Beat → Postgres query (`FAILED AND next_retry_at <= now()`) →
   Redis enqueue → back to step 2.

## Scalability

- **FastAPI**: scales horizontally behind a load balancer; no shared
  in-process state.
- **Celery workers**: scale horizontally; `task_acks_late=True` plus the
  compare-and-swap claim query make redelivery/duplicate pickup safe.
- **PostgreSQL**: the eventual bottleneck at high volume. The
  `(status, next_retry_at)` index keeps the scheduler query cheap; at very
  high volume, partitioning `webhook_events` by `created_at` and moving
  `SUCCESS`/`DEAD` rows to a cold table would help.
- **Redis**: can be clustered; at our volumes a single instance with
  persistence (AOF) is enough.

## Bottlenecks / Single Points of Failure

- **Celery Beat** is a SPOF for *scheduling* retries (not for ingestion or
  first-attempt processing). Running it in an active/passive pair with a
  distributed lock removes this.
- **PostgreSQL** is the ultimate SPOF for all state; mitigated in production
  with managed HA Postgres (e.g. read replica + automatic failover).
- A **downstream outage** doesn't take down our system — retries simply
  back off — but it will grow the FAILED queue; alerting on queue depth is
  important at scale.

## Horizontal Scaling Path (100/day → 1M/day)

See README.md's "Scalability Discussion" section for the concrete plan at
each order of magnitude.
