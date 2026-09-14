"""
Simulated downstream service.

In real life this would be "call Stripe" or "call the customer's fulfillment
API." For this project we simulate the four outcomes that matter for a
retry system, so we can demonstrate the whole engine without needing a real
third-party integration.
"""

import random
import time


class DownstreamTimeoutError(Exception):
    pass


class DownstreamServerError(Exception):
    def __init__(self, status_code: int, message: str = "downstream server error"):
        self.status_code = status_code
        super().__init__(message)


def call_downstream(mode: str = "random") -> int:
    """
    Simulates calling an external service.

    mode:
      - "success"   -> always returns 200
      - "error_500" -> always raises DownstreamServerError(500)
      - "timeout"   -> always raises DownstreamTimeoutError
      - "random"    -> ~60% success, ~25% 500, ~15% timeout

    Returns the HTTP-like status code on success, or raises on failure.
    """
    if mode == "success":
        return 200

    if mode == "error_500":
        raise DownstreamServerError(500)

    if mode == "timeout":
        # simulate hanging then failing, rather than actually sleeping long
        time.sleep(0.05)
        raise DownstreamTimeoutError("downstream did not respond in time")

    # "random" mode
    roll = random.random()
    if roll < 0.60:
        return 200
    elif roll < 0.85:
        raise DownstreamServerError(500)
    else:
        raise DownstreamTimeoutError("downstream did not respond in time")
