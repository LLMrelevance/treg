"""Paid enrichment comparisons. Each leg is an ordinary governed, metered call.

Runs are claimed once in the database. An interrupted owner is never retried automatically:
that would risk buying the same data twice after an ambiguous upstream completion.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid
from dataclasses import replace
from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy import delete, update
from sqlmodel import select

from .. import crypto
from ..domain import arena as rules, money
from ..domain.catalog import store as catalog_store
from ..domain.identity.access import Caller
from ..infra.db import session_maker
from ..models import ArenaEvaluation, ArenaRun, LedgerEntry, Membership, Org, User
from ..timeutil import utcnow_naive as now
from .call import route, service
from .call.resolve import _marketplace_pricing
from .call.types import CallInput, CallerSnapshot, CallFailure

log = logging.getLogger(__name__)
RUN_SECONDS = 240
_owners: dict[str, asyncio.Task] = {}


def _pack(value: dict) -> str:
    return crypto.encrypt(json.dumps(value, ensure_ascii=True, separators=(",", ":")))


def _unpack(value: str) -> dict:
    return json.loads(crypto.decrypt(value))


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def public_tasks() -> list[dict]:
    """Public catalog estimates by input shape; no team reads or upstream requests."""
    cat = catalog_store.load()
    tasks = []
    # Shape probes only: canonical derivations and adapter constants affect eligibility/pricing.
    # These values are neither returned to the page nor dispatched to a provider.
    probe = {"full_name": "Example Person", "domain": "example.com", "name": "Example",
             "email": "person@example.com", "linkedin_url": "https://www.linkedin.com/in/example"}
    for t in rules.TASKS.values():
        contract = cat.contracts[t.capability]
        previews = []
        for variant in t.variants:
            identity, _ = route.canonical_identity(contract, {k: probe[k] for k in variant})
            candidates, _ = route.candidates_for(contract, cat.for_capability(t.capability), cat.adapters, identity)
            providers = {}
            for ep, adapter, accepted in candidates:
                if ep["id"] in rules.EXCLUDED or ep.get("async") or ".bulk" in ep["id"]:
                    continue
                provider = ep["provider"]
                cv = cat.cost_view(ep.get("cost"), provider)
                query, body = adapter.to_upstream(identity, accepted)
                estimate = money.with_margin(_marketplace_pricing(
                    provider, ep["id"], cv, query, json.dumps(body).encode())[0]) if cv and cv.get("usd") is not None else None
                item = {"provider": provider, "endpoint_id": ep["id"], "estimate_micro": estimate,
                        "price_type": (ep.get("cost") or {}).get("type"), "tier": "catalog"}
                previous = providers.get(provider)
                if previous is None or (estimate is not None and (previous["estimate_micro"] is None or estimate < previous["estimate_micro"])):
                    providers[provider] = item
            previews.append(sorted(providers.values(), key=lambda p: (p["estimate_micro"] is None, p["estimate_micro"] or 0, p["provider"])))
        tasks.append({"id": t.capability, "label": t.label, "description": t.description,
                      "variants": t.variants, "fields": list(contract.output),
                      "providers": sorted({p["provider"] for rows in previews for p in rows}),
                      "provider_previews": previews})
    return tasks


def _track(run_id, task):
    _owners[run_id] = task
    task.add_done_callback(lambda t: _owners.pop(run_id, None) if _owners.get(run_id) is t else None)


async def _locked_owned(db, run_id, caller):
    await db.execute(update(ArenaRun).where(ArenaRun.id == run_id,
        ArenaRun.org_id == caller.org_id, ArenaRun.user_id == caller.user.id)
        .values(cancel_requested=ArenaRun.cancel_requested))
    return await _owned(db, run_id, caller)


def _attempt(payload, attempt_id):
    return next((a for a in payload["attempts"] if a["id"] == attempt_id), None)


def _uncalled(row, payload, attempt_id):
    if row.state not in rules.TERMINAL:
        raise rules.ArenaError("Wait for this run to finish.", 409)
    attempt = _attempt(payload, attempt_id)
    if not attempt or attempt["state"] not in {"not_attempted", "skipped"} or attempt.get("call_ref") or attempt.get("manual"):
        raise rules.ArenaError("This service has already been attempted or is unavailable.", 409)
    return attempt


def _returned(row, payload, attempt_id, *, action: str):
    if row.state not in rules.TERMINAL:
        raise rules.ArenaError(f"Wait for the run to finish before {action} a result.", 409)
    attempt = _attempt(payload, attempt_id)
    if not attempt or attempt["state"] not in {"hit", "miss", "error", "timeout"}:
        raise rules.ArenaError("Choose a returned result.")
    return attempt


async def manual_plan(caller, run_id, attempt_id):
    async with session_maker() as db:
        row = await _owned(db, run_id, caller)
        payload = _unpack(row.payload)
        original = _uncalled(row, payload, attempt_id)
        capability, identity = row.capability, payload["identity"]
    cat = catalog_store.load()
    plan = await route.build_plan(cat.by_id["treg." + capability], identity, caller,
                                  route.RouteOptions(strict_filters=True))
    candidate = next((c for c in plan.candidates if c.endpoint["id"] == original["endpoint_id"]), None)
    if candidate is None:
        raise rules.ArenaError("This service cannot use this query with your current access.", 409)
    ep, provider = candidate.endpoint, candidate.endpoint["provider"]
    query, body = candidate.adapter.to_upstream(plan.identity, candidate.variant)
    cv = cat.cost_view(ep.get("cost"), provider)
    if candidate.tier == "platform" and (not cv or cv.get("usd") is None):
        raise rules.ArenaError("Price unavailable for this service.", 409)
    estimate = money.with_margin(_marketplace_pricing(provider, ep["id"], cv, query,
        json.dumps(body).encode())[0]) if candidate.tier == "platform" else 0
    if estimate > 10_000_000:
        raise rules.ArenaError("This service exceeds the Arena per-call limit.")
    quote = {"id": uuid.uuid4().hex, "expires_at": (now() + timedelta(minutes=5)).isoformat(),
             "estimate_micro": estimate, "tier": candidate.tier, "query": query, "body": body,
             "method": ep["method"], "price_type": (ep.get("cost") or {}).get("type"),
             "endpoint_hash": _hash(ep), "adapter_hash": _hash(candidate.adapter.__dict__)}
    async with session_maker() as db:
        row = await _locked_owned(db, run_id, caller)
        payload = _unpack(row.payload)
        attempt = _uncalled(row, payload, attempt_id)
        attempt["manual_quote"] = quote
        row.payload = _pack(payload)
        balance = await money.balance_of(db, caller.org_id)
        db.add(row)
        await db.commit()
    return {"id": quote["id"], "estimate_micro": estimate, "balance_micro": balance,
            "affordable": balance >= estimate, "expires_at": quote["expires_at"] + "Z"}


async def start_manual(caller, run_id, attempt_id, quote_id, client, client_ip):
    from datetime import datetime
    async with session_maker() as db:
        row = await _locked_owned(db, run_id, caller)
        payload = _unpack(row.payload)
        attempt = _uncalled(row, payload, attempt_id)
        quote = attempt.get("manual_quote") or {}
        if quote.get("id") != quote_id or datetime.fromisoformat(quote["expires_at"]) < now():
            raise rules.ArenaError("Price expired. Try again for a fresh price.", 409)
        cat = catalog_store.load()
        ep, ad = cat.by_id.get(attempt["endpoint_id"]), cat.adapters.get(attempt["endpoint_id"])
        if not ep or not ad or _hash(ep) != quote["endpoint_hash"] or _hash(ad.__dict__) != quote["adapter_hash"]:
            raise rules.ArenaError("The catalog changed. Refresh this service's price.", 409)
        if await money.balance_of(db, caller.org_id) < quote["estimate_micro"]:
            raise rules.ArenaError("Not enough team credits.", 402)
        active = (await db.execute(select(ArenaRun.id).where(ArenaRun.user_id == caller.user.id,
            ArenaRun.state == "running", ArenaRun.deadline_at > now()).limit(3))).all()
        if len(active) >= 3:
            raise rules.ArenaError("Finish or cancel an active Arena run first.", 429)
        attempt.update({k: quote[k] for k in ("estimate_micro", "tier", "query", "body", "method",
                                             "price_type", "endpoint_hash", "adapter_hash")})
        attempt.pop("manual_quote", None)
        attempt.update(state="queued", manual=True, detail="Manually requested additional service.")
        payload["stop_reason"] = ""
        row.payload = _pack(payload)
        row.state = "running"
        row.cancel_requested = False
        row.deadline_at = now() + timedelta(seconds=RUN_SECONDS + 30)
        mode, capability = row.mode, row.capability
        db.add(row)
        await db.commit()
    _track(run_id, asyncio.create_task(_run(run_id, mode, capability, payload,
        CallerSnapshot.capture(caller), client, client_ip, only_attempt_id=attempt_id)))
    return {"id": run_id, "state": "running"}


async def rate_result(caller, run_id, attempt_id, *, value):
    if value not in {"up", "down"}:
        raise rules.ArenaError("Choose thumbs up or thumbs down.")
    async with session_maker() as db:
        row = await _locked_owned(db, run_id, caller)
        payload = _unpack(row.payload)
        attempt = _returned(row, payload, attempt_id, action="rating")
        previous = attempt.get("rating") or {}
        if previous.get("value") != value:
            timestamp = now().isoformat() + "Z"
            attempt["rating"] = {"value": value, "created_at": previous.get("created_at", timestamp),
                "updated_at": timestamp, "feedback_context": "attributed"}
            row.payload = _pack(payload)
            db.add(row)
            await db.commit()
        return {"rating": attempt["rating"]}


async def report_result(caller, run_id, attempt_id, *, reason, comment):
    if reason not in {"", "wrong_person", "wrong_company", "incorrect_data", "outdated_data", "other"}:
        raise rules.ArenaError("Choose an issue type.")
    if not reason and not comment.strip():
        raise rules.ArenaError("Add a reason or a note.")
    async with session_maker() as db:
        row = await _locked_owned(db, run_id, caller)
        payload = _unpack(row.payload)
        attempt = _returned(row, payload, attempt_id, action="reporting")
        if (attempt.get("rating") or {}).get("value") == "up":
            raise rules.ArenaError("Choose thumbs down before adding issue details.", 409)
        if not attempt.get("report"):
            attempt["report"] = {"reason": reason, "comment": comment.strip(),
                "created_at": now().isoformat() + "Z", "feedback_context": "attributed"}
            row.payload = _pack(payload)
            db.add(row)
            await db.commit()
        return {"report": attempt["report"]}


async def _owned(db, run_id: str, caller) -> ArenaRun:
    row = await db.get(ArenaRun, run_id)
    if row is None or row.org_id != caller.org_id or row.user_id != caller.user.id or row.expires_at < now():
        raise rules.ArenaError("Run not found.", 404)
    return row


async def prune(db) -> None:
    """Bounded lazy retention on Arena use; no call/identity payload enters analytics."""
    ids = list((await db.execute(select(ArenaRun.id).where(ArenaRun.expires_at < now()).limit(50))).scalars())
    if ids:
        await db.execute(delete(ArenaEvaluation).where(ArenaEvaluation.run_id.in_(ids)))
        await db.execute(delete(ArenaRun).where(ArenaRun.id.in_(ids)))


async def quote(caller, *, capability: str, identity: dict, mode: str,
                providers: list[str] | None, max_cost_micro: int) -> dict:
    identity = rules.validate_identity(capability, identity)
    if caller.org.demo or caller.org.public_demo:
        raise rules.ArenaError("Sign in with a regular team to run Arena.", 403)
    if mode not in {"compare", "waterfall"} or not 0 <= max_cost_micro <= 10_000_000:
        raise rules.ArenaError("Choose a mode and a budget between $0 and $10.")
    cat = catalog_store.load()
    plan = await route.build_plan(cat.by_id["treg." + capability], identity, caller,
                                  route.RouteOptions(strict_filters=True))
    chosen, dropped, seen = [], list(plan.dropped), set()
    requested = set(providers) if providers is not None else None
    if requested is not None and not requested:
        raise rules.ArenaError("Select at least one service.")
    for c in plan.candidates:
        ep, p = c.endpoint, c.endpoint["provider"]
        if requested is not None and p not in requested:
            continue
        if ep["id"] in rules.EXCLUDED or ep.get("async") or ".bulk" in ep["id"] or p in seen:
            continue
        query, body = c.adapter.to_upstream(plan.identity, c.variant)
        cv = cat.cost_view(ep.get("cost"), p)
        if c.tier == "platform" and (not cv or cv.get("usd") is None):
            dropped.append({"endpoint_id": ep["id"], "why": "price unavailable"})
            continue
        # Exact adapter request, including modifiers: a catalog base price is not a quote.
        estimate = money.with_margin(_marketplace_pricing(
            p, ep["id"], cv, query, json.dumps(body).encode())[0]) if c.tier == "platform" else 0
        seen.add(p)
        chosen.append({"id": uuid.uuid4().hex, "provider": p, "endpoint_id": ep["id"],
                       "tier": c.tier, "estimate_micro": estimate, "query": query, "body": body,
                       "method": ep["method"], "state": "queued", "output": {},
                       "charged_micro": 0, "order": len(chosen), "adapter_hash": _hash(c.adapter.__dict__),
                       "endpoint_hash": _hash(ep), "price_type": (ep.get("cost") or {}).get("type")})
    if requested is not None and requested - seen:
        raise rules.ArenaError("Some selected services cannot use this input right now. Refresh the service selection.")
    if not chosen:
        raise rules.ArenaError("No service can use this input with your team's current access.")
    if mode == "waterfall":
        # Use the exact quoted request price, including own keys and modifiers.
        chosen.sort(key=lambda a: a["estimate_micro"])
        for order, a in enumerate(chosen):
            a["order"] = order
    total = sum(a["estimate_micro"] for a in chosen)
    if mode == "compare" and total > max_cost_micro:
        raise rules.ArenaError(f"Comparing these services reserves up to ${total / 1e6:.4f}. Increase your budget or select fewer services.")
    if mode == "waterfall" and min(a["estimate_micro"] for a in chosen) > max_cost_micro:
        raise rules.ArenaError("Your budget is below every available service's estimate.")
    display = list(range(len(chosen)))
    secrets.SystemRandom().shuffle(display)
    for a, d in zip(chosen, display):
        a["display_order"] = d
    snapshot = {"identity": identity, "attempts": chosen, "max_cost_micro": max_cost_micro,
                "version": rules.VERSION, "fields": list(plan.contract.output), "stop_reason": ""}
    async with session_maker() as db:
        await db.execute(update(User).where(User.id == caller.user.id).values(token_version=User.token_version))
        await prune(db)
        # Automatic price previews are not executions. Bound unused quote storage by
        # retiring the oldest drafts, without blocking edits or touching run history.
        stale = (await db.execute(select(ArenaRun.id).where(
            ArenaRun.user_id == caller.user.id, ArenaRun.state == "quoted")
            .order_by(ArenaRun.created_at.desc(), ArenaRun.id.desc()).offset(99))).scalars().all()
        if stale:
            await db.execute(delete(ArenaRun).where(ArenaRun.id.in_(stale), ArenaRun.state == "quoted"))
        balance = await money.balance_of(db, caller.org_id)
        required = total if mode == "compare" else chosen[0]["estimate_micro"]
        row = ArenaRun(id=uuid.uuid4().hex, org_id=caller.org_id, user_id=caller.user.id,
                       request_key=uuid.uuid4().hex, fingerprint=_hash(snapshot), capability=capability,
                       mode=mode, state="quoted", payload=_pack(snapshot),
                       deadline_at=now() + timedelta(minutes=5), expires_at=now() + timedelta(days=rules.RETENTION_DAYS))
        db.add(row)
        await db.commit()
    return {"id": row.id, "mode": mode, "capability": capability, "org_id": caller.org_id,
            "org": caller.org.slug, "balance_micro": balance, "estimate_micro": total,
            "required_micro": required, "max_cost_micro": max_cost_micro,
            "affordable": balance >= required, "dropped": dropped,
            "providers": [{k: a[k] for k in ("provider", "endpoint_id", "tier", "estimate_micro", "price_type")} for a in chosen],
            "expires_at": row.deadline_at.isoformat() + "Z"}


async def start(caller, run_id: str, client, client_ip: str) -> dict:
    async with session_maker() as db:
        # Serialize admission for one user across distinct drafts and teams.
        await db.execute(update(User).where(User.id == caller.user.id).values(token_version=User.token_version))
        row = await _owned(db, run_id, caller)
        if row.state != "quoted":
            return {"id": row.id, "state": row.state}
        if row.deadline_at < now():
            raise rules.ArenaError("This quote expired. Submit again for a fresh quote.", 409)
        recent = (await db.execute(select(ArenaRun.id).where(
            ArenaRun.user_id == caller.user.id, ArenaRun.state != "quoted",
            ArenaRun.created_at > now() - timedelta(hours=1)).limit(100))).all()
        if len(recent) >= 100:
            raise rules.ArenaError("You've started 100 Arena runs in the past hour. Try again when an earlier run leaves that window; price previews don't count.", 429)
        payload = _unpack(row.payload)
        cat = catalog_store.load()
        for a in payload["attempts"]:
            ep, ad = cat.by_id.get(a["endpoint_id"]), cat.adapters.get(a["endpoint_id"])
            if not ep or not ad or _hash(ep) != a["endpoint_hash"] or _hash(ad.__dict__) != a["adapter_hash"]:
                raise rules.ArenaError("The catalog changed. Refresh your quote before running.", 409)
        required = sum(a["estimate_micro"] for a in payload["attempts"])
        if row.mode == "waterfall":
            required = min(a["estimate_micro"] for a in payload["attempts"])
        if await money.balance_of(db, caller.org_id) < required:
            raise rules.ArenaError("Not enough team credits. Add credits or choose fewer services.", 402)
        active = (await db.execute(select(ArenaRun.id).where(
            ArenaRun.user_id == caller.user.id, ArenaRun.state == "running", ArenaRun.deadline_at > now()).limit(3))).all()
        if len(active) >= 3:
            raise rules.ArenaError("Finish or cancel an active Arena run first.", 429)
        claim = await db.execute(update(ArenaRun).where(ArenaRun.id == run_id, ArenaRun.state == "quoted").values(
            state="running", created_at=now(), deadline_at=now() + timedelta(seconds=RUN_SECONDS + 30)))
        await db.commit()
        mode, capability = row.mode, row.capability
    if claim.rowcount:
        task = asyncio.create_task(_run(run_id, mode, capability, payload, CallerSnapshot.capture(caller), client, client_ip))
        _track(run_id, task)
    return {"id": run_id, "state": "running"}


async def _fresh_caller(snapshot):
    """Recheck membership and policy between legs; never resurrect revoked access from a quote."""
    async with session_maker() as db:
        member = await db.get(Membership, snapshot.membership.id)
        user = await db.get(User, snapshot.user.id)
        org = await db.get(Org, snapshot.org.id)
        if not member or not user or not org or user.suspended or org.suspended:
            raise rules.ArenaError("Team access is no longer available.", 403)
        current = CallerSnapshot.capture(Caller(member, user, org))
    # A comparison measures a direct service. Avoid aggregator substitutions and their different
    # prices; this is a per-run execution choice, never a change to the team's settings.
    return replace(current, org=replace(current.org, platform_overflow_disabled=True))


async def _save(run_id, payload, state=None):
    async with session_maker() as db:
        values = {"payload": _pack(payload)}
        if state:
            values["state"] = state
        await db.execute(update(ArenaRun).where(ArenaRun.id == run_id, ArenaRun.state == "running").values(**values))
        await db.commit()


async def _run(run_id, mode, capability, payload, caller, client, client_ip, only_attempt_id=None):
    lock = asyncio.Lock()
    started = time.monotonic()
    offset = max((a.get("started_ms", 0) + (a.get("duration_ms") or 0) for a in payload["attempts"]), default=0) if only_attempt_id else 0
    cat = catalog_store.load()
    contract = cat.contracts[capability]
    state = "completed"

    async def persist():
        async with lock:
            await _save(run_id, payload)

    async def attempt(a):
        ep, ad = cat.by_id[a["endpoint_id"]], cat.adapters[a["endpoint_id"]]
        current = await _fresh_caller(caller)
        a.update(state="running", started_ms=offset + round((time.monotonic() - started) * 1000), charged_micro=None)
        await persist()  # A dispatch is durably visible before any upstream work.
        began = time.monotonic()
        data = json.dumps(a["body"]).encode() if a["method"] in {"POST", "PUT", "PATCH"} else b""
        headers = ((b"content-type", b"application/json"), (b"content-length", str(len(data)).encode()),
                   (b"x-treg-client", b"enrich-arena"), (b"cache-control", b"no-cache"),
                   (b"x-treg-route-max-cost", f"{a['estimate_micro'] / 1e6:.6f}".encode()))
        context = service.create_call_context(CallInput(method=a["method"], raw_rest=a["endpoint_id"],
            raw_headers=headers, query_items=tuple(a["query"].items()), raw_query=urlencode(a["query"]),
            body=route._Bytes(data), caller=current, client_ip=client_ip))
        a["call_ref"] = context.call_ref
        await persist()
        response = None
        try:
            async with asyncio.timeout(90):
                response = await service.execute_call(context, client)
                buf = bytearray()
                oversized = False
                async for chunk in response.body_stream:
                    if len(buf) + len(chunk) <= rules.MAX_RESULT_BYTES and not oversized:
                        buf.extend(chunk)
                    else:
                        oversized = True  # Drain for the ordinary settlement/close lifecycle.
                a["charged_micro"] = context.cost_micro if context.cost_micro is not None else int(route._header(response, "X-Treg-Cost-Micro") or 0)
                a["cached"] = bool(route._header(response, "X-Treg-Cache") in {"hit", "stale"})
                try:
                    doc = json.loads(buf) if not oversized else None
                except (ValueError, UnicodeDecodeError):
                    doc = None
                outcome, output = rules.classify(contract, ad, ep, response.status, doc)
                a.update(state=outcome, output=rules.safe_output(output), raw=doc,
                         detail=("Response exceeded the Arena size limit." if oversized else
                                 "No matching result." if outcome == "miss" else
                                 f"Service returned HTTP {response.status}." if outcome == "error" else "Task data found."),
                         status=response.status)
        except CallFailure as exc:
            a.update(state="error", detail=exc.kind, failure_kind=exc.kind, status=exc.status_code,
                     charged_micro=context.cost_micro if context.cost_micro is not None else 0)
        except TimeoutError:
            a.update(state="timeout", detail="Service deadline exceeded.", charged_micro=context.cost_micro)
        except asyncio.CancelledError:
            a.update(state="cancelled", detail="Run stopped.", charged_micro=context.cost_micro)
            raise
        except Exception:
            a.update(state="error", detail="Result could not be completed.", charged_micro=context.cost_micro)
            log.exception("Arena attempt failed: %s", run_id)
        finally:
            if response is not None:
                await response.close()
            a["duration_ms"] = round((time.monotonic() - began) * 1000)
            await persist()

    async def work():
        if only_attempt_id:
            await attempt(next(a for a in payload["attempts"] if a["id"] == only_attempt_id))
            payload["stop_reason"] = "Additional service completed. Earlier results are preserved."
        elif mode == "compare":
            # All ceilings were admitted together at quote/start, not checked independently by
            # racing child tasks. Runtime balance/price/policy gates still apply to every leg.
            sem = asyncio.Semaphore(4)
            async def leg(a):
                async with sem:
                    await attempt(a)
            async with asyncio.TaskGroup() as group:
                for a in payload["attempts"]:
                    group.create_task(leg(a))
            payload["stop_reason"] = "All selected services completed."
        else:
            spent, errors, rejected = 0, 0, set()
            for a in payload["attempts"]:
                if spent + a["estimate_micro"] > payload["max_cost_micro"]:
                    a.update(state="skipped", detail="Would exceed the run budget.")
                    continue
                if rejected and (a["provider"] in rejected or
                    (a["tier"] == "platform" and a["price_type"] not in {"per_success", "free"} and a["estimate_micro"] > route.CHEAP_RETRY_MICRO)):
                    a.update(state="skipped", detail="Not retried on a paid service after a rejected request.")
                    continue
                await attempt(a)
                # An uncertain fee consumes its ceiling for subsequent admission.
                spent += a["charged_micro"] if a.get("charged_micro") is not None else a["estimate_micro"]
                if a["state"] == "hit":
                    payload["stop_reason"] = "Stopped at the first result containing the task's required data."
                    break
                if a.get("failure_kind") in route._GLOBAL_REFUSALS:
                    payload["stop_reason"] = "Stopped by the team's balance or usage policy."
                    break
                if a["state"] != "miss":
                    errors += 1
                    if a.get("status") in route._CALLER_FAULT and not (a["tier"] == "platform" and a.get("status") in {401, 403}):
                        rejected.add(a["provider"])
                    if errors > route.MAX_ERROR_FALLBACKS:
                        payload["stop_reason"] = "Stopped after the bounded error fallback."
                        break
            if not payload["stop_reason"]:
                payload["stop_reason"] = "No remaining service within the run budget returned the required data."

    async def watch_cancel():
        while True:
            await asyncio.sleep(0.75)
            async with session_maker() as db:
                row = await db.get(ArenaRun, run_id)
                if row is None or row.cancel_requested:
                    return

    worker = asyncio.create_task(work())
    watcher = asyncio.create_task(watch_cancel())
    try:
        done, _ = await asyncio.wait({worker, watcher}, timeout=RUN_SECONDS, return_when=asyncio.FIRST_COMPLETED)
        if worker in done:
            await worker
        else:
            state = "cancelled" if watcher in done else "interrupted"
            payload["stop_reason"] = "Cancelled by you." if state == "cancelled" else "Run deadline reached."
    except asyncio.CancelledError:
        state = "interrupted"
        payload["stop_reason"] = "Execution interrupted; paid calls are not automatically retried."
    except Exception:
        state = "failed"
        payload["stop_reason"] = "The run could not finish. Completed results are kept."
        log.exception("Arena run failed: %s", run_id)
    finally:
        worker.cancel()
        watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)
        for a in payload["attempts"]:
            if a["state"] == "queued":
                a.update(state="not_attempted", detail=payload["stop_reason"])
        await _save(run_id, payload, state)


async def get_run(caller, run_id):
    async with session_maker() as db:
        row = await _locked_owned(db, run_id, caller)
        payload = _unpack(row.payload)
        if row.state == "running" and row.deadline_at < now():
            # No re-dispatch after process loss. Keep unknown fees unknown until reconciled.
            row.state = "interrupted"
            payload["stop_reason"] = "Execution was interrupted. No calls will be retried automatically."
            for a in payload["attempts"]:
                if a["state"] in {"queued", "running"}:
                    a["state"] = "not_attempted" if a["state"] == "queued" else "interrupted"
            row.payload = _pack(payload)
            db.add(row)
            await db.commit()
        if row.state in rules.TERMINAL:
            uncertain = [a for a in payload["attempts"] if a.get("charged_micro") is None]
            refs = [a["call_ref"] for a in uncertain if a.get("call_ref")]
            entries = (await db.execute(select(LedgerEntry).where(LedgerEntry.org_id == caller.org_id,
                LedgerEntry.call_id.in_(refs), LedgerEntry.kind.in_(["settle", "release"])))).scalars().all() if refs else []
            final_costs = {e.call_id: -e.amount_micro if e.kind == "settle" else 0 for e in entries}
            changed = False
            for a in uncertain:
                if a.get("call_ref") in final_costs or a["tier"] != "platform":
                    a["charged_micro"] = final_costs.get(a.get("call_ref"), 0)
                    changed = True
            if changed:
                row.payload = _pack(payload)
                db.add(row)
                await db.commit()
        evaluation = (await db.execute(select(ArenaEvaluation).where(ArenaEvaluation.run_id == row.id))).scalar_one_or_none()
        return {"id": row.id, "mode": row.mode, "capability": row.capability, "state": row.state,
                "revealed": True, "created_at": row.created_at.isoformat() + "Z",
                "identity": payload["identity"], "fields": payload["fields"],
                "vote": ({"kind": evaluation.kind, **_unpack(evaluation.payload).get("vote", {})} if evaluation else None),
                **rules.present(payload, mode=row.mode, state=row.state)}


async def history(caller):
    async with session_maker() as db:
        await prune(db)
        rows = (await db.execute(select(ArenaRun).where(ArenaRun.org_id == caller.org_id,
            ArenaRun.user_id == caller.user.id, ArenaRun.state != "quoted", ArenaRun.expires_at > now())
            .order_by(ArenaRun.created_at.desc()).limit(30))).scalars().all()
        result = [{"id": r.id, "capability": r.capability, "mode": r.mode, "state": r.state,
                   "identity": _unpack(r.payload)["identity"], "created_at": r.created_at.isoformat() + "Z"} for r in rows]
        await db.commit()
        return result


async def cancel(caller, run_id):
    async with session_maker() as db:
        row = await _owned(db, run_id, caller)
        if row.state == "running":
            await db.execute(update(ArenaRun).where(ArenaRun.id == run_id).values(cancel_requested=True))
            await db.commit()
    return {"id": run_id}


async def evaluate(caller, run_id, *, kind: str, selected: list[str], reasons: list[str], comment: str):
    # Lock the run, rather than an absent evaluation row, to serialize concurrent votes.
    async with session_maker() as db:
        await db.execute(update(ArenaRun).where(ArenaRun.id == run_id, ArenaRun.org_id == caller.org_id,
            ArenaRun.user_id == caller.user.id).values(cancel_requested=ArenaRun.cancel_requested))
        row = await _owned(db, run_id, caller)
        if row.mode != "compare" or row.state not in rules.TERMINAL:
            raise rules.ArenaError("Finish a comparison before voting.", 409)
        existing = (await db.execute(select(ArenaEvaluation).where(ArenaEvaluation.run_id == row.id))).scalar_one_or_none()
        if existing is not None:
            # Showing vendors does not submit a vote; an explicit vote is immutable on retries.
            return {"id": run_id, "revealed": True}
        payload = _unpack(row.payload)
        rules.validate_vote(kind, selected, reasons, payload["attempts"])
        vote = {"selected": selected, "reasons": reasons, "comment": comment[:1000]}
        db.add(ArenaEvaluation(id=uuid.uuid4().hex, org_id=caller.org_id, run_id=row.id,
            user_id=caller.user.id, kind=kind, payload=_pack({"vote": vote, "version": rules.VERSION,
                "feedback_context": "attributed",
                "fingerprint": row.fingerprint, "exposure": [{k: a[k] for k in
                    ("id", "endpoint_id", "display_order", "state", "adapter_hash")} for a in payload["attempts"]]})))
        row.revealed_at = now()
        db.add(row)
        await db.commit()
    return {"id": run_id, "revealed": True}


async def shutdown():
    tasks = list(_owners.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
