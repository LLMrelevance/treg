"""Conservative result evidence for cache admission. Never rewrites provider bytes."""
from dataclasses import dataclass
import json
import math
from typing import Literal


SUPPORTED = frozenset({
    "hunter.companies.emails",
    "leadmagic.x.employee-finder",
    "seranking.google.keywords.volume",
})


@dataclass(frozen=True)
class Result:
    state: Literal["found", "empty", "error", "unknown"]
    reason: str

    @property
    def hit(self) -> bool | None:
        return {"found": True, "empty": False}.get(self.state)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def classify(endpoint_id: str, status: int, body: bytes) -> Result:
    if not 200 <= status < 300:
        return Result("error", "http_error")
    if endpoint_id not in SUPPORTED:
        return Result("unknown", "unsupported_endpoint")
    try:
        doc = json.loads(body)
    except (ValueError, RecursionError):
        return Result("unknown", "invalid_json")
    if isinstance(doc, dict) and (doc.get("error") or doc.get("errors")):
        return Result("error", "provider_error")
    if endpoint_id == "seranking.google.keywords.volume":
        if not isinstance(doc, list):
            return Result("unknown", "invalid_shape")
        found = False
        for row in doc:
            if not isinstance(row, dict) or type(row.get("is_data_found")) is not bool:
                return Result("unknown", "invalid_shape")
            if row["is_data_found"]:
                volume = row.get("volume")
                if (not _text(row.get("keyword")) or type(volume) not in (int, float)
                        or (isinstance(volume, float) and not math.isfinite(volume)) or volume < 0):
                    return Result("unknown", "invalid_shape")
                found = True
        return Result("found", "keyword_data") if found else Result("empty", "no_keyword_data")
    if not isinstance(doc, dict):
        return Result("unknown", "invalid_shape")
    if endpoint_id == "hunter.companies.emails":
        data = doc.get("data")
        rows = data.get("emails") if isinstance(data, dict) else None
        fields, reason = ("value",), "emails"
    else:
        rows = doc.get("data")
        fields, reason = ("name", "full_name", "first_name", "profile_url"), "people"
    if not isinstance(rows, list):
        return Result("unknown", "invalid_shape")
    if not rows:
        return Result("empty", "no_" + reason)
    if all(isinstance(row, dict) and any(_text(row.get(f)) for f in fields) for row in rows):
        return Result("found", reason)
    return Result("unknown", "invalid_shape")
