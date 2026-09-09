"""Local deterministic invitation sampling. No network, database or response body access."""
import hashlib
from typing import Literal

from .config import get_settings


def sampled(kind: Literal["review", "feedback"], sample_id: str) -> bool:
    settings = get_settings()
    rate = {"review": settings.review_sample_rate, "feedback": settings.feedback_hint_rate}[kind]
    bucket = int.from_bytes(hashlib.sha256(f"{kind}:{sample_id}".encode()).digest()[:8], "big")
    return bucket < rate * 2**64
