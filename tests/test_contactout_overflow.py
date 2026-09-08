"""ContactOut reuses the existing overflow cycle; fixtures here are synthetic."""
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import json

import httpx
import pytest

from treg.application.call import overflow as O, service as call_service
from treg.config import get_settings
from treg.domain.capacity import marks, policy, signatures
from treg.domain.capacity import routes as R
from treg.domain.catalog import store
from treg.infra.db import session_maker
from treg.models import OverflowRoute
from treg.timeutil import utcnow_naive
from test_capacity_overflow import _holds, _orthogonal, _route, overflow_on
from test_marketplace_call import _balance, _fake_relay, platform_on

EP = 'contactout.people.contact.work'
PATH = '/v1/people/linkedin'
QUERY = {'profile': 'https://linkedin.com/in/synthetic-test', 'email_type': 'work'}
QUOTA = b'{"status_code":403,"message":"You\'re out of credits, please email your sales manager"}'
BODY = {'status_code': 200, 'profile': {'work_email': ['test@example.test']}}

@pytest.fixture
def contactout_on(monkeypatch, overflow_on):
    monkeypatch.setenv('TREG_PLATFORM_PROVIDERS', 'contactout')
    monkeypatch.setenv('TREG_PLATFORM_KEY_CONTACTOUT', 'DIRECT-TEST')
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_credit_exhaustion_is_endpoint_scoped_and_access_refusal_is_not_capacity():
    signal = signatures.classify('contactout', 403, {}, QUOTA)
    assert signal.kind == 'quota' and signatures.is_exhausting(signal)
    assert marks.lock_key('contactout', EP, signal.kind) == EP
    assert signatures.classify('contactout', 403, {}, b'{"message":"No access to endpoint"}') is None
    assert signatures.classify('contactout', 429, httpx.Headers({'Retry-After':'2'}), b'').kind == 'burst'
    assert policy.default_policy('contactout', has_key=True).overflow_allowed


@pytest.mark.parametrize('status,body', [(403,QUOTA),(429,b'{"message":"Rate limit reached"}')])
async def test_capacity_failure_uses_existing_child_billing(clients, contactout_on, monkeypatch, status, body):
    await _route(endpoint_id=EP, provider='contactout', path=PATH, price_micro=550000)
    fake = _fake_relay(status, body)
    async def relay(*args, **kwargs):
        response = await fake(*args, **kwargs)
        return replace(response, raw_headers=((b'retry-after', b'0'),)) if status == 429 else response
    monkeypatch.setattr(call_service, 'relay', relay)
    seen=[]
    monkeypatch.setattr(O, '_send', _orthogonal([(200,{'success':True,'data':BODY,'priceCents':55})],seen))
    before=await _balance(clients)
    r=await clients.get('/call/'+EP,params=QUERY)
    assert r.status_code==200
    assert r.json()==BODY
    assert r.headers['X-Treg-Served-Via']=='overflow:orthogonal'
    assert before-await _balance(clients)==550000
    assert not await _holds()
    assert seen[0].json['query']['email_type']=='work'


@pytest.mark.parametrize('own,optout,status,body', [
    (True,False,403,QUOTA), (False,True,403,QUOTA),
    (False,False,404,b'{}'), (False,False,403,b'{"message":"No access to endpoint"}')])
async def test_byok_optout_and_noncapacity_errors_never_overflow(clients,contactout_on,monkeypatch,own,optout,status,body):
    await _route(endpoint_id=EP,provider='contactout',path=PATH,price_micro=550000)
    if own:
        await clients.post('/secrets',json={'name':'contactout','value':'OWN-TEST'})
    if optout:
        org=(await clients.get('/orgs')).json()[0]['org_id']
        await clients.patch(f'/orgs/{org}/settings',json={'platform_overflow':False})
    monkeypatch.setattr(call_service,'relay',_fake_relay(status,body))
    seen=[]
    monkeypatch.setattr(O,'_send',_orthogonal([],seen))
    r=await clients.get('/call/'+EP,params=QUERY)
    assert r.status_code==status and seen==[]
    assert not await _holds()


def test_only_verified_compatible_candidates_enable():
    enabled=[]
    cat=store.load()
    for row in R.load_seed():
        if row['provider']!='contactout': continue
        ep=cat.by_id[row['endpoint_id']];cv=cat.cost_view(ep['cost'],'contactout')
        route=OverflowRoute(**{k:row[k] for k in ('endpoint_id','aggregator','provider','method','path','agg_slug','agg_path','agg_unit')},agg_price_micro=round(row['agg_price_usd']*1e6),ratio=R.price_ratio(row['agg_price_usd'],R.our_event_usd(cv)),last_verified_at=datetime.fromisoformat(row['verified_at']) if row['verified_at'] else None)
        verdict=R.eligible(route,our_cost=ep['cost'],platform_eligible=True,policy=None,our_usd=cv['usd'])
        if verdict.enabled:
            enabled.append((row['endpoint_id'],row['aggregator']))
            assert row['verified_at']
            expired=R.eligible(route,our_cost=ep['cost'],platform_eligible=True,policy=None,our_usd=cv['usd'],now=route.last_verified_at+timedelta(days=8))
            assert not expired.enabled
    assert len(enabled)==13
    assert (EP,'orthogonal') in enabled
    assert (EP,'monid') not in enabled
    assert ('contactout.companies.enrich','orthogonal') not in enabled



@pytest.mark.parametrize('budget,expected_calls', [(0.5,0),(1.0,1)])
async def test_renewal_budget_includes_direct_cost_and_uses_ephemeral_profile(monkeypatch,budget,expected_calls):
    import runpy
    from types import SimpleNamespace
    from treg.domain.capacity import verify as V
    namespace=runpy.run_path('scripts/contactout_overflow_verify.py')
    main=namespace['main']
    globals_=main.__globals__
    candidate=next(dict(r) for r in R.load_seed() if r['endpoint_id']==EP and r['aggregator']=='orthogonal')
    monkeypatch.setattr(R,'load_seed',lambda:[candidate])
    globals_['get_settings']=lambda:SimpleNamespace(platform_key_contactout='DIRECT-TEST',overflow_key_for=lambda p:'AGG-TEST')
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kwargs):
            return httpx.Response(200,json={'profiles':{'one':{'li_vanity':'synthetic-renewal'}}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:Client())
    seen=[]
    async def verify(client,route,**kwargs):
        seen.append(kwargs)
        assert kwargs['test_request']['queryParams']['profile'].endswith('/synthetic-renewal')
        assert kwargs['test_request']['queryParams']['include_phone']=='true'
        return V.Verification(EP,'orthogonal',200,200,True,550000,utcnow_naive())
    monkeypatch.setattr(V,'verify_route',verify)
    result=await main(SimpleNamespace(budget_usd=budget,apply=False))
    assert len(seen)==expected_calls
    assert result==(0 if expected_calls else 1)
