"""
Integration tests for the FastAPI HTTP layer, using a real (SQLite) DB
session via dependency override and mocking the Celery enqueue call so no
Redis broker is required to run this suite.
"""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.database import get_db
from app.main import app

settings = get_settings()


@pytest.fixture()
def client(db_session, no_celery):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _signed_post(client, body: dict):
    raw = json.dumps(body).encode()
    sig = hmac.new(settings.WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        f"{settings.API_V1_PREFIX}/webhooks",
        content=raw,
        headers={"Content-Type": "application/json", settings.SIGNATURE_HEADER: sig},
    )


def test_ingest_new_event_returns_202(client):
    resp = _signed_post(
        client, {"event_id": "evt_api_1", "event_type": "payment.succeeded", "payload": {"amount": 5}}
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["event_id"] == "evt_api_1"
    assert body["status"] == "PENDING"


def test_duplicate_ingest_does_not_error_and_reuses_event(client):
    payload = {"event_id": "evt_api_dup", "event_type": "t", "payload": {}}
    first = _signed_post(client, payload)
    second = _signed_post(client, payload)
    assert first.status_code == 202
    assert second.status_code == 202
    assert "duplicate" in second.json()["message"]


def test_invalid_signature_is_rejected(client):
    raw = json.dumps({"event_id": "evt_bad_sig", "event_type": "t", "payload": {}}).encode()
    resp = client.post(
        f"{settings.API_V1_PREFIX}/webhooks",
        content=raw,
        headers={"Content-Type": "application/json", settings.SIGNATURE_HEADER: "not-the-real-signature"},
    )
    assert resp.status_code == 401


def test_missing_signature_is_rejected(client):
    raw = json.dumps({"event_id": "evt_no_sig", "event_type": "t", "payload": {}}).encode()
    resp = client.post(
        f"{settings.API_V1_PREFIX}/webhooks", content=raw, headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 401


def test_get_event_detail(client):
    _signed_post(client, {"event_id": "evt_detail", "event_type": "t", "payload": {"a": 1}})
    resp = client.get(f"{settings.API_V1_PREFIX}/webhooks/evt_detail")
    assert resp.status_code == 200
    assert resp.json()["event_id"] == "evt_detail"
    assert resp.json()["attempts"] == []


def test_get_nonexistent_event_returns_404(client):
    resp = client.get(f"{settings.API_V1_PREFIX}/webhooks/does-not-exist")
    assert resp.status_code == 404


def test_list_events_with_status_filter_and_pagination(client):
    for i in range(3):
        _signed_post(client, {"event_id": f"evt_list_{i}", "event_type": "t", "payload": {}})

    resp = client.get(
        f"{settings.API_V1_PREFIX}/webhooks", params={"status_filter": "PENDING", "page": 1, "page_size": 2}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) <= 2
    assert body["total"] >= 3


def test_retry_rejected_when_event_not_dead(client):
    _signed_post(client, {"event_id": "evt_not_dead", "event_type": "t", "payload": {}})
    resp = client.post(f"{settings.API_V1_PREFIX}/webhooks/evt_not_dead/retry")
    assert resp.status_code == 409


def test_metrics_summary_reflects_ingested_events(client):
    _signed_post(client, {"event_id": "evt_metrics_1", "event_type": "t", "payload": {}})
    resp = client.get(f"{settings.API_V1_PREFIX}/metrics/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_events"] >= 1
    assert "success_rate" in body


def test_health_check(client):
    resp = client.get(f"{settings.API_V1_PREFIX}/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
