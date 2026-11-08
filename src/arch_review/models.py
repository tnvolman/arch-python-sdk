"""Response types for ``POST /v1/review``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from arch_review.exceptions import APIError

_VERDICTS = frozenset({"APPROVED", "FLAGGED", "REJECTED"})


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """Arch verdict. Counts are read from the top level or from ``meta``."""

    verdict: str
    summary: str
    approved: int
    flagged: int
    rejected: int
    total: int
    passed: bool | None = None
    suggestions: tuple[str, ...] = ()
    charge_id: str | None = None
    billing_status: str | None = None
    chars: int | None = None
    charged: str | None = None


def parse_review_result(payload: Any) -> ReviewResult:
    """Build a result from Arch JSON. Never invents a verdict."""
    if not isinstance(payload, dict):
        raise APIError("Arch returned a non-object JSON body.")
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    summary = _string(payload.get("summary"))
    if summary is None:
        summary = _string(payload.get("review")) or ""
    verdict = _verdict(payload.get("verdict")) or _verdict_token(summary)
    if verdict is None:
        raise APIError("Arch response did not include a verdict.")
    suggestions = payload.get("suggestions") or []
    if not isinstance(suggestions, list) or not all(isinstance(item, str) for item in suggestions):
        raise APIError("Arch response field suggestions is not a list of strings.")
    passed = payload.get("passed")
    if passed is not None and not isinstance(passed, bool):
        raise APIError("Arch response field passed is not a boolean.")
    return ReviewResult(
        verdict=verdict,
        summary=summary,
        approved=_count(payload, meta, "approved"),
        flagged=_count(payload, meta, "flagged"),
        rejected=_count(payload, meta, "rejected"),
        total=_count(payload, meta, "total"),
        passed=passed,
        suggestions=tuple(suggestions),
        charge_id=_string(payload.get("charge_id")),
        billing_status=_string(payload.get("billing_status")),
        chars=_optional_int(_field(payload, meta, "chars")),
        charged=_string(_field(payload, meta, "charged")),
    )


def _field(payload: dict[str, Any], meta: dict[str, Any], name: str) -> Any:
    if name in payload and not isinstance(payload[name], (dict, list)):
        return payload[name]
    return meta.get(name)


def _string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise APIError("Arch response contained a non-string text field.")
    return value


def _verdict(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise APIError("Arch response field verdict is not a string.")
    token = value.strip().upper()
    if token not in _VERDICTS:
        raise APIError(f"Arch response had an unknown verdict: {value}.")
    return token


def _verdict_token(text: str) -> str | None:
    if not text.strip():
        return None
    token = text.lstrip().split(None, 1)[0].rstrip(":.,;").upper()
    return token if token in _VERDICTS else None


def _count(payload: dict[str, Any], meta: dict[str, Any], name: str) -> int:
    raw = _field(payload, meta, name)
    return 0 if raw is None else _integer(raw, name)


def _optional_int(raw: Any) -> int | None:
    return None if raw is None else _integer(raw, "chars")


def _integer(raw: Any, name: str) -> int:
    if isinstance(raw, bool) or isinstance(raw, float) or not isinstance(raw, (int, str)):
        raise APIError(f"Arch response field {name} is not an integer.")
    try:
        return int(raw)
    except ValueError as exc:
        raise APIError(f"Arch response field {name} is not an integer.") from exc
