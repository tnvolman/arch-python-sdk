"""Sync and async clients for ``POST /v1/review``."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any

import httpx

from arch_review.exceptions import APIError, AuthError, PaymentError, RateLimitError
from arch_review.models import ReviewResult, parse_review_result

DEFAULT_BASE_URL = "https://sanctuary.tnvolman.com"
DEFAULT_API_KEY_HEADER = "X-API-Key"
DEFAULT_MAX_TOKENS = 1024
MAX_TOKENS = 4096
MAX_TEXT_CHARS = 50_000
_BACKOFF_CAP_SECONDS = 30.0


class _Settings:
    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str,
        api_key_header: str,
        max_retries: int,
        backoff_factor: float,
    ) -> None:
        self.api_key = _resolve_api_key(api_key)
        self.base_url = base_url.rstrip("/")
        self.api_key_header = _header_name(api_key_header)
        if max_retries < 0:
            raise ValueError("max_retries must be zero or greater")
        if backoff_factor < 0:
            raise ValueError("backoff_factor must be zero or greater")
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    def auth_headers(self, idempotency_key: str) -> dict[str, str]:
        if self.api_key_header.lower() == "authorization":
            token = self.api_key
            value = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        else:
            value = self.api_key
        return {self.api_key_header: value, "Idempotency-Key": idempotency_key}

    def delay(self, attempt: int) -> float:
        return min(_BACKOFF_CAP_SECONDS, self.backoff_factor * (2**attempt))


class ArchClient:
    """Blocking client. Each successful ``review`` is billed at $2.

    Retries of one call reuse a single idempotency key. A new call is a new charge.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key_header: str = DEFAULT_API_KEY_HEADER,
        timeout: float = 60.0,
        max_retries: int = 2,
        backoff_factor: float = 0.5,
    ) -> None:
        self._settings = _Settings(
            api_key,
            base_url=base_url,
            api_key_header=api_key_header,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
        )
        self._client = httpx.Client(
            base_url=self._settings.base_url,
            timeout=timeout,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )

    def __enter__(self) -> ArchClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def review(
        self,
        text: str,
        max_tokens: int | None = None,
        idempotency_key: str | None = None,
    ) -> ReviewResult:
        body, key = _prepare(text, max_tokens, idempotency_key)
        headers = self._settings.auth_headers(key)
        for attempt in range(self._settings.max_retries + 1):
            try:
                response = self._client.post("/v1/review", headers=headers, json=body)
            except httpx.TransportError as exc:
                raise APIError("Arch request failed (network).", idempotency_key=key) from exc
            result, error, retry = _interpret(response, attempt, self._settings.max_retries, key)
            if result is not None:
                return result
            assert error is not None
            if not retry:
                raise error
            time.sleep(self._settings.delay(attempt))
        raise AssertionError("retry loop exited without a result")


class AsyncArchClient:
    """Async client with the same billing and retry rules as ``ArchClient``."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key_header: str = DEFAULT_API_KEY_HEADER,
        timeout: float = 60.0,
        max_retries: int = 2,
        backoff_factor: float = 0.5,
    ) -> None:
        self._settings = _Settings(
            api_key,
            base_url=base_url,
            api_key_header=api_key_header,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
        )
        self._client = httpx.AsyncClient(
            base_url=self._settings.base_url,
            timeout=timeout,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )

    async def __aenter__(self) -> AsyncArchClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def review(
        self,
        text: str,
        max_tokens: int | None = None,
        idempotency_key: str | None = None,
    ) -> ReviewResult:
        body, key = _prepare(text, max_tokens, idempotency_key)
        headers = self._settings.auth_headers(key)
        for attempt in range(self._settings.max_retries + 1):
            try:
                response = await self._client.post("/v1/review", headers=headers, json=body)
            except httpx.TransportError as exc:
                raise APIError("Arch request failed (network).", idempotency_key=key) from exc
            result, error, retry = _interpret(response, attempt, self._settings.max_retries, key)
            if result is not None:
                return result
            assert error is not None
            if not retry:
                raise error
            await asyncio.sleep(self._settings.delay(attempt))
        raise AssertionError("retry loop exited without a result")


def _resolve_api_key(api_key: str | None) -> str:
    if api_key is None:
        api_key = os.environ.get("ARCH_API_KEY")
    if not isinstance(api_key, str) or not api_key.strip():
        raise AuthError("Missing API key. Pass api_key or set ARCH_API_KEY.")
    key = api_key.strip()
    _header_value(key, "api_key")
    return key


def _header_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("api_key_header is not a valid header name")
    for char in name:
        if ord(char) < 33 or ord(char) > 126 or char == ":":
            raise ValueError("api_key_header is not a valid header name")
    return name


def _header_value(value: str, label: str) -> None:
    for char in value:
        code = ord(char)
        if code == 0x09 or 32 <= code <= 126:
            continue
        raise ValueError(f"{label} is not a valid HTTP header value")


def _prepare(
    text: str,
    max_tokens: int | None,
    idempotency_key: str | None,
) -> tuple[dict[str, Any], str]:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not text or len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"text must be between 1 and {MAX_TEXT_CHARS} characters")
    if max_tokens is None:
        tokens = DEFAULT_MAX_TOKENS
    else:
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
            raise TypeError("max_tokens must be an int")
        if not 1 <= max_tokens <= MAX_TOKENS:
            raise ValueError(f"max_tokens must be between 1 and {MAX_TOKENS}")
        tokens = max_tokens
    if idempotency_key is None:
        key = str(uuid.uuid4())
    else:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        key = idempotency_key
        _header_value(key, "idempotency_key")
    return {"text": text, "max_tokens": tokens, "idempotency_key": key}, key


def _interpret(
    response: httpx.Response,
    attempt: int,
    max_retries: int,
    idempotency_key: str,
) -> tuple[ReviewResult | None, APIError | None, bool]:
    status = response.status_code
    if 200 <= status < 300:
        try:
            payload = response.json()
        except ValueError as exc:
            raise APIError(
                "Arch returned a non-JSON success body.",
                status_code=status,
                idempotency_key=idempotency_key,
            ) from exc
        return parse_review_result(payload), None, False

    error = _exception_for(response, idempotency_key)
    retry = (status == 429 or 500 <= status <= 599) and attempt < max_retries
    return None, error, retry


def _exception_for(response: httpx.Response, idempotency_key: str) -> APIError:
    status = response.status_code
    body: Any
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = _error_detail(body) or f"HTTP {status}"
    message = f"Arch API error {status}: {detail}"
    kwargs: dict[str, Any] = {
        "status_code": status,
        "body": body,
        "idempotency_key": idempotency_key,
    }
    if status == 401:
        return AuthError(message, **kwargs)
    if status == 402:
        billing = body.get("billing_setup_url") if isinstance(body, dict) else None
        if not isinstance(billing, str):
            billing = None
        return PaymentError(message, billing_setup_url=billing, **kwargs)
    if status == 429:
        return RateLimitError(message, **kwargs)
    return APIError(message, **kwargs)


def _error_detail(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, str) and error:
        return error
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    message = body.get("message")
    if isinstance(message, str) and message:
        return message
    return None
