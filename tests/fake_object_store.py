"""In-memory object storage for bootstrap injection; never opens a socket."""
import asyncio
import hashlib

from treg.infra.object_store import ObjectInfo


class MemoryObjectStore:
    def __init__(self):
        self.objects = {}
        self.put_calls = 0
        self.get_calls = 0
        self.fail_puts = 0
        self.fail_gets = False
        self.gate = None
        self.entered = asyncio.Event()
        self.check_io = lambda: None

    async def put(self, body: bytes) -> ObjectInfo:
        self.check_io()
        self.put_calls += 1
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_puts:
            self.fail_puts -= 1
            raise OSError('fake unavailable')
        key = hashlib.sha256(body).hexdigest()
        self.objects[key] = body
        return ObjectInfo(key, len(body))

    async def get(self, content_hash: str) -> bytes | None:
        self.check_io()
        self.get_calls += 1
        if self.fail_gets:
            raise OSError('fake unavailable')
        return self.objects.get(content_hash)

    async def head(self, content_hash: str) -> ObjectInfo | None:
        self.check_io()
        body = self.objects.get(content_hash)
        return ObjectInfo(content_hash, len(body)) if body is not None else None
