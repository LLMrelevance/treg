"""Arena task definitions and attributed results. No execution or money writes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VERSION = "2"
MAX_RESULT_BYTES = 256_000
RETENTION_DAYS = 30
TERMINAL = frozenset({"completed", "cancelled", "interrupted", "failed"})
REASONS = frozenset({"more_complete", "correct_identity", "more_current", "better_verification",
                     "wrong_identity", "missing_fields", "conflicting_data"})


@dataclass(frozen=True)
class Task:
    capability: str
    label: str
    description: str
    variants: tuple[tuple[str, ...], ...]


TASKS = {
    t.capability: t for t in (
        Task("people.email.find", "Work email", "Find a work email from a name or LinkedIn profile.",
             (("full_name", "domain"), ("linkedin_url",))),
        Task("people.enrich", "Person", "Compare the details each service knows about a person.",
             (("linkedin_url",), ("email",), ("full_name", "domain"))),
        Task("companies.enrich", "Company", "Compare company profiles, from the basics to the details.",
             (("domain",), ("name",), ("linkedin_url",))),
        Task("people.phone.find", "Phone", "Find a phone number. A found number is not a verified live line.",
             (("linkedin_url",), ("email",), ("full_name", "domain"))),
        Task("people.email.verify", "Verify email", "Compare mailbox verdicts. Invalid is a useful answer, too.",
             (("email",),)),
        Task("people.identity.resolve", "Email → LinkedIn", "Find the LinkedIn profile associated with an email.",
             (("email",),)),
    )
}

# A personal mailbox is a different task; bulk and asynchronous jobs are excluded by the planner.
EXCLUDED = frozenset({"leadmagic.x.personal-email-finder"})


class ArenaError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def validate_identity(capability: str, identity: dict) -> dict[str, str]:
    task = TASKS.get(capability)
    if task is None:
        raise ArenaError("Choose an Arena task.")
    allowed = {k for variant in task.variants for k in variant}
    if any(k not in allowed for k in identity):
        raise ArenaError("This task does not accept those input fields.")
    clean = {}
    for k, v in identity.items():
        if not isinstance(v, str) or len(v) > 500:
            raise ArenaError("Use text inputs of at most 500 characters.")
        if v.strip():
            clean[k] = v.strip()
    if not any(set(v) <= clean.keys() for v in task.variants):
        raise ArenaError("Complete one of the task's input options.")
    if "full_name" in clean:
        parts = clean["full_name"].split()
        if len(parts) < 2 or not all(any(c.isalpha() for c in p) for p in (parts[0], parts[-1])):
            raise ArenaError("Enter both a first and last name for a name-based comparison, or use a LinkedIn URL.")
        clean["full_name"] = " ".join(parts)
    if "domain" in clean:
        from urllib.parse import urlsplit
        value = clean["domain"]
        host = urlsplit(value if "://" in value else "https://" + value).hostname
        if not host or "." not in host or " " in host:
            raise ArenaError("Enter a company domain, such as example.com.")
        clean["domain"] = host.lower()
    if "email" in clean and ("@" not in clean["email"] or " " in clean["email"]):
        raise ArenaError("Enter an email address.")
    if "linkedin_url" in clean:
        from urllib.parse import urlsplit
        u = urlsplit(clean["linkedin_url"])
        if u.scheme not in {"http", "https"} or u.hostname not in {"linkedin.com", "www.linkedin.com"}:
            raise ArenaError("Enter a full LinkedIn profile URL.")
        prefix = "/company/" if capability == "companies.enrich" else "/in/"
        if not u.path.startswith(prefix) or not u.path[len(prefix):].strip("/"):
            raise ArenaError("Use a LinkedIn company URL." if prefix == "/company/" else "Use a LinkedIn person URL.")
        clean["linkedin_url"] = "https://www.linkedin.com" + u.path.rstrip("/")
    return clean


def classify(contract, adapter, endpoint: dict, status: int, doc: Any) -> tuple[str, dict]:
    """Match the routed lookup's structural hit rule; retain verification qualifiers separately."""
    miss_status = (endpoint.get("miss") or {}).get("status")
    if status == miss_status and 400 <= status < 500:
        return "miss", {}
    if not 200 <= status < 300:
        return "error", {}
    if not isinstance(doc, (dict, list)):
        return "error", {}
    output = adapter.from_upstream(doc)
    empty = any(output.get(k) in (None, "", [], {}) for k in contract.required_output)
    return ("miss" if adapter.is_miss(doc) or empty else "hit"), output


def safe_output(output: dict) -> dict:
    """A typed core only. No vendor envelope, raw response, or injected presentation HTML."""
    return {k: (v[:4000] if isinstance(v, str) else v)
            for k, v in output.items() if isinstance(v, (str, int, float, bool)) or v is None}


def present(payload: dict, *, mode: str, state: str) -> dict:
    attempts = payload.get("attempts", [])
    ordered = sorted(attempts, key=lambda a: a["order"] if mode == "waterfall" else a["display_order"])
    results = []
    for a in ordered:
        row = {"id": a["id"], "label": chr(65 + a["display_order"]), "state": a["state"],
               "output": a.get("output", {})}
        row.update({k: a.get(k) for k in ("provider", "endpoint_id", "tier", "estimate_micro",
                   "charged_micro", "duration_ms", "started_ms", "detail", "raw", "cached", "call_ref", "report", "rating", "manual")})
        row["can_try"] = state in TERMINAL and a["state"] in {"not_attempted", "skipped"} and not a.get("call_ref") and not a.get("manual")
        results.append(row)
    out = {"progress": sum(a["state"] not in {"queued", "running"} for a in attempts),
           "total": len(attempts), "results": results}
    out["charged_micro"] = sum(a.get("charged_micro") or 0 for a in attempts)
    out["charge_pending"] = any(a.get("charged_micro") is None and a["state"] != "queued" for a in attempts)
    out["stop_reason"] = payload.get("stop_reason", "")
    return out


def validate_vote(kind: str, selected: list[str], reasons: list[str], attempts: list[dict]) -> None:
    eligible = {a["id"] for a in attempts if a["state"] == "hit"}
    if len(set(selected)) != len(selected) or not set(selected) <= eligible:
        raise ArenaError("Choose from the results shown in this comparison.")
    expected = {"winner": len(selected) == 1, "tie": len(selected) >= 2,
                "none": not selected, "cannot_judge": not selected, "skip": not selected}
    if kind not in expected or not expected[kind]:
        raise ArenaError("Choose a result, a tie, none useful, or cannot judge.")
    if not set(reasons) <= REASONS:
        raise ArenaError("Unknown feedback reason.")
