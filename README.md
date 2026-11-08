# arch-review

Typed Python client for the [Arch](https://arch.tnvolman.com) theological review API.

**Each successful `review()` call costs $2.** A retry of the same call reuses one `Idempotency-Key`, so it is not billed again. A new call is a new charge. Failed reviews are not a verdict: this client raises, and it does not invent `APPROVED`.

## Install

```bash
pip install arch-review
```

The package requires Python 3.10 or newer.

## Quickstart

Create a key at [arch.tnvolman.com](https://arch.tnvolman.com/#access) and export it. Do not hard-code it.

```bash
export ARCH_API_KEY="arch_live_..."
```

```python
from arch_review import ArchClient

with ArchClient() as client:
    result = client.review("John 3:16 teaches universal salvation.")

print(result.verdict)  # APPROVED, FLAGGED, or REJECTED
print(result.summary)
print(result.approved, result.flagged, result.rejected, result.total)
```

Async:

```python
import asyncio
from arch_review import AsyncArchClient


async def main() -> None:
    async with AsyncArchClient() as client:
        result = await client.review("Grace is unearned favor.")
        print(result.verdict, result.summary)


asyncio.run(main())
```

`review(text, max_tokens=None, idempotency_key=None)` posts JSON to `https://sanctuary.tnvolman.com/v1/review`. `text` must be 1–50,000 characters. `max_tokens` defaults to 1024 and cannot exceed 4096. Pass `base_url` to use another origin, including the documented host `https://arch.tnvolman.com`.

## Authentication

The API expects `X-API-Key`. The key is the `api_key` argument, or `ARCH_API_KEY` when the argument is omitted. `api_key_header` changes the header name. Setting it to `Authorization` sends `Authorization: Bearer <key>`.

## Retries

HTTP 429 and 5xx (including 503) are retried with exponential backoff. Every attempt sends the same idempotency key: the one you passed, or a key generated for that call. Other 4xx responses, redirects, and network errors are not retried. On a network error, `APIError.idempotency_key` is the key already used; pass it back to `review` if you try the call again.

| Status | Exception | Retried |
| --- | --- | --- |
| 401 | `AuthError` | no |
| 402 | `PaymentError` | no |
| 429 | `RateLimitError` | yes, then raised |
| other 4xx | `APIError` | no |
| 5xx and 503 | `APIError` | yes, then raised |

`PaymentError.billing_setup_url` is set when Arch returns one (no card on file).

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

Tests mock HTTP with [respx](https://github.com/lundberg/respx). They do not use a live key and do not call Arch.
