# Interview Preparation

## Explanations at Different Lengths

**10-second:** A webhook receiver that never processes the same event
twice and automatically retries failures with exponential backoff, backed
by Postgres, Redis, and Celery.

**30-second:** External systems send webhook events that can arrive
duplicated, fail transiently, or fail permanently. This project stores
every event durably with a database-enforced idempotency key, processes it
asynchronously via Celery so the HTTP response is never blocked, and
retries failures with exponential backoff + jitter up to a configurable
limit before moving the event to a dead-letter state for manual review.

**1-minute:** Webhook delivery over HTTP is inherently unreliable — senders
retry on timeout, networks drop responses, and workers can crash mid-task.
This project treats those failure modes as first-class design constraints
rather than edge cases. A FastAPI endpoint validates and durably stores
each event with a unique constraint on its external event_id, which is the
actual idempotency guarantee (not an application-level check, which has a
race condition under concurrency). Processing happens asynchronously in
Celery workers so the API responds in milliseconds. Failures are retried
with `delay = min(base * 2^retry_count, max_delay)` plus jitter, and events
that exhaust their retry budget move to a DEAD state visible on a small
React dashboard, where an administrator can manually retry them. An
append-only attempts table preserves the full history of every attempt for
debugging.

**2-minute technical:** [Combine the above with the state machine diagram,
the compare-and-swap claim query for concurrency safety, and the
discussion in §27 of the original spec about why "exactly-once processing"
is fundamentally hard — response can be lost after a downstream success,
which is why downstream idempotency keys matter as much as our own.]

## Q&A

**Why did you build this?** To go deeper than CRUD apps and demonstrate the
concepts that actually come up in backend/distributed-systems interviews:
idempotency, retries, race conditions, and asynchronous processing.

**What problem does it solve?** Reliable ingestion and processing of
at-least-once-delivered events, without duplicate side effects and without
losing events to transient failures.

**What is idempotency?** The property that performing an operation multiple
times has the same effect as performing it once. Here, it's enforced by a
database-level unique constraint on the event's external ID.

**Why is payment only an example?** The engine is generic — it works for
any webhook source (GitHub, Shopify, Slack). Payments just make the failure
scenarios concrete and relatable.

**Why do webhooks get duplicated?** Senders implement their own
at-least-once delivery guarantees: if they don't get a fast 2xx, they
retry, which can produce true duplicates, and network conditions can also
produce near-simultaneous concurrent duplicates.

**Why do we need retries?** Many failures are transient (a brief timeout,
a 500 from an overloaded service) and would succeed if attempted again
after a short delay.

**Why exponential backoff?** Fixed-interval retries from many failed events
can pile more load onto an already-struggling downstream service. Backing
off exponentially reduces load quickly while still eventually retrying.

**Why not retry immediately?** An immediate retry is likely to hit the same
transient condition again and wastes resources; a short wait raises the
odds the underlying issue (timeout, overload) has cleared.

**Why use Redis?** As the Celery broker — a fast, simple, well-supported
transport for "here's a task, run it" messages between the API and workers.

**Why use Celery?** It's a mature Python distributed task queue that
handles serialization, worker pools, and periodic scheduling (Beat) without
building all of that from scratch, while leaving business logic (retry math,
state transitions) fully visible in application code.

**Why PostgreSQL?** Strong consistency guarantees (ACID, unique
constraints, atomic conditional updates) are exactly what idempotency and
safe concurrent claiming require.

**How did you handle race conditions?** Two specific cases: (1) concurrent
duplicate ingestion is resolved by a DB-level unique constraint, not an
application check; (2) concurrent workers claiming the same event is
resolved by an atomic `UPDATE ... WHERE status IN (...) RETURNING`
compare-and-swap.

**What happens if the worker crashes?** With `task_acks_late=True`, an
unacknowledged task is redelivered to another worker. Because claiming is
idempotent (the compare-and-swap only succeeds if the event is still
PENDING/FAILED), redelivery is safe — a stale in-flight event just won't be
re-claimed if it already moved to SUCCESS.

**What happens if downstream succeeds but the response is lost?** This is
the hardest case in the system, discussed at length in the README — the
worker retries because it saw a timeout, but the operation already
happened downstream, risking a duplicate side effect. This is why passing
our idempotency key through to the *downstream* call matters as much as
our own idempotency.

**What is a dead-letter queue?** A holding state/queue for
messages/events that have exhausted their retry budget, so they stop
consuming resources automatically but remain available for inspection or
manual reprocessing instead of being silently dropped.

**How would you scale this?** See the README's scalability discussion —
briefly: horizontally scale FastAPI and Celery workers behind a load
balancer, keep Postgres as the consistency anchor with proper indexing/
connection pooling, and only reach for partitioning/sharding once volume
genuinely requires it.

**What is the difference between synchronous and asynchronous processing?**
Synchronous: the caller waits for the operation to fully complete before
getting a response. Asynchronous: the caller is acknowledged immediately,
and the actual work happens independently, later, out of band.

**How do you guarantee duplicate events aren't processed twice?** The
unique constraint prevents two rows for the same event_id; the
compare-and-swap claim prevents two workers processing the same row
concurrently. Together these prevent duplicate *processing* of a given
event_id — but see the downstream-idempotency caveat above for duplicate
*side effects*.

**What are the limitations of your implementation?** Single Celery Beat
instance is a scheduling SPOF; simulated downstream rather than a real
integration; no dead-letter alerting/paging; no per-event-type retry
policy; no rate limiting on ingestion.

## Five Realistic Follow-Up System Design Questions

1. How would you detect and alert on a growing backlog of DEAD events
   before an operator has to notice manually?
2. If a single downstream partner degrades and drives most of your retries,
   how would you avoid its retries starving processing capacity for other
   event types?
3. How would you make Celery Beat itself highly available without risking
   duplicate retry sweeps?
4. How would you support per-tenant or per-event-type retry policies
   (different max_retries or backoff curves) without a schema migration
   for every new policy?
5. If you needed true exactly-once side effects (not just exactly-once
   *processing* on your side), how would you design the downstream call to
   guarantee that even under network ambiguity?
