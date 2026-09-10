#!/usr/bin/env python3
"""Opt-in dev-bucket smoke: PUT, HEAD, GET. Leaves one tiny content-addressed object.

TREG_ARCHIVE_OBJECT_STORE_* credentials must refer to the explicitly named dev/test bucket.
Run with: uv run --extra server python scripts/smoke_archive_r2.py --dev-bucket treg-archive-dev
This is never part of CI and never creates buckets or changes cloud configuration.
"""
import argparse
import asyncio
import hashlib
import os
import re


async def run(bucket: str):
    from treg.config import Settings
    from treg.infra.object_store import open_r2

    settings = Settings(_env_file=None)
    required = ("endpoint", "bucket", "access_key_id", "secret_access_key")
    missing = ["TREG_ARCHIVE_OBJECT_STORE_" + name.upper() for name in required
               if not getattr(settings, "archive_object_store_" + name)]
    if missing:
        print("SKIP: missing dev object-store configuration: " + ", ".join(missing))
        return
    if (os.environ.get('CI') or bucket != settings.archive_object_store_bucket
            or bucket != 'treg-archive-dev'):
        raise SystemExit('Refusing: requires a matching explicit dev/test bucket outside CI')
    if not re.fullmatch(r'https://[0-9a-f]{32}\.r2\.cloudflarestorage\.com', settings.archive_object_store_endpoint):
        raise SystemExit('Invalid R2 endpoint')
    body = b'treg archive R2 dev smoke v1\n'
    digest = hashlib.sha256(body).hexdigest()
    async with open_r2(settings) as store:
        uploaded = await store.put(body)
        assert uploaded.content_hash == digest
        assert await store.head(digest) == uploaded
        assert await store.get(digest) == body
    print('PASS: upload checksum, HEAD, and exact GET verified in dev bucket')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dev-bucket', default='treg-archive-dev')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.dev_bucket))
    except Exception:
        raise SystemExit('R2 smoke failed; check the dev configuration and bucket access') from None
