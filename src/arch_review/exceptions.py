"""Errors raised by the Arch review client."""

from __future__ import annotations

from typing import Any


class APIError(Exception):
    """A call failed, or Arch returned an error instead of a verdict.

    ``idempotency_key`` is set once a key was sent. Reuse it to retry without
    a second charge.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
        idempotency_key: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.idempotency_key = idempotency_key


class AuthError(APIError):
    """Missing API key, or Arch rejected it (HTTP 401)."""


class PaymentError(APIError):
    """No payment method, or the charge was declined (HTTP 402)."""

    def __init__(
        self,
        message: str,
        *,
        billing_setup_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.billing_setup_url = billing_setup_url


class RateLimitError(APIError):
    """Rate limit still exceeded after retries (HTTP 429)."""
