"""Client tests. respx mocks every call; nothing here touches the network."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from arch_review import (
    APIError,
    ArchClient,
    AsyncArchClient,
    AuthError,
    PaymentError,
    RateLimitError,
)

URL = "https://sanctuary.tnvolman.com/v1/review"
SUCCESS = {
    "review": "REJECTED: John 3:16 does not teach universal salvation.",
    "passed": False,
    "suggestions": ["Revise to reflect conditional salvation through faith."],
    "charge_id": "pi_test",
    "billing_status": "charged",
    "meta": {
        "approved": 0,
        "flagged": 0,
        "rejected": 1,
        "total": 1,
        "chars": 42,
        "charged": "$2.00",
    },
}
FLAT = {
    "verdict": "FLAGGED",
    "summary": "Check the citation.",
    "approved": 1,
    "flagged": 1,
    "rejected": 0,
    "total": 2,
}


@pytest.fixture(autouse=True)
def _no_live_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARCH_API_KEY", raising=False)


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []
    monkeypatch.setattr("arch_review.client.time.sleep", lambda delay: delays.append(delay))

    async def _asleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("arch_review.client.asyncio.sleep", _asleep)
    return delays


def _ok(payload: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json=SUCCESS if payload is None else payload)


def _err(status: int, **extra: object) -> httpx.Response:
    return httpx.Response(status, json={"error": "nope", **extra})


def test_missing_or_blank_key(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AuthError):
        ArchClient()
    monkeypatch.setenv("ARCH_API_KEY", "   ")
    with pytest.raises(AuthError):
        AsyncArchClient()


@respx.mock
def test_key_source_payload_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCH_API_KEY", "from-env")
    route = respx.post(URL).mock(side_effect=[_ok(), _ok(), _ok(FLAT), _ok()])
    with ArchClient() as client:
        client.review("from the environment")
    assert route.calls[0].request.headers["X-API-Key"] == "from-env"
    with ArchClient("from-arg") as client:
        official = client.review("John 3:16 teaches universal salvation.")
        flat = client.review("Check this citation.", max_tokens=128, idempotency_key="caller-key")
        client.review("y" * 50_000, max_tokens=4096)
    assert official.verdict == "REJECTED"
    assert official.summary.startswith("REJECTED:")
    assert (official.approved, official.flagged, official.rejected, official.total) == (0, 0, 1, 1)
    assert official.passed is False
    assert official.suggestions == ("Revise to reflect conditional salvation through faith.",)
    assert (official.charge_id, official.billing_status, official.chars, official.charged) == (
        "pi_test",
        "charged",
        42,
        "$2.00",
    )
    assert flat.verdict == "FLAGGED" and flat.summary == "Check the citation."
    assert (flat.approved, flat.flagged, flat.rejected, flat.total) == (1, 1, 0, 2)
    bodies = [json.loads(call.request.content) for call in route.calls]
    assert bodies[1]["max_tokens"] == 1024
    assert bodies[1]["idempotency_key"] == route.calls[1].request.headers["Idempotency-Key"]
    assert bodies[2]["max_tokens"] == 128
    assert route.calls[2].request.headers["Idempotency-Key"] == "caller-key"
    assert bodies[1]["idempotency_key"] != bodies[2]["idempotency_key"]
    assert route.calls[1].request.headers["X-API-Key"] == "from-arg"
    assert bodies[3]["text"] == "y" * 50_000 and bodies[3]["max_tokens"] == 4096


@respx.mock
def test_authorization_bearer_and_custom_base_url() -> None:
    arch = respx.post("https://arch.tnvolman.com/v1/review").mock(return_value=_ok())
    with ArchClient(
        "test-key",
        base_url="https://arch.tnvolman.com/",
        api_key_header="Authorization",
    ) as client:
        client.review("Grace is unearned favor.")
    assert arch.calls[0].request.headers["Authorization"] == "Bearer test-key"
    sanctuary = respx.post(URL).mock(return_value=_ok())
    with ArchClient("Bearer already", api_key_header="Authorization") as client:
        client.review("Grace is unearned favor.")
    assert arch.call_count == 1
    assert sanctuary.calls[0].request.headers["Authorization"] == "Bearer already"


@respx.mock
def test_retries_reuse_one_key_and_cap_backoff(no_sleep: list[float]) -> None:
    route = respx.post(URL).mock(side_effect=[_err(429), _err(503), _err(500), _ok()])
    with ArchClient("test-key", max_retries=3, backoff_factor=0.5) as client:
        assert client.review("same charge").verdict == "REJECTED"
    keys = [call.request.headers["Idempotency-Key"] for call in route.calls]
    bodies = {json.loads(call.request.content)["idempotency_key"] for call in route.calls}
    assert keys == [keys[0]] * 4 and bodies == {keys[0]}
    assert no_sleep == [0.5, 1.0, 2.0]

    no_sleep.clear()
    route.reset()
    route.mock(side_effect=[_err(500), _err(500), _err(500), _ok()])
    with ArchClient("test-key", max_retries=3, backoff_factor=16) as client:
        client.review("capped")
    assert no_sleep == [16.0, 30.0, 30.0]


@respx.mock
@pytest.mark.parametrize(
    ("status", "exc"),
    [(400, APIError), (401, AuthError), (402, PaymentError), (404, APIError), (422, APIError)],
)
def test_other_4xx_are_not_retried(status: int, exc: type[APIError]) -> None:
    route = respx.post(URL).mock(
        return_value=_err(status, billing_setup_url="https://example.test/billing")
    )
    with ArchClient("test-key", max_retries=3) as client:
        with pytest.raises(exc) as raised:
            client.review("bad once")
    assert route.call_count == 1 and type(raised.value) is exc and raised.value.idempotency_key
    if status == 402:
        assert isinstance(raised.value, PaymentError)
        assert raised.value.billing_setup_url == "https://example.test/billing"


@respx.mock
def test_exhausted_retries_raise(no_sleep: list[float]) -> None:
    route = respx.post(URL).mock(return_value=_err(429))
    with ArchClient("test-key", max_retries=2, backoff_factor=0.25) as client:
        with pytest.raises(RateLimitError):
            client.review("slow")
    assert route.call_count == 3 and no_sleep == [0.25, 0.5]
    assert len({call.request.headers["Idempotency-Key"] for call in route.calls}) == 1
    route.reset()
    no_sleep.clear()
    route.mock(return_value=_err(503))
    with ArchClient("test-key", max_retries=1, backoff_factor=0.25) as client:
        with pytest.raises(APIError) as raised:
            client.review("down")
    assert route.call_count == 2 and type(raised.value) is APIError and no_sleep == [0.25]


@respx.mock
@pytest.mark.parametrize(
    ("text", "max_tokens", "error"),
    [
        ("", None, ValueError),
        ("x" * 50_001, None, ValueError),
        ("ok", 0, ValueError),
        ("ok", 4097, ValueError),
        ("ok", True, TypeError),
        (None, None, TypeError),
    ],
)
def test_limits_are_rejected_before_http(
    text: object,
    max_tokens: object,
    error: type[Exception],
) -> None:
    route = respx.post(URL).mock(return_value=_ok())
    with ArchClient("test-key") as client:
        with pytest.raises(error):
            client.review(text, max_tokens=max_tokens)  # type: ignore[arg-type]
    assert route.call_count == 0


@respx.mock
def test_transport_redirect_and_bad_success_are_not_retried() -> None:
    route = respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    with ArchClient("test-key", max_retries=3) as client:
        with pytest.raises(APIError, match="network") as raised:
            client.review("maybe sent")
    assert route.call_count == 1 and raised.value.idempotency_key

    route.reset()
    route.mock(return_value=httpx.Response(302, headers={"Location": "https://evil.example/steal"}))
    with ArchClient("test-key", max_retries=2) as client:
        with pytest.raises(APIError) as raised:
            client.review("stay here")
    assert route.call_count == 1 and raised.value.status_code == 302

    route.reset()
    route.mock(return_value=httpx.Response(200, text="ok"))
    with ArchClient("test-key") as client:
        with pytest.raises(APIError, match="non-JSON"):
            client.review("no verdict")
    route.mock(return_value=httpx.Response(200, json={"review": "No status word here."}))
    with ArchClient("test-key") as client:
        with pytest.raises(APIError, match="verdict"):
            client.review("still no verdict")


@respx.mock
def test_async_review_retries_with_the_same_key(no_sleep: list[float]) -> None:
    route = respx.post(URL).mock(side_effect=[_err(429), _ok(FLAT)])

    async def run() -> None:
        async with AsyncArchClient("test-key", max_retries=2, backoff_factor=0.5) as client:
            result = await client.review("async text", idempotency_key="async-key")
        assert result.verdict == "FLAGGED" and result.summary == "Check the citation."

    asyncio.run(run())
    keys = [call.request.headers["Idempotency-Key"] for call in route.calls]
    assert keys == ["async-key", "async-key"] and no_sleep == [0.5]
