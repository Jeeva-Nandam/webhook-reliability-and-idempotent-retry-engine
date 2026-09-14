"""
Celery application instance.

Celery is a distributed task queue. Redis here plays the role of the
"broker": when FastAPI wants work done asynchronously, it doesn't call a
Python function directly -- it serializes a message ("run task X with these
arguments") and pushes it onto a Redis list/stream. One or more separate
Celery *worker* processes are constantly watching that queue, pop messages
off it, and execute the corresponding Python function in their own process.

This is what actually decouples "accept the HTTP request" from "do the
work": the FastAPI process and the worker process(es) don't even have to be
running on the same machine.
"""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "webhook_engine",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.webhook_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # If a worker process dies mid-task, redeliver the task rather than
    # silently losing it. Combined with our idempotent claim_event_for_processing
    # (compare-and-swap on status), redelivery is safe: a second delivery
    # that finds the event already PROCESSING/SUCCESS simply no-ops.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Celery Beat schedule: periodically sweep for FAILED events whose
# next_retry_at has passed and re-enqueue them. This is what actually
# "wakes up" a retry -- the event doesn't reschedule itself.
celery_app.conf.beat_schedule = {
    "sweep-due-retries-every-5-seconds": {
        "task": "app.workers.webhook_tasks.sweep_due_retries",
        "schedule": 5.0,
    },
}
