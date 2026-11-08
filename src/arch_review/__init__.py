"""Python client for the Arch theological review API.

Every successful review is billed. See the README before calling ``review``.
"""

from arch_review.client import (
    DEFAULT_BASE_URL,
    MAX_TEXT_CHARS,
    MAX_TOKENS,
    ArchClient,
    AsyncArchClient,
)
from arch_review.exceptions import APIError, AuthError, PaymentError, RateLimitError
from arch_review.models import ReviewResult

__version__ = "0.1.0"
PRICE_USD = 2

__all__ = [
    "APIError",
    "ArchClient",
    "AsyncArchClient",
    "AuthError",
    "DEFAULT_BASE_URL",
    "MAX_TEXT_CHARS",
    "MAX_TOKENS",
    "PRICE_USD",
    "PaymentError",
    "RateLimitError",
    "ReviewResult",
]
