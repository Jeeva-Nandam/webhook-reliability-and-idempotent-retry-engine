"""
Unit tests for forwarding_service.forward_event, using httpx.MockTransport
so no real network call or running server is needed for the fast test
suite. See tests/integration/test_forwarding_live.py for a genuine live
HTTP round-trip test against a real running server.
"""

import httpx
import pytest

from app.services.downstream_service import DownstreamServerError, DownstreamTimeoutError
from app.services.forwarding_service import forward_event


def _client_with_handler(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_forward_event_success_returns_status_code():
    def handler(request):
        return httpx.Response(200, json={"received": True})

    code = forward_event(
        "evt_1",
        "post.created",
        {"content": "hi"},
        attempt_number=1,
        target_url="http://demo-app.test/internal/handle-event",
        client=_client_with_handler(handler),
    )
    assert code == 200


def test_forward_event_signs_the_request():
    captured = {}

    def handler(request):
        captured["signature"] = request.headers.get("X-Engine-Signature")
        captured["event_id"] = request.headers.get("X-Event-Id")
        captured["body"] = request.content
        return httpx.Response(200)

    forward_event(
        "evt_sign",
        "post.created",
        {"content": "hi"},
        attempt_number=1,
        target_url="http://demo-app.test/internal/handle-event",
        client=_client_with_handler(handler),
    )

    assert captured["event_id"] == "evt_sign"
    assert captured["signature"] is not None and len(captured["signature"]) == 64

    from app.core.config import get_settings
    from app.core.security import verify_signature

    assert verify_signature(get_settings().WEBHOOK_SECRET, captured["body"], captured["signature"])


def test_forward_event_raises_on_500():
    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(DownstreamServerError):
        forward_event(
            "evt_2",
            "t",
            {},
            attempt_number=1,
            target_url="http://demo-app.test/internal/handle-event",
            client=_client_with_handler(handler),
        )


def test_forward_event_raises_on_connection_error():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(DownstreamServerError):
        forward_event(
            "evt_3",
            "t",
            {},
            attempt_number=1,
            target_url="http://demo-app.test/internal/handle-event",
            client=_client_with_handler(handler),
        )


def test_forward_event_raises_on_timeout():
    def handler(request):
        raise httpx.TimeoutException("timed out")

    with pytest.raises(DownstreamTimeoutError):
        forward_event(
            "evt_4",
            "t",
            {},
            attempt_number=1,
            target_url="http://demo-app.test/internal/handle-event",
            client=_client_with_handler(handler),
        )


def test_forward_event_requires_a_target_url(monkeypatch):
    from app.services import forwarding_service as mod

    monkeypatch.setattr(mod.settings, "TARGET_URL", "")
    with pytest.raises(ValueError):
        forward_event("evt_5", "t", {}, attempt_number=1)
