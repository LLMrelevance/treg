"""R2 double writes and reads through /call and /calls, with no real object network."""
import asyncio
from collections import Counter
import hashlib

import pytest
from sqlalchemy import event, select

from treg import archive, archive_bodies, audit, bootstrap
from treg.application.call import service
from treg.config import get_settings
from treg.infra import db
from treg.models import ArchiveKey, ArchiveSnapshot
from tests.fake_object_store import MemoryObjectStore
from tests.test_marketplace_call import _fake_relay

EP = 'tikhub.tiktok.video.comments'
RAW = b'{"data":{"comments":[{"text":"hello"}]}}'
URL = '/call/' + EP + '?aweme_id=7'


@pytest.fixture
async def r2(monkeypatch):
    monkeypatch.setenv('TREG_PLATFORM_KEY_TIKHUB', 'test-platform-key')
    monkeypatch.setenv('TREG_PLATFORM_PROVIDERS', 'tikhub')
    get_settings.cache_clear()
    s = get_settings()
    for name, value in {
        'archive_mode': 'serve', 'archive_body_write': 'both',
        'archive_serve_endpoints': EP, 'archive_serve_percent': 100,
        'archive_object_store_endpoint': 'https://' + 'a' * 32 + '.r2.cloudflarestorage.com', 'archive_object_store_bucket': 'treg-dev',
        'archive_object_store_access_key_id': 'test-access', 'archive_object_store_secret_access_key': 'test-secret',
    }.items():
        monkeypatch.setattr(s, name, value)
    store = MemoryObjectStore()
    bootstrap.configure_archive_object_store(store)
    # Enforce the invariant at the actual I/O seam, including request dependency sessions and
    # the background pool. Other requests may use DB concurrently; this task must own none.
    held = Counter()
    def checkout(connection, record, proxy):
        owner = asyncio.current_task()
        record.info['r2_test_owner'] = owner
        held[owner] += 1
    def checkin(connection, record):
        held[record.info.pop('r2_test_owner', None)] -= 1
    engines = {engine.sync_engine for engine in db._engines}
    for engine in engines:
        event.listen(engine, 'checkout', checkout)
        event.listen(engine, 'checkin', checkin)
    def check_io():
        assert held[asyncio.current_task()] == 0, 'object I/O holds a DB connection'
    store.check_io = check_io
    monkeypatch.setattr(service, 'relay', _fake_relay(200, RAW))
    yield store
    await archive.drain()
    bootstrap.configure_archive_object_store(None)
    for engine in engines:
        event.remove(engine, 'checkout', checkout)
        event.remove(engine, 'checkin', checkin)
    get_settings.cache_clear()


async def snapshots():
    async with db.session_maker() as s:
        return (await s.execute(select(ArchiveSnapshot).order_by(ArchiveSnapshot.id))).scalars().all()


async def test_upload_precedes_pointer_and_call_does_not_wait(clients, r2, monkeypatch):
    events = []
    monkeypatch.setattr(service.analytics, 'capture', lambda who, name, props, **kw: events.append((name, props)))
    r2.gate = asyncio.Event()
    response = await asyncio.wait_for(clients.get(URL), 2)
    assert response.status_code == 200 and response.content == RAW
    await asyncio.wait_for(r2.entered.wait(), 2)
    assert await snapshots() == []
    assert not [p for name, p in events if name == 'tool_called']
    r2.gate.set()
    await archive.drain()
    rows = await snapshots()
    assert len(rows) == 1 and rows[0].body_storage == 'both'
    assert r2.objects[rows[0].content_hash] == RAW
    assert archive._unpack(rows[0].body, rows[0].enc) == RAW
    props = [p for name, p in events if name == 'tool_called']
    assert len(props) == 1 and props[0]['archive_body_upload_status'] == 'uploaded'
    assert props[0]['archive_body_upload_ms'] >= 0
    assert props[0]['archive_body_dropped'] is False


@pytest.mark.parametrize('mode,expected_rows', [('both', 1), ('r2', 0)])
async def test_upload_failure_never_publishes_r2_pointer(clients, r2, monkeypatch, mode, expected_rows):
    monkeypatch.setattr(get_settings(), 'archive_body_write', mode)
    r2.fail_puts = 100
    events = []
    monkeypatch.setattr(service.analytics, 'capture', lambda who, name, props, **kw: events.append((name, props)))
    response = await clients.get(URL)
    assert response.content == RAW and response.status_code == 200
    await archive.drain()
    rows = await snapshots()
    assert len(rows) == expected_rows and all(row.body_storage == 'db' for row in rows)
    props = [p for name, p in events if name == 'tool_called']
    assert len(props) == 1
    assert props[0]['archive_body_drop_reason'] == 'upload_failed'
    assert props[0]['archive_body_upload_status'] == 'failed'


async def test_r2_queue_has_independent_concurrency_and_sheds_observably(clients, r2, monkeypatch):
    monkeypatch.setattr(get_settings(), 'archive_r2_max_pending', 3)
    r2.gate = asyncio.Event()
    events = []
    monkeypatch.setattr(service.analytics, 'capture', lambda who, name, props, **kw: events.append((name, props)))
    for i in range(4):
        await clients.get(URL + '&count=' + str(i))
    await asyncio.sleep(0.05)
    # More than the two DB write slots can upload; no DB transaction has started yet.
    assert r2.put_calls == 3
    assert all(row.body_storage == 'db' for row in await snapshots())
    assert any(p.get('archive_body_drop_reason') == 'upload_queue_full' for _, p in events)
    r2.gate.set()
    await archive.drain()
    assert len(await snapshots()) == 4 and len(r2.objects) == 1
    assert len([p for name, p in events if name == 'tool_called']) == 4


@pytest.mark.parametrize('path', ['lookup', 'result', 'terminal'])
@pytest.mark.parametrize('fallback', [False, 'error', 'missing', 'corrupt'])
async def test_read_switches_and_fallback_without_db_connection(clients, r2, monkeypatch, path, fallback):
    monkeypatch.setattr(get_settings(), 'archive_body_read_' + path, 'r2-first')
    if path == 'terminal':
        await archive.store_terminal_response('terminal-test', 'tikhub', EP, 200, RAW)
    else:
        await clients.get(URL)
        await archive.drain()
        await audit.drain()
    r2.fail_gets = fallback == 'error'
    if fallback == 'missing':
        r2.objects.clear()
    elif fallback == 'corrupt':
        r2.objects = {key: b'corrupt' for key in r2.objects}
    if not fallback:
        # Remove only the DB test copy, proving this path really obtains bytes from R2.
        async with db.session_maker() as s:
            for row in (await s.execute(select(ArchiveSnapshot))).scalars():
                row.body = None
                s.add(row)
            await s.commit()
    if path == 'lookup':
        response = await clients.get(URL)
        assert response.headers.get('x-treg-cache') == 'hit' and response.content == RAW
    elif path == 'result':
        call = (await clients.get('/calls')).json()[0]
        response = await clients.get(f"/calls/{call['id']}/result")
        assert response.status_code == 200
        assert response.json()['response']['body_text'] == RAW.decode()
    else:
        assert await archive.load_terminal_responses([('terminal-test', EP)]) == {'terminal-test': RAW}
    assert r2.get_calls > 0


async def test_defaults_read_db_and_r2_only_has_no_db_carrier(clients, r2, monkeypatch):
    await clients.get(URL)
    await archive.drain()
    await clients.get(URL)
    assert r2.get_calls == 0
    monkeypatch.setattr(get_settings(), 'archive_body_write', 'r2')
    for _ in range(2):
        await clients.get(URL, headers={'Cache-Control': 'no-cache'})
        await archive.drain()
    rows = await snapshots()
    assert all(row.body is None and row.body_of is None and row.body_storage == 'r2' for row in rows[1:])
    monkeypatch.setattr(get_settings(), 'archive_body_read_lookup', 'r2-first')
    assert (await clients.get(URL)).content == RAW


async def test_terminal_retries_synchronously_bypassing_full_queue(clients, r2, monkeypatch):
    monkeypatch.setattr(get_settings(), 'archive_r2_max_pending_bytes', 1)
    r2.fail_puts = 2
    await archive.store_terminal_response('terminal-test', 'tikhub', EP, 200, RAW)
    assert r2.put_calls == 3
    assert (await snapshots())[0].body_storage == 'both'
    assert not archive_bodies._pending


async def test_pruner_preserves_r2_rows_and_db_carriers(clients, r2, monkeypatch):
    from datetime import timedelta
    for i in range(4):
        monkeypatch.setattr(service, 'relay', _fake_relay(200, RAW + b' ' * i))
        await clients.get(URL, headers={'Cache-Control': 'no-cache'})
        await archive.drain()
    async with db.session_maker() as s:
        for row in (await s.execute(select(ArchiveSnapshot))).scalars():
            row.fetched_at -= timedelta(days=40)
            s.add(row)
        key = (await s.execute(select(ArchiveKey))).scalar_one()
        key.last_requested_at -= timedelta(days=40)
        key.ttl_s = archive.TTL_NEVER
        s.add(key)
        await s.commit()
    assert await archive.prune_once() == 0
    assert all(row.body is not None for row in await snapshots())


@pytest.mark.parametrize('setting', ['archive_body_write', 'archive_body_read_lookup',
                                    'archive_body_read_result', 'archive_body_read_terminal'])
async def test_startup_fails_before_db_when_r2_configuration_missing(monkeypatch, setting):
    from fastapi import FastAPI
    s = get_settings()
    monkeypatch.setattr(s, 'archive_mode', 'serve')
    monkeypatch.setattr(s, setting, 'both' if setting == 'archive_body_write' else 'r2-first')
    monkeypatch.setattr(s, 'archive_object_store_endpoint', '')
    async def no_db():
        pytest.fail('startup should reject missing R2 configuration before DB I/O')
    monkeypatch.setattr(bootstrap, 'verify_db', no_db)
    with pytest.raises(RuntimeError, match='Archive R2'):
        async with bootstrap._lifespan('control')(FastAPI()):
            pass


def test_db_defaults_need_no_r2_configuration():
    from treg.config import Settings
    s = Settings(_env_file=None)
    assert s.archive_body_write == 'db'
    assert all(getattr(s, 'archive_body_read_' + p) == 'db' for p in ('lookup', 'result', 'terminal'))


async def test_checksum_mismatch_is_not_published(clients, r2, monkeypatch):
    from treg.infra.object_store import ObjectInfo
    async def wrong(body):
        return ObjectInfo('0' * 64, len(body))
    monkeypatch.setattr(r2, 'put', wrong)
    monkeypatch.setattr(get_settings(), 'archive_body_write', 'r2')
    await clients.get(URL)
    await archive.drain()
    assert await snapshots() == []


@pytest.mark.parametrize('path', ['/calls', '/calls/{ref}'])
async def test_activity_terminal_reads_release_request_session(clients, r2, monkeypatch, path):
    from treg.models import AsyncTaskRecord, CallRecord
    from treg.timeutil import utcnow_naive
    monkeypatch.setattr(get_settings(), 'archive_body_read_terminal', 'r2-first')
    await clients.get(URL)
    await archive.drain()
    await audit.drain()
    async with db.session_maker() as s:
        call = (await s.execute(select(CallRecord))).scalar_one()
        ref = call.call_ref
        s.add(AsyncTaskRecord(call_id=ref, org_id=call.org_id, endpoint_id=EP, provider='tikhub',
                              reserved_micro=0, settled_micro=0, status='settled',
                              next_check_at=utcnow_naive(), completed_at=utcnow_naive()))
        await s.commit()
    await archive.store_terminal_response(ref, 'tikhub', EP, 200, RAW)
    response = await clients.get(path.format(ref=ref))
    assert response.status_code == 200
    assert r2.get_calls > 0


async def test_upload_timeout_and_byte_shedding_are_observable(clients, r2, monkeypatch):
    events = []
    monkeypatch.setattr(service.analytics, 'capture', lambda who, name, props, **kw: events.append((name, props)))
    monkeypatch.setattr(get_settings(), 'archive_r2_timeout_s', 0.02)
    r2.gate = asyncio.Event()
    await clients.get(URL)
    await archive.drain()
    assert any(p.get('archive_body_drop_reason') == 'upload_timeout' for _, p in events)
    monkeypatch.setattr(get_settings(), 'archive_r2_max_pending_bytes', 1)
    await clients.get(URL, headers={'Cache-Control': 'no-cache'})
    await archive.drain()
    assert any(p.get('archive_body_drop_reason') == 'upload_bytes_full' for _, p in events)


async def test_obstore_client_uses_one_request_and_checks_hash_and_size():
    from treg.infra.object_store import R2ObjectStore, ObjectInfo
    from tests.fake_object_store import MemoryObstoreSDK
    sdk = MemoryObstoreSDK()
    store = R2ObjectStore(sdk, 1000, FileNotFoundError)
    digest = hashlib.sha256(RAW).hexdigest()
    assert await store.put(RAW) == ObjectInfo(digest, len(RAW))
    assert sdk.path == digest and 'sha256' not in sdk.attributes
    assert sdk.calls == ['put']
    assert await store.head(digest) == ObjectInfo(digest, len(RAW))
    assert sdk.calls == ['put', 'head']
    assert await store.get(digest) == RAW
    assert sdk.calls == ['put', 'head', 'get']
    small = R2ObjectStore(sdk, len(RAW) - 1, FileNotFoundError)
    with pytest.raises(ValueError, match='too large'):
        await small.put(RAW)
    with pytest.raises(ValueError, match='too large'):
        await small.get(digest)
    with pytest.raises(ValueError, match='content hash'):
        await store.get('../caller-controlled')
    sdk.body = b'corrupt'
    with pytest.raises(ValueError, match='checksum'):
        await store.get(digest)


async def test_dev_smoke_skips_missing_credentials(monkeypatch, capsys):
    import runpy
    for name in ('ENDPOINT', 'BUCKET', 'ACCESS_KEY_ID', 'SECRET_ACCESS_KEY'):
        monkeypatch.setenv('TREG_ARCHIVE_OBJECT_STORE_' + name, '')
    smoke = runpy.run_path('scripts/smoke_archive_r2.py')
    await smoke['run']('treg-archive-dev')
    output = capsys.readouterr().out
    assert output.startswith('SKIP:')
    assert 'TREG_ARCHIVE_OBJECT_STORE_ACCESS_KEY_ID' in output


async def test_dev_smoke_refuses_production_bucket(monkeypatch):
    import runpy
    for name, value in {'ENDPOINT': 'https://' + 'a' * 32 + '.r2.cloudflarestorage.com',
                        'BUCKET': 'treg-archive', 'ACCESS_KEY_ID': 'fake',
                        'SECRET_ACCESS_KEY': 'fake'}.items():
        monkeypatch.setenv('TREG_ARCHIVE_OBJECT_STORE_' + name, value)
    smoke = runpy.run_path('scripts/smoke_archive_r2.py')
    with pytest.raises(SystemExit, match='Refusing'):
        await smoke['run']('treg-archive')


async def test_pruner_keeps_legacy_carrier_referenced_by_both_row(clients, r2, monkeypatch):
    from datetime import timedelta
    monkeypatch.setattr(get_settings(), 'archive_body_write', 'db')
    await clients.get(URL)
    await archive.drain()
    monkeypatch.setattr(get_settings(), 'archive_body_write', 'both')
    await clients.get(URL, headers={'Cache-Control': 'no-cache'})
    await archive.drain()
    async with db.session_maker() as s:
        rows = (await s.execute(select(ArchiveSnapshot).order_by(ArchiveSnapshot.version))).scalars().all()
        assert rows[1].body_of == rows[0].id and rows[1].body_storage == 'both'
        for row in rows:
            row.fetched_at -= timedelta(days=40)
            s.add(row)
        key = (await s.execute(select(ArchiveKey))).scalar_one()
        key.ttl_s = archive.TTL_NEVER
        key.last_requested_at -= timedelta(days=40)
        s.add(key)
        await s.commit()
    assert await archive.prune_once() == 0
    assert (await snapshots())[0].body is not None


@pytest.mark.parametrize('write_mode', ['both', 'r2'])
async def test_result_admission_retains_decisive_r2_snapshot(clients, r2, monkeypatch, write_mode):
    from tests.test_cache_result_admission import FOUND, EMPTY, _call, _key
    s = get_settings()
    monkeypatch.setattr(s, 'platform_providers', 'hunter')
    monkeypatch.setattr(s, 'platform_key_hunter', 'test-platform-key')
    monkeypatch.setattr(s, 'archive_serve_endpoints', 'hunter.companies.emails')
    monkeypatch.setattr(s, 'archive_body_write', write_mode)
    monkeypatch.setattr(s, 'archive_body_read_lookup', 'r2-first')
    monkeypatch.setattr(s, 'archive_body_read_result', 'r2-first')
    await _call(clients, monkeypatch, FOUND)
    first = await _key()
    await _call(clients, monkeypatch, b'{}', live=True)
    uncertain = await _key()
    assert uncertain.result_snapshot_id == first.result_snapshot_id
    assert uncertain.result_observed_version == 2
    assert uncertain.result_state == 'found' and uncertain.ttl_s == first.ttl_s
    cached = await _call(clients, monkeypatch, EMPTY)
    assert cached.headers['x-treg-cache'] == 'hit' and cached.content == FOUND
    await _call(clients, monkeypatch, FOUND, live=True)
    assert (await _key()).stable_seen == 1  # compare hashes across an uncertain version
    await _call(clients, monkeypatch, EMPTY, live=True)
    empty = await _key()
    assert empty.result_state == 'empty' and empty.change_seen == 1
    miss = await _call(clients, monkeypatch, EMPTY)
    assert 'x-treg-cache' not in miss.headers and (await _key()).ttl_s == empty.ttl_s
    history = await archive.resolve_result(first.key_hash, archive.content_hash(FOUND))
    assert history['response']['body_text'] == FOUND.decode()
    await _call(clients, monkeypatch, FOUND)
    recovered = await _key()
    assert (recovered.result_state, recovered.stable_seen, recovered.change_seen) == ('found', 1, 2)
    if write_mode == 'r2':
        assert all(row.body_of is None for row in await snapshots())


async def test_obstore_factory_configuration_and_missing_objects(monkeypatch):
    from treg.infra.object_store import open_r2
    from tests.fake_object_store import MemoryObstoreSDK
    from obstore.store import S3Store
    import obstore.store
    from treg.config import Settings
    captured = {}
    class Missing(MemoryObstoreSDK):
        async def head_async(self, path):
            raise FileNotFoundError('absent')
        async def get_async(self, path, **kwargs):
            raise FileNotFoundError('absent')
    def factory(bucket, **kwargs):
        captured.update(kwargs)
        # Parse config with the real wheel, but perform no network I/O.
        S3Store(bucket, **kwargs)
        return Missing()
    monkeypatch.setattr(obstore.store, 'S3Store', factory)
    settings = Settings(_env_file=None, archive_object_store_bucket='treg-dev',
                        archive_object_store_endpoint='https://' + 'a' * 32 + '.r2.cloudflarestorage.com',
                        archive_object_store_access_key_id='fake', archive_object_store_secret_access_key='fake')
    async with open_r2(settings) as store:
        assert await store.head('0' * 64) is None
        assert await store.get('0' * 64) is None
    assert captured['config']['checksum_algorithm'] == 'SHA256'
    assert captured['config']['region'] == 'auto'
    assert captured['retry_config'] == {'max_retries': 0}
    assert captured['client_options'] == {'timeout': '10.0s', 'connect_timeout': '10.0s'}
