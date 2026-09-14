"""Unit tests for HMAC signature verification."""

from app.core.security import compute_signature, verify_signature


def test_valid_signature_passes():
    secret = "my-secret"
    body = b'{"event_id": "evt_1"}'
    sig = compute_signature(secret, body)
    assert verify_signature(secret, body, sig) is True


def test_wrong_secret_fails():
    body = b'{"event_id": "evt_1"}'
    sig = compute_signature("secret-a", body)
    assert verify_signature("secret-b", body, sig) is False


def test_tampered_body_fails():
    secret = "my-secret"
    original_body = b'{"amount": 100}'
    tampered_body = b'{"amount": 999999}'
    sig = compute_signature(secret, original_body)
    assert verify_signature(secret, tampered_body, sig) is False


def test_missing_signature_fails():
    assert verify_signature("secret", b"{}", None) is False


def test_malformed_signature_does_not_raise():
    assert verify_signature("secret", b"{}", "not-a-valid-hex-digest!!") is False
