"""
Integration tests for the GitHub webhook adapter, simulating exactly what
GitHub itself sends: X-Hub-Signature-256, X-GitHub-Delivery, X-GitHub-Event.
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


def _github_signature(body: bytes) -> str:
    digest = hmac.new(settings.WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_github_ping_event_accepted(client):
    body = json.dumps({"zen": "Design for failure.", "hook_id": 12345}).encode()
    resp = client.post(
        f"{settings.API_V1_PREFIX}/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _github_signature(body),
            "X-GitHub-Delivery": "11111111-1111-1111-1111-111111111111",
            "X-GitHub-Event": "ping",
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["event_id"] == "11111111-1111-1111-1111-111111111111"
    assert data["status"] == "PENDING"


def test_github_duplicate_delivery_id_is_idempotent(client):
    body = json.dumps({"ref": "refs/heads/main"}).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": _github_signature(body),
        "X-GitHub-Delivery": "22222222-2222-2222-2222-222222222222",
        "X-GitHub-Event": "push",
    }
    first = client.post(f"{settings.API_V1_PREFIX}/webhooks/github", content=body, headers=headers)
    second = client.post(f"{settings.API_V1_PREFIX}/webhooks/github", content=body, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert "duplicate" in second.json()["message"]


def test_github_invalid_signature_rejected(client):
    body = json.dumps({"ref": "refs/heads/main"}).encode()
    resp = client.post(
        f"{settings.API_V1_PREFIX}/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=deadbeef",
            "X-GitHub-Delivery": "33333333-3333-3333-3333-333333333333",
            "X-GitHub-Event": "push",
        },
    )
    assert resp.status_code == 401


def test_github_missing_signature_format_rejected(client):
    body = json.dumps({"ref": "refs/heads/main"}).encode()
    # GitHub always prefixes with 'sha256=' -- a bare hex digest (our native
    # format) should NOT be accepted on this endpoint.
    bare_digest = hmac.new(settings.WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    resp = client.post(
        f"{settings.API_V1_PREFIX}/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": bare_digest,
            "X-GitHub-Delivery": "44444444-4444-4444-4444-444444444444",
            "X-GitHub-Event": "push",
        },
    )
    assert resp.status_code == 401
