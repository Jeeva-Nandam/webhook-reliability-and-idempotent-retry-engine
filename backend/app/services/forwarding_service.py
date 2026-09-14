"""
Forwards a verified, deduplicated event to a real downstream application,
instead of the built-in simulator. This is what turns the engine from a
self-contained demo into a relay you can point at your own project.

The forwarded request is signed with the same shared secret used for
inbound verification (WEBHOOK_SECRET). Reusing one secret for both
directions is a deliberate simplification for this project -- a larger
system would typically use a separate outbound secret per downstream
consumer, so a leaked inbound secret can't be used to forge callbacks.

The receiving application (see demo-app/ for a complete example) should
verify X-Engine-Signature exactly the way our own /webhooks endpoint
verifies X-Webhook-Signature -- it's the same HMAC-SHA256-over-raw-body
scheme, just relayed one hop further downstream.
"""

import json

import httpx

from app.core.config import get_settings
from app.core.security import compute_signature
from app.services.downstream_service import DownstreamServerError, DownstreamTimeoutError

settings = get_settings()


def forward_event(
    event_id: str,
    event_type: str,
    payload: dict,
    attempt_number: int,
    target_url: str | None = None,
    client: httpx.Client | None = None,
) -> int:
    """
    POSTs the event to target_url (defaults to settings.TARGET_URL).

    Raises DownstreamServerError or DownstreamTimeoutError on any failure
    (non-2xx response, timeout, or connection error) so the caller
    (app/workers/webhook_tasks.py) can feed it through the exact same
    retry/dead-letter logic used for the simulator -- from the retry
    engine's point of view, a real downstream failure and a simulated one
    look identical.

    target_url and client are optional overrides purely for testability
    (see tests/unit/test_forwarding_service.py) -- production code always
    calls this with no overrides and gets settings.TARGET_URL + a fresh
    httpx.Client.
    """
    url = target_url or settings.TARGET_URL
    if not url:
        raise ValueError("forward_event called but no TARGET_URL is configured")

    body = json.dumps(
        {
            "event_id": event_id,
            "event_type": event_type,
            "payload": payload,
            "attempt_number": attempt_number,
        }
    ).encode()

    signature = compute_signature(settings.WEBHOOK_SECRET, body)
    headers = {
        "Content-Type": "application/json",
        "X-Engine-Signature": signature,
        "X-Event-Id": event_id,
        "X-Event-Type": event_type,
        "X-Attempt-Number": str(attempt_number),
    }

    owns_client = client is None
    _client = client or httpx.Client()
    try:
        resp = _client.post(url, content=body, headers=headers, timeout=settings.FORWARD_TIMEOUT_SECONDS)
    except httpx.TimeoutException as exc:
        raise DownstreamTimeoutError(f"forwarding to {url} timed out") from exc
    except httpx.RequestError as exc:
        # DNS failure, connection refused, etc. -- treated as a retryable
        # server error rather than a hard crash, same as an actual 5xx.
        raise DownstreamServerError(status_code=502, message=f"could not reach {url}: {exc}") from exc
    finally:
        if owns_client:
            _client.close()

    if 200 <= resp.status_code < 300:
        return resp.status_code

    raise DownstreamServerError(status_code=resp.status_code, message=f"target responded {resp.status_code}")
