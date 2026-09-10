"""Archive body I/O and upload scheduling, separate from archive indexing and TTL learning."""
import asyncio
from collections import Counter
from dataclasses import dataclass
import hashlib
import logging
import re
import time

from .config import get_settings
from .infra.object_store import ObjectInfo, ObjectStore

_log = logging.getLogger("treg.archive_bodies")
_store: ObjectStore | None = None
_pending: set[asyncio.Task] = set()
_pending_bytes = 0
_sem = None
_sem_loop = None
# Operational counters have bounded labels and no call, team, key or body dimensions.
outcomes: Counter = Counter()


def configure(store: ObjectStore | None) -> None:
    global _store
    _store = store


def uses_r2() -> bool:
    s = get_settings()
    return s.archive_body_write != "db" or any(
        getattr(s, f"archive_body_read_{path}") != "db" for path in ("lookup", "result", "terminal"))


def validate_configuration() -> bool:
    s = get_settings()
    from .archive import mode
    from .infra.object_store import R2_ENDPOINT_RE
    if mode() == "off" or not uses_r2():
        return False
    if s.archive_body_write == "r2" and any(
            getattr(s, "archive_body_read_" + path) != "r2-first"
            for path in ("lookup", "result", "terminal")):
        raise RuntimeError("Archive R2-only writing requires all read paths to use r2-first")
    if (not R2_ENDPOINT_RE.fullmatch(s.archive_object_store_endpoint)
            or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", s.archive_object_store_bucket)
            or not s.archive_object_store_access_key_id or not s.archive_object_store_secret_access_key):
        raise RuntimeError("Archive R2 is enabled but its endpoint, bucket or credentials are missing/invalid")
    return True


class Observation:
    """Attach the completed storage outcome to the existing event without delaying the call."""
    def __init__(self):
        self.future = asyncio.get_running_loop().create_future()
        self.props = {
            "archive_body_write": get_settings().archive_body_write,
            "archive_body_upload_status": "not_requested",
            "archive_body_upload_ms": 0.0,
        }

    def finish(self, *, storage: str | None = None, reason: str | None = None) -> None:
        if self.future.done():
            return
        props = self.props | {"archive_body_storage": storage or "none",
                              "archive_body_dropped": reason is not None,
                              "archive_body_drop_reason": reason or "none"}
        outcomes[reason or storage or "none"] += 1
        self.future.set_result(props)

    def capture(self, emit) -> None:
        if self.future.done():
            emit(self.future.result())
        else:
            self.future.add_done_callback(lambda future: emit(future.result()))


def _upload_sem():
    global _sem, _sem_loop
    loop = asyncio.get_running_loop()
    if _sem is None or _sem_loop is not loop:
        _sem = asyncio.Semaphore(get_settings().archive_r2_upload_concurrency)
        _sem_loop = loop
    return _sem


@dataclass(frozen=True)
class WritePlan:
    storage: str | None
    keep_db: bool
    publish: bool = True
    reason: str | None = None


async def prepare(body: bytes, content_hash: str, *, keep: bool, observation: Observation,
                  terminal: bool = False) -> WritePlan:
    mode = observation.props["archive_body_write"]
    if not keep:
        return WritePlan(None, False, reason="policy_or_size")
    if mode == "db":
        return WritePlan("db", True)
    started = time.monotonic()
    reason = "upload_failed"
    attempts = get_settings().archive_r2_terminal_attempts if terminal else 1
    try:
        for attempt in range(attempts):
            try:
                if hashlib.sha256(body).hexdigest() != content_hash:
                    raise ValueError("archive content hash mismatch")
                async with asyncio.timeout(get_settings().archive_r2_timeout_s):
                    async with _upload_sem():
                        if _store is None:
                            raise RuntimeError("archive object store unavailable")
                        info = await _store.put(body)
                        if info != ObjectInfo(content_hash, len(body)):
                            raise ValueError("archive uploaded object mismatch")
                observation.props["archive_body_upload_status"] = "uploaded"
                return WritePlan(mode, mode == "both")
            except TimeoutError:
                reason = "upload_timeout"
            except Exception:
                reason = "upload_failed"
            if attempt + 1 < attempts:
                await asyncio.sleep(min(0.1 * 2 ** attempt, 1.0))
        observation.props["archive_body_upload_status"] = "failed"
        _log.error("archive body upload failed after %s attempt(s): %s", attempts, reason)
        # Double write preserves the DB copy when R2 fails, without publishing an R2 pointer.
        return WritePlan("db" if mode == "both" else None, mode == "both",
                         publish=mode == "both", reason=reason)
    finally:
        observation.props["archive_body_upload_ms"] = round((time.monotonic() - started) * 1000, 3)


def submit(factory, body_len: int, observation: Observation) -> str | None:
    """Separate count/byte budgets from archive's DB queue and DB semaphore."""
    global _pending_bytes
    s = get_settings()
    reason = ("upload_queue_full" if len(_pending) >= s.archive_r2_max_pending else
              "upload_bytes_full" if _pending_bytes + body_len > s.archive_r2_max_pending_bytes else None)
    if reason:
        observation.props["archive_body_upload_status"] = "dropped"
        observation.props["archive_body_upload_drop_reason"] = reason
        return reason
    _pending_bytes += body_len

    async def run():
        try:
            # Upload has its own deadline; the DB phase retains archive's existing deadline.
            await factory()
        except asyncio.CancelledError:
            observation.finish(reason="cancelled")
            raise
        except Exception:
            observation.finish(reason="record_failed")
        finally:
            observation.finish(reason="record_failed")  # no-op after the writer completed it

    task = asyncio.create_task(run())
    _pending.add(task)

    def done(task):
        global _pending_bytes
        _pending.discard(task)
        _pending_bytes -= body_len
    task.add_done_callback(done)


async def drain() -> None:
    while _pending:
        tasks = list(_pending)
        await asyncio.gather(*tasks, return_exceptions=True)
        _pending.difference_update(tasks)
    await asyncio.sleep(0)  # flush observation/event callbacks before analytics.drain()


@dataclass(frozen=True)
class BodyPointer:
    content_hash: str
    storage: str | None
    body: bytes | None
    enc: str | None


async def pointer(session, snapshot) -> BodyPointer:
    """Read DB fallback bytes while the session is open. Performs no object storage I/O."""
    from .models import ArchiveSnapshot

    body, enc = snapshot.body, snapshot.enc
    if body is None and snapshot.body_of is not None:
        carrier = await session.get(ArchiveSnapshot, snapshot.body_of)
        if carrier is not None and carrier.key_id == snapshot.key_id:
            body, enc = carrier.body, carrier.enc
    return BodyPointer(snapshot.content_hash, snapshot.body_storage, body, enc)


async def read(pointer: BodyPointer, path: str, *, diagnostics: dict | None = None) -> bytes | None:
    """Call only after closing every DB session owned by the request."""
    from .infra.object_store import ObjectReadError
    from .archive import _unpack

    reason, elapsed = "none", 0.0
    def observed(body, source):
        if diagnostics is not None:
            diagnostics.update(cache_body_source=source, cache_body_fallback_reason=reason,
                               cache_r2_read_ms=elapsed)
        return body

    if (getattr(get_settings(), f"archive_body_read_{path}") == "r2-first"
            and pointer.storage in ("both", "r2")):
        started = time.monotonic()
        try:
            async with asyncio.timeout(get_settings().archive_r2_read_timeout_s):
                if _store is None:
                    raise ObjectReadError("store_unavailable")
                body = await _store.get(pointer.content_hash)
                if body is None:
                    reason = "not_found"
                elif hashlib.sha256(body).hexdigest() != pointer.content_hash:
                    raise ObjectReadError("hash_mismatch")
                else:
                    elapsed = round((time.monotonic() - started) * 1000, 3)
                    return observed(body, "r2")
        except TimeoutError:
            reason = "timeout"
        except PermissionError:
            reason = "permission_denied"
        except ObjectReadError as exc:
            reason = exc.reason if exc.reason in {
                "not_found", "timeout", "permission_denied", "hash_mismatch", "too_large",
                "store_unavailable", "store_error"} else "store_error"
        except Exception:
            reason = "store_error"
        elapsed = round((time.monotonic() - started) * 1000, 3)
        outcomes["read_fallback_" + path] += 1
        outcomes["read_fallback_" + path + "_" + reason] += 1
        level = logging.ERROR if reason in {"permission_denied", "hash_mismatch", "too_large"} else logging.WARNING
        _log.log(level, "archive R2 read fallback path=%s reason=%s elapsed_ms=%s", path, reason, elapsed)
    body = _unpack(pointer.body, pointer.enc)
    return observed(body, "db" if body is not None else "none")
