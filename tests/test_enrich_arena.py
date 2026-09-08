"""Arena's paid execution, attributed results, and repeat-safe feedback boundaries."""
import asyncio
import json
from datetime import timedelta

import pytest
from sqlmodel import select

from treg import crypto
from treg.application import arena
from treg.application.call import service
from treg.domain import arena as rules
from treg.infra.db import session_maker
from treg.models import ArenaEvaluation, ArenaRun, Hold, LedgerEntry
from treg.timeutil import utcnow_naive
from test_marketplace_call import _balance, platform_on  # noqa: F401
from test_routing import enrichment_on, _relay_by_provider  # noqa: F401

IDENTITY = {"full_name": "Test Person", "domain": "example.com"}
HUNTER_HIT = {"data": {"email": "test@example.com", "score": 90, "verification": {"status": "valid"}}}
TOMBA_HIT = {"data": {"email": "another@example.com", "score": 80, "verification": {"status": "accept_all"}}}


@pytest.fixture(autouse=True)
async def drain_arena():
    yield
    await arena.shutdown()


async def plan(c, mode="compare", providers=None, **extra):
    r = await c.post("/arena/plans", json={"capability": "people.email.find", "identity": IDENTITY,
        "mode": mode, "providers": providers or ["hunter", "tomba"], "max_cost_micro": 1_000_000, **extra})
    assert r.status_code == 200, r.text
    return r.json()


async def finish(c, quote):
    r = await c.post(f"/arena/runs/{quote['id']}/start")
    assert r.status_code == 200, r.text
    task = arena._owners.get(quote["id"])
    if task:
        await asyncio.wait_for(asyncio.shield(task), 15)
    r = await c.get(f"/arena/runs/{quote['id']}")
    assert r.status_code == 200, r.text
    return r.json()


async def test_public_page_and_tasks_but_no_anonymous_spending(clients):
    token = clients.headers.pop("X-Treg-Token")
    assert (await clients.get("/enrich-arena")).status_code == 200
    tasks = (await clients.get("/arena/tasks")).json()
    assert len(tasks) == 6
    work = next(t for t in tasks if t["id"] == "people.email.find")
    names, linkedin = work["provider_previews"]
    assert "hunter" in {p["provider"] for p in names}
    assert "hunter" not in {p["provider"] for p in linkedin}
    assert "fiber-ai" in {p["provider"] for p in linkedin}
    assert all(isinstance(p["estimate_micro"], int) and p["estimate_micro"] >= 0 for p in names)
    assert all(p["price_type"] == "per_success" for p in names)
    async with session_maker() as db:
        assert not (await db.execute(select(ArenaRun))).scalars().all()
        assert not (await db.execute(select(Hold))).scalars().all()
    assert (await clients.get("/enrich-arena/arena.js")).status_code == 200
    assert (await clients.get("/enrich-arena/../../models.py")).status_code == 404
    assert (await clients.post("/arena/plans", json={"capability":"people.email.find", "identity":IDENTITY})).status_code == 401
    for path in ("/arena/runs", "/arena/runs/anything"):
        assert (await clients.get(path)).status_code == 401
    clients.headers["X-Treg-Token"] = token


async def test_compare_shows_vendors_and_costs_before_one_click_vote(clients, enrichment_on, monkeypatch):
    seen = []
    monkeypatch.setattr(service, "relay", _relay_by_provider({"hunter": [(200,HUNTER_HIT)], "tomba": [(200,TOMBA_HIT)]}, seen))
    before = await _balance(clients)
    quote = await plan(clients)
    assert quote["estimate_micro"] > 0
    assert await _balance(clients) == before
    result = await finish(clients, quote)
    assert result["state"] == "completed" and len(seen) == 2
    assert result["revealed"] is True and result["vote"] is None
    assert {r["label"] for r in result["results"]} == {"A", "B"}
    assert all(r["state"] == "hit" for r in result["results"])
    assert {r["provider"] for r in result["results"]} == {"hunter", "tomba"}
    assert result["charged_micro"] == before - await _balance(clients)
    assert all(r["duration_ms"] >= 0 and r["raw"] for r in result["results"])
    async with session_maker() as db:
        assert not (await db.execute(select(ArenaEvaluation))).scalars().all()
    chosen = result["results"][0]["id"]
    path = f"/arena/runs/{quote['id']}"
    assert (await clients.post(path+"/evaluations",json={"kind":"winner","selected":[chosen]})).status_code == 200
    revealed = (await clients.get(path)).json()
    assert revealed["revealed"] and revealed["vote"]["selected"] == [chosen]
    assert revealed["charged_micro"] == before - await _balance(clients)
    assert revealed["charged_micro"] > 0
    assert all(r["duration_ms"] >= 0 for r in revealed["results"])
    assert {r["provider"] for r in revealed["results"]} == {"hunter", "tomba"}
    # A duplicate start, reveal, and changed vote are all inert once the run/vote is claimed.
    await clients.post(path+"/start")
    await clients.post(path+"/reveal")
    await clients.post(path+"/evaluations",json={"kind":"none"})
    assert (await clients.get(path)).json()["vote"]["selected"] == [chosen]
    assert len(seen) == 2
    async with session_maker() as db:
        assert len((await db.execute(select(ArenaEvaluation))).scalars().all()) == 1
        evaluation = (await db.execute(select(ArenaEvaluation))).scalar_one()
        feedback = arena._unpack(evaluation.payload)
        assert feedback["feedback_context"] == "attributed" and feedback["version"] == "2"
        stored = await db.get(ArenaRun, quote["id"])
        assert "example.com" not in stored.payload
        assert "example.com" in crypto.decrypt(stored.payload)


async def test_waterfall_exposes_steps_and_stops_on_first_hit(clients, enrichment_on, monkeypatch):
    seen = []
    monkeypatch.setattr(service, "relay", _relay_by_provider({"tomba": [(200,TOMBA_HIT)]}, seen))
    result = await finish(clients, await plan(clients, mode="waterfall"))
    assert len(seen) == 1
    assert [r["state"] for r in result["results"]] == ["hit", "not_attempted"]
    assert result["results"][0]["provider"] == "tomba"
    assert "first result" in result["stop_reason"]


async def test_waterfall_miss_then_hit_records_both_costs(clients, enrichment_on, monkeypatch):
    seen = []
    monkeypatch.setattr(service, "relay", _relay_by_provider({"tomba": [(200,{"data":{"email":None}})],
        "hunter": [(200,HUNTER_HIT)]}, seen))
    before = await _balance(clients)
    result = await finish(clients, await plan(clients, mode="waterfall"))
    assert [r["state"] for r in result["results"]] == ["miss","hit"]
    assert result["charged_micro"] == before - await _balance(clients)
    assert result["results"][0]["charged_micro"] == 0


async def test_compare_budget_rejects_aggregate_not_each_leg(clients,enrichment_on):
    q = await plan(clients)
    cap = max(p["estimate_micro"] for p in q["providers"])
    r = await clients.post("/arena/plans",json={"capability":"people.email.find","identity":IDENTITY,
        "providers":["hunter","tomba"],"max_cost_micro":cap})
    assert r.status_code == 422
    assert "select fewer" in r.text


async def test_own_key_priority_is_not_platform_metered(clients,enrichment_on,monkeypatch):
    await clients.post("/secrets",json={"name":"hunter","value":"OWN-KEY"})
    seen=[]
    monkeypatch.setattr(service,"relay",_relay_by_provider({"hunter":[(200,HUNTER_HIT)]},seen))
    before=await _balance(clients)
    q=await plan(clients,providers=["hunter"])
    assert q["estimate_micro"]==0 and q["providers"][0]["tier"]=="credential"
    result=await finish(clients,q)
    await clients.post(f"/arena/runs/{q['id']}/reveal")
    result=(await clients.get(f"/arena/runs/{q['id']}")).json()
    assert result["charged_micro"]==0 and await _balance(clients)==before


async def test_no_partial_or_foreign_vote_and_no_cross_origin_start(clients,enrichment_on,monkeypatch):
    gate=asyncio.Event()
    original=_relay_by_provider({"hunter":[(200,HUNTER_HIT)]},[])
    async def blocked(*args,**kwargs):
        await gate.wait()
        return await original(*args,**kwargs)
    monkeypatch.setattr(service,"relay",blocked)
    q=await plan(clients,providers=["hunter"])
    path=f"/arena/runs/{q['id']}"
    assert (await clients.post(path+"/start",headers={"Origin":"https://evil.example"})).status_code==403
    await clients.post(path+"/start")
    progress = (await clients.get(path)).json()
    assert progress["vote"] is None
    assert progress["results"][0]["provider"] == "hunter"
    assert progress["results"][0]["state"] in {"queued", "running"}
    assert (await clients.post(path+"/evaluations",json={"kind":"none"})).status_code==409
    gate.set()
    task=arena._owners.get(q["id"])
    if task: await task
    assert (await clients.post(path+"/evaluations",json={"kind":"winner","selected":["invented"]})).status_code==422
    token=clients.headers["X-Treg-Token"]
    other=(await clients.post('/users',json={"email":"other@superdesign.dev"})).json()["token"]
    clients.headers['X-Treg-Token']=other
    assert (await clients.get(path)).status_code==404
    assert (await clients.post(path+"/reveal")).status_code==404
    clients.headers['X-Treg-Token']=token


async def test_concurrent_start_dispatches_only_once(clients,enrichment_on,monkeypatch):
    seen=[]
    monkeypatch.setattr(service,"relay",_relay_by_provider({"hunter":[(200,HUNTER_HIT)]},seen))
    q=await plan(clients,providers=["hunter"])
    path=f"/arena/runs/{q['id']}"
    responses=await asyncio.gather(clients.post(path+"/start"),clients.post(path+"/start"))
    assert all(r.status_code==200 for r in responses)
    task=arena._owners.get(q['id'])
    if task: await task
    assert len(seen)==1


async def test_stale_owner_never_redispatches_and_keeps_charge_unknown(clients,enrichment_on):
    q=await plan(clients,providers=["hunter"])
    async with session_maker() as db:
        row=await db.get(ArenaRun,q['id'])
        payload=arena._unpack(row.payload)
        payload['attempts'][0].update(state='running',charged_micro=None)
        row.state='running';row.deadline_at=utcnow_naive()-timedelta(seconds=1);row.payload=arena._pack(payload)
        db.add(row);await db.commit()
    r=(await clients.get(f"/arena/runs/{q['id']}")).json()
    assert r['state']=='interrupted'
    await clients.post(f"/arena/runs/{q['id']}/reveal")
    r=(await clients.get(f"/arena/runs/{q['id']}")).json()
    assert r['charge_pending'] is True
    assert q['id'] not in arena._owners


@pytest.mark.parametrize('kind,selected', [('tie',['a','b']),('none',[]),('cannot_judge',[]),('skip',[])])
def test_non_winner_feedback(kind,selected):
    rules.validate_vote(kind,selected,[],[{'id':'a','state':'hit'},{'id':'b','state':'hit'}])


def test_false_mailbox_verdict_is_not_a_miss():
    from treg.domain.catalog import store
    cat=store.load();ep=cat.by_id['hunter.people.email.verify']
    outcome,out=rules.classify(cat.contracts['people.email.verify'],cat.adapters[ep['id']],ep,200,
        {'data':{'status':'invalid','score':0}})
    assert outcome=='hit' and out['valid'] is False


async def test_oauth_return_only_allows_arena(clients,monkeypatch):
    from types import SimpleNamespace
    from starlette.requests import Request
    from treg.routers.auth import _finish_oauth_login
    u=SimpleNamespace(id=1,token_version=0)
    for value,expected in [('/enrich-arena','/enrich-arena'),('https://evil.example','/app'),('//evil.example','/app')]:
        req=Request({'type':'http','scheme':'http','server':('registry',80),'path':'/auth/google/callback',
                     'headers':[(b'cookie',('treg_arena_return='+value).encode())]})
        response=_finish_oauth_login(req,u,None)
        assert response.headers['location']==expected


async def test_cancel_releases_reserved_credit_once(clients, enrichment_on, monkeypatch):
    entered = asyncio.Event()
    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(service, "relay", blocked)
    before = await _balance(clients)
    q = await plan(clients, providers=["hunter"])
    path = f"/arena/runs/{q['id']}"
    await clients.post(path + "/start")
    owner = arena._owners[q['id']]
    await asyncio.wait_for(entered.wait(), 5)
    async with session_maker() as db:
        assert len((await db.execute(select(Hold))).scalars().all()) == 1
    await clients.post(path + "/cancel")
    await asyncio.wait_for(asyncio.shield(owner), 5)
    await clients.post(path + "/cancel")
    assert (await clients.get(path)).json()['state'] == 'cancelled'
    assert await _balance(clients) == before
    async with session_maker() as db:
        assert not (await db.execute(select(Hold))).scalars().all()


async def test_quote_expiration_does_not_dispatch(clients, enrichment_on, monkeypatch):
    q = await plan(clients)
    async with session_maker() as db:
        row = await db.get(ArenaRun, q['id'])
        row.deadline_at = utcnow_naive() - timedelta(seconds=1)
        db.add(row)
        await db.commit()
    r = await clients.post(f"/arena/runs/{q['id']}/start")
    assert r.status_code == 409 and 'expired' in r.text
    assert q['id'] not in arena._owners


async def test_vote_racing_reveal_keeps_one_immutable_evaluation(clients, enrichment_on, monkeypatch):
    monkeypatch.setattr(service, 'relay', _relay_by_provider({'hunter': [(200, HUNTER_HIT)]}, []))
    q = await plan(clients, providers=['hunter'])
    r = await finish(clients, q)
    path = f"/arena/runs/{q['id']}"
    responses = await asyncio.gather(
        clients.post(path + '/evaluations', json={'kind': 'winner', 'selected': [r['results'][0]['id']]}),
        clients.post(path + '/reveal'))
    assert all(r.status_code == 200 for r in responses)
    async with session_maker() as db:
        assert len((await db.execute(select(ArenaEvaluation))).scalars().all()) == 1


async def test_interrupted_receipt_recovers_durable_settlement(clients, enrichment_on, monkeypatch):
    monkeypatch.setattr(service, 'relay', _relay_by_provider({'hunter': [(200, HUNTER_HIT)]}, []))
    q = await plan(clients, providers=['hunter'])
    await finish(clients, q)
    async with session_maker() as db:
        row = await db.get(ArenaRun, q['id'])
        payload = arena._unpack(row.payload)
        attempt = payload['attempts'][0]
        expected = attempt['charged_micro']
        assert expected > 0
        assert (await db.execute(select(LedgerEntry).where(LedgerEntry.call_id == attempt['call_ref'],
            LedgerEntry.kind == 'settle'))).scalar_one()
        attempt['charged_micro'] = None
        row.state = 'interrupted'
        row.payload = arena._pack(payload)
        db.add(row)
        await db.commit()
    path = f"/arena/runs/{q['id']}"
    await clients.post(path + '/reveal')
    result = (await clients.get(path)).json()
    assert result['charged_micro'] == expected and not result['charge_pending']


@pytest.mark.parametrize("capability", ["people.email.find", "people.enrich", "people.phone.find"])
async def test_incomplete_name_is_rejected_before_quote_or_charge(clients, capability):
    before = await _balance(clients)
    response = await clients.post("/arena/plans", json={
        "capability": capability, "identity": {"full_name": "jason", "domain": "example.com"}})
    assert response.status_code == 422
    assert "first and last name" in response.json()["detail"]
    assert await _balance(clients) == before
    async with session_maker() as db:
        assert not (await db.execute(select(ArenaRun))).scalars().all()
        assert not (await db.execute(select(Hold))).scalars().all()


@pytest.mark.parametrize("name", ["  Test   Person  ", "Mary-Jane O’Neill", "José García", "李 小龙"])
def test_name_validation_preserves_real_name_characters(name):
    identity = rules.validate_identity("people.email.find", {"full_name": name, "domain": "example.com"})
    assert identity["full_name"] == " ".join(name.split())


async def test_report_and_manual_vendor_extend_same_session_once(clients, enrichment_on, monkeypatch):
    seen = []
    monkeypatch.setattr(service, 'relay', _relay_by_provider({
        'tomba': [(200, TOMBA_HIT)], 'hunter': [(200, HUNTER_HIT)]}, seen))
    result = await finish(clients, await plan(clients, mode='waterfall'))
    first, remaining = result['results']
    root = '/arena/runs/' + result['id']
    assert first['can_try'] is False and remaining['can_try'] is True
    report_path = root + '/attempts/' + first['id'] + '/report'
    assert (await clients.post(report_path, json={'reason':'wrong_person', 'comment':'This belongs to someone else.'})).status_code == 200
    # Reports are durable and idempotent, independent of comparison votes.
    await clients.post(report_path, json={'reason':'other', 'comment':'Duplicate click'})
    reported = (await clients.get(root)).json()['results'][0]['report']
    assert reported['reason'] == 'wrong_person'
    before = await _balance(clients)
    path = root + '/attempts/' + remaining['id']
    q = (await clients.post(path + '/plan')).json()
    assert await _balance(clients) == before and len(seen) == 1
    starts = await asyncio.gather(*(clients.post(path + '/start', json={'quote_id':q['id']}) for _ in range(2)))
    assert sorted(r.status_code for r in starts) == [200, 409]
    task = arena._owners.get(result['id'])
    if task: await task
    extended = (await clients.get(root)).json()
    assert extended['id'] == result['id'] and extended['state'] == 'completed'
    assert len(seen) == 2
    assert extended['results'][0]['raw'] == first['raw']
    assert extended['results'][0]['report'] == reported
    extra = extended['results'][1]
    assert extra['state'] == 'hit' and extra['manual'] is True and not extra['can_try']
    assert extra['charged_micro'] == before - await _balance(clients) > 0
    assert extra['started_ms'] >= first['started_ms'] + first['duration_ms']
    assert (await clients.post(path + '/plan')).status_code == 409
    assert (await clients.post(path + '/start', json={'quote_id':q['id']})).status_code == 409
    assert len((await clients.get('/arena/runs')).json()) == 1
    async with session_maker() as db:
        row = await db.get(ArenaRun, result['id'])
        assert 'someone else' not in row.payload


async def test_manual_plan_rechecks_own_key_and_credit_admission(clients, enrichment_on, monkeypatch):
    seen = []
    monkeypatch.setattr(service, 'relay', _relay_by_provider({'tomba':[(200,TOMBA_HIT)], 'hunter':[(200,HUNTER_HIT)]}, seen))
    result = await finish(clients, await plan(clients, mode='waterfall'))
    target = result['results'][1]
    path = '/arena/runs/' + result['id'] + '/attempts/' + target['id']
    q = (await clients.post(path+'/plan')).json()
    from treg.domain import money
    original = money.balance_of
    async def empty(*args, **kwargs): return 0
    monkeypatch.setattr(money, 'balance_of', empty)
    assert (await clients.post(path+'/start',json={'quote_id':q['id']})).status_code == 402
    assert len(seen) == 1
    monkeypatch.setattr(money, 'balance_of', original)
    await clients.post('/secrets',json={'name':'hunter','value':'OWN-KEY'})
    q = (await clients.post(path+'/plan')).json()
    assert q['estimate_micro'] == 0
    before = await _balance(clients)
    assert (await clients.post(path+'/start',json={'quote_id':q['id']})).status_code == 200
    task = arena._owners.get(result['id'])
    if task: await task
    extra = (await clients.get('/arena/runs/'+result['id'])).json()['results'][1]
    assert extra['charged_micro'] == 0 and await _balance(clients) == before


async def test_manual_and_feedback_scope_validation_and_expiry(clients, enrichment_on, monkeypatch):
    monkeypatch.setattr(service, 'relay', _relay_by_provider({'tomba':[(200,TOMBA_HIT)]}, []))
    result = await finish(clients, await plan(clients, mode='waterfall'))
    target = result['results'][1]
    path = '/arena/runs/' + result['id'] + '/attempts/' + target['id']
    assert (await clients.post(path+'/report',json={'reason':'incorrect_data'})).status_code == 422
    assert (await clients.post(path+'/rating',json={'value':'down'})).status_code == 422
    assert (await clients.post(path+'/plan',headers={'Origin':'https://evil.example'})).status_code == 403
    q = (await clients.post(path+'/plan')).json()
    async with session_maker() as db:
        row = await db.get(ArenaRun, result['id'])
        payload = arena._unpack(row.payload)
        payload['attempts'][1]['manual_quote']['expires_at'] = (utcnow_naive()-timedelta(seconds=1)).isoformat()
        row.payload = arena._pack(payload)
        db.add(row)
        await db.commit()
    assert (await clients.post(path+'/start',json={'quote_id':q['id']})).status_code == 409
    token = clients.headers['X-Treg-Token']
    other = (await clients.post('/users',json={'email':'manual-other@superdesign.dev'})).json()['token']
    clients.headers['X-Treg-Token'] = other
    for suffix, body in [('plan',{}),('start',{'quote_id':q['id']}),('report',{'reason':'incorrect_data'}),('rating',{'value':'down'})]:
        assert (await clients.post(path+'/'+suffix,json=body)).status_code == 404
    clients.headers['X-Treg-Token'] = token


@pytest.mark.parametrize('mode', ['waterfall', 'compare'])
async def test_ratings_persist_without_details_and_can_change(clients, enrichment_on, monkeypatch, mode):
    seen = []
    monkeypatch.setattr(service, 'relay', _relay_by_provider({
        'tomba': [(200, TOMBA_HIT)], 'hunter': [(200, HUNTER_HIT)]}, seen))
    result = await finish(clients, await plan(clients, mode=mode))
    first = result['results'][0]
    root = '/arena/runs/' + result['id']
    path = root + '/attempts/' + first['id']
    before, calls = await _balance(clients), len(seen)
    response = await clients.post(path+'/rating', json={'value':'down'})
    assert response.status_code == 200
    rating = response.json()['rating']
    saved = (await clients.get(root)).json()['results'][0]
    assert saved['rating'] == rating and not saved.get('report')
    assert (await clients.post(path+'/rating', json={'value':'down'})).json()['rating'] == rating
    assert (await clients.post(path+'/report', json={'comment':'Wrong person'})).status_code == 200
    saved = (await clients.get(root)).json()['results'][0]
    assert saved['rating'] == rating and saved['report']['comment'] == 'Wrong person'
    assert (await clients.post(path+'/rating', json={'value':'up'})).status_code == 200
    saved = (await clients.get(root)).json()['results'][0]
    assert saved['rating']['value'] == 'up' and saved['rating']['created_at'] == rating['created_at']
    assert saved['output'] == first['output'] and saved['state'] == first['state']
    assert (await clients.post(path+'/report', json={'reason':'other'})).status_code == 409
    assert (await clients.get(root)).json()['vote'] is None
    assert await _balance(clients) == before and len(seen) == calls
    assert (await clients.post(path+'/rating', json={'value':'bad'})).status_code == 422
    assert (await clients.post(path+'/rating', json={'value':'down'}, headers={'Origin':'https://evil.example'})).status_code == 403
    async with session_maker() as db:
        row = await db.get(ArenaRun, result['id'])
        assert 'Wrong person' not in row.payload


@pytest.mark.parametrize('extra_credit,hit', [(-1, True), (0, True), (1, True), (0, False)])
async def test_waterfall_admits_cheapest_and_checks_later_balance(clients, enrichment_on, monkeypatch, extra_credit, hit):
    from treg.config import get_settings
    from treg.domain import money
    monkeypatch.setenv('TREG_PLATFORM_MARGIN', '0')
    get_settings.cache_clear()
    q = await plan(clients, mode='waterfall')
    prices = [p['estimate_micro'] for p in q['providers']]
    assert prices == sorted(prices) and q['required_micro'] == prices[0] < q['estimate_micro']
    target = prices[0] + extra_credit
    async with session_maker() as db:
        call_id = await money.reserve(db, q['org_id'], 'test.credit-spend', q['balance_micro'] - target)
        await money.settle(db, call_id)
    assert await _balance(clients) == target
    seen = []
    monkeypatch.setattr(service, 'relay', _relay_by_provider({
        'tomba': [(200, TOMBA_HIT if hit else {'data': {'email': None}})]}, seen))
    current = await plan(clients, mode='waterfall')
    assert current['affordable'] is (extra_credit >= 0)
    # Battle still requires all selected estimates, both at quote and start.
    battle = await plan(clients)
    assert battle['affordable'] is False
    assert (await clients.post('/arena/runs/'+battle['id']+'/start')).status_code == 402
    if extra_credit < 0:
        # The previously affordable quote is rechecked against the live balance.
        assert (await clients.post('/arena/runs/'+q['id']+'/start')).status_code == 402
        assert not seen
    else:
        result = await finish(clients, current)
        assert len(seen) == 1
        assert result['results'][0]['state'] == ('hit' if hit else 'miss')
        if not hit:
            assert result['results'][1]['state'] == 'error'
            assert result['results'][1]['charged_micro'] == 0
            assert 'balance' in result['stop_reason']
        assert await _balance(clients) == target - result['charged_micro'] >= 0
    async with session_maker() as db:
        assert not (await db.execute(select(Hold))).scalars().all()


async def test_waterfall_can_start_own_key_at_zero_balance(clients, enrichment_on, monkeypatch):
    from treg.config import get_settings
    from treg.domain import money
    monkeypatch.setenv('TREG_PLATFORM_MARGIN', '0')
    get_settings.cache_clear()
    await clients.post('/secrets', json={'name':'hunter', 'value':'OWN-KEY'})
    q = await plan(clients, mode='waterfall')
    async with session_maker() as db:
        call_id = await money.reserve(db, q['org_id'], 'test.credit-spend', q['balance_micro'])
        await money.settle(db, call_id)
    q = await plan(clients, mode='waterfall')
    assert q['required_micro'] == 0 and q['affordable'] and q['estimate_micro'] > 0
    assert q['providers'][0]['provider'] == 'hunter'
    seen = []
    monkeypatch.setattr(service, 'relay', _relay_by_provider({'hunter': [(200, HUNTER_HIT)]}, seen))
    result = await finish(clients, q)
    assert len(seen) == 1 and result['charged_micro'] == 0 and await _balance(clients) == 0


async def test_automatic_quotes_do_not_use_execution_limit_or_erase_history(clients, enrichment_on, monkeypatch):
    import uuid
    q = await plan(clients, mode='waterfall')
    async with session_maker() as db:
        template = (await db.get(ArenaRun, q['id'])).model_dump()
        for index in range(135):
            db.add(ArenaRun(**{**template, 'id':uuid.uuid4().hex, 'request_key':uuid.uuid4().hex,
                'created_at':utcnow_naive()-timedelta(minutes=10,seconds=index)}))
        historical = uuid.uuid4().hex
        db.add(ArenaRun(**{**template, 'id':historical, 'request_key':uuid.uuid4().hex, 'state':'completed'}))
        await db.commit()
    before = await _balance(clients)
    fresh = await plan(clients, mode='waterfall')
    async with session_maker() as db:
        assert len((await db.execute(select(ArenaRun).where(ArenaRun.state=='quoted'))).scalars().all()) == 100
        assert (await db.get(ArenaRun, historical)).state == 'completed'
    assert await _balance(clients) == before
    seen = []
    monkeypatch.setattr(service, 'relay', _relay_by_provider({'tomba':[(200,TOMBA_HIT)]},seen))
    result = await finish(clients, fresh)
    assert result['results'][0]['state']=='hit' and len(seen)==1


async def test_hourly_limit_applies_at_start_but_pricing_remains_available(clients, enrichment_on, monkeypatch):
    import uuid
    q = await plan(clients, mode='waterfall')
    async with session_maker() as db:
        template = (await db.get(ArenaRun, q['id'])).model_dump()
        for _ in range(100):
            db.add(ArenaRun(**{**template, 'id':uuid.uuid4().hex, 'request_key':uuid.uuid4().hex, 'state':'completed'}))
        await db.commit()
    seen = []
    monkeypatch.setattr(service, 'relay', _relay_by_provider({},seen))
    fresh = await plan(clients, mode='waterfall')
    response = await clients.post('/arena/runs/'+fresh['id']+'/start')
    assert response.status_code == 429 and "price previews don't count" in response.text
    assert not seen
