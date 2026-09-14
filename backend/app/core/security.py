"""
HMAC webhook signature verification.

INTERVIEW CONCEPT: HMAC signature verification.

The sender and receiver share a secret (never sent over the wire). The
sender computes:

    signature = HMAC_SHA256(secret, raw_request_body)

and sends it in a header (e.g. X-Webhook-Signature). We recompute the same
HMAC over the raw body we received and compare it to what was sent. If they
match, we know two things:
  1. The request really came from someone who knows the secret (authenticity).
  2. The body was not modified in transit (integrity).

We deliberately hash the RAW bytes, not the parsed/re-serialized JSON,
because re-serializing JSON can change whitespace/key order and silently
break a legitimate signature.

We use `hmac.compare_digest` instead of `==` to compare digests in constant
time. A naive `==` comparison returns False as soon as it finds the first
differing byte, which means the comparison takes slightly less time for a
signature that's "more wrong" near the start. An attacker who can measure
response timing very precisely could exploit that to guess the correct
signature one byte at a time (a timing attack). `compare_digest` always
takes the same amount of time regardless of where the mismatch is.
"""

import hashlib
import hmac


def compute_signature(secret: str, raw_body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, raw_body: bytes, provided_signature: str | None) -> bool:
    if not provided_signature:
        return False
    expected = compute_signature(secret, raw_body)
    try:
        return hmac.compare_digest(expected, provided_signature)
    except (TypeError, ValueError):
        return False
