"""ContactOut billing, faithful BYOK and independent pool monitoring."""

import json
from datetime import timedelta

import httpx
import pytest

from treg.api import app
from treg import oauth_providers as P
from treg.config import get_settings, Settings
from treg.application.call import contactout
from treg.application.call import service as call_service
from treg.application.call.types import UpstreamResponse
from treg.domain.catalog import store
from treg.domain.capacity import collectors, policy, sweep
from treg.timeutil import utcnow_naive


def cost(eid):
    return store.load().cost_view(
        store.load().by_id["contactout." + eid]["cost"], "contactout"
    )


@pytest.fixture
def platform(monkeypatch):
    monkeypatch.setenv("TREG_PLATFORM_KEY_CONTACTOUT", "PLATFORM-TEST")
    monkeypatch.setenv("TREG_PLATFORM_PROVIDERS", "contactout")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "work,personal,phone,expected",
    [
        (False, False, False, 0),
        (True, False, False, 150000),
        (False, True, False, 250000),
        (True, False, True, 400000),
        (False, True, True, 500000),
        (True, True, True, 650000),
    ],
)
def test_contact_hits_are_per_type_per_profile(work, personal, phone, expected):
    c = cost("people.linkedin.enrich")
    doc = {
        "status_code": 200,
        "profile": {
            "work_email": ["a@example.test", "b@example.test"] if work else [],
            "personal_email": ["c@example.test"] if personal else [],
            "phone": ["123", "456"] if phone else [],
            "email": ["duplicate-combined@example.test"],
            "contact_availability": {"phone": True},
        },
    }
    assert contactout.observed(c, {"queryParams": {}}, doc) == expected
    assert contactout.estimate(c, {}) == 650000


def test_search_counts_returned_profiles_and_contacts_not_availability_or_total():
    c = cost("people.search.reveal")
    doc = {
        "status_code": 200,
        "metadata": {"total_results": 10000},
        "profiles": {
            "one": {
                "contact_info": {
                    "work_emails": ["a@example.test", "b@example.test"],
                    "phones": ["1"],
                }
            },
            "two": {"contact_availability": {"personal_email": True}},
            "three": {"contact_info": {"personal_emails": ["c@example.test"]}},
        },
    }
    assert contactout.observed(c, {"body": {"reveal_info": True}}, doc) == 710000
    assert contactout.observed(c, {"body": {"reveal_info": False}}, doc) == 60000
    assert contactout.estimate(c, {"page_size": 3, "reveal_info": True}) == 2010000
    assert contactout.estimate(c, {"reveal_info": False}) == 500000
    assert (
        contactout.observed(c, {"body": {}}, {"status_code": 200, "profiles": []}) == 0
    )


def test_person_and_email_echo_and_search_surcharge():
    doc = {
        "status_code": 200,
        "profile": {
            "email": "input@example.test",
            "workEmail": "work@example.test",
            "phone": "123",
        },
    }
    c = cost("people.enrich")
    request = {
        "email": "input@example.test",
        "include": ["work_email", "personal_email", "phone"],
    }
    assert contactout.observed(c, {"body": request}, doc) == 420000
    assert contactout.observed(c, {"body": {}}, doc) == 20000
    assert (
        contactout.observed(
            cost("people.email.enrich"),
            {"queryParams": {"email": "input@example.test", "include": "work_email"}},
            doc,
        )
        == 400000
    )


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"name": "A"}, None, {}],
        {"example.test": {"name": "A"}, "missing.test": None},
    ],
)
def test_company_counts(rows):
    expected = 0 if rows == [] else 20000
    assert (
        contactout.observed(
            cost("companies.enrich"), {}, {"status_code": 200, "companies": rows}
        )
        == expected
    )


@pytest.mark.parametrize(
    "eid,params,amount",
    [
        ("people.contact.work", {"email_type": "work"}, 150000),
        (
            "people.contact.work",
            {"email_type": "work", "include_phone": "true"},
            400000,
        ),
        (
            "people.contact.personal",
            {"email_type": "personal", "include_phone": "true"},
            500000,
        ),
        ("people.contact.phone", {"email_type": "none", "include_phone": True}, 250000),
        ("people.enrich", {"include": ["phone"]}, 270000),
        ("companies.enrich", {"domains": ["a.test", "b.test"]}, 40000),
        ("people.linkedin.from-email", {}, 60000),
        ("people.email.verify", {}, 0),
    ],
)
def test_holds(eid, params, amount):
    assert contactout.estimate(cost(eid), params) == amount


async def test_connect_rejects_garbage_and_accepts_zero_pools(clients, monkeypatch):
    def reply(request):
        assert request.url.path == "/v1/stats"
        assert request.headers["token"] in ("garbage", "valid-test")
        if request.headers["token"] == "garbage":
            return httpx.Response(
                401, json={"status_code": 401, "message": "Bad credentials"}
            )
        return httpx.Response(
            200,
            json={
                "status_code": 200,
                "usage": {"quota": 0, "phone_quota": 0, "search_quota": 0},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as upstream:
        monkeypatch.setattr(app.state, "http", upstream)
        bad = await clients.post(
            "/connections/token", json={"provider": "contactout", "token": "garbage"}
        )
        assert bad.status_code == 422
        assert not (await clients.get("/tools")).json()
        good = await clients.post(
            "/connections/token", json={"provider": "contactout", "token": "valid-test"}
        )
        assert good.status_code == 200, good.text
        binding = (await clients.get("/tools")).json()[0]["bindings"][0]
        assert binding["name"] == "token" and binding["format"] == "{secret}"


def test_platform_binding(platform):
    assert Settings(_env_file=None).platform_key_for("contactout") == "PLATFORM-TEST"
    assert P.platform_bindings(P.get("contactout")) == [
        {
            "platform_setting": "platform_key_contactout",
            "injector": "env",
            "location": "header",
            "name": "token",
            "format": "{secret}",
        }
    ]
    assert not store.load().platform_eligible(
        store.load().by_id["contactout.account.usage"]
    )


async def test_pool_stats_are_informational_even_when_empty(platform):
    for extra in ({}, {"remaining": -5, "phone_remaining": 0, "search_remaining": 20}):
        usage = {
            "count": 10,
            "quota": 0,
            "phone_count": 20,
            "phone_quota": 0,
            "search_count": 30,
            "search_quota": 20,
        } | extra
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"status_code": 200, "usage": usage})
            )
        ) as c:
            row = await collectors.provider_balance("contactout", c)
        snap = sweep.snapshot_from("contactout", row)
        assert not snap.error and snap.remaining is None
        assert "email: used=10, quota=0" in snap.note
        state = policy.latest_state(
            policy.default_policy("contactout", has_key=True), snap
        )
        assert state.confidence == "informational" and state.exhausted_until is None
        old = policy.latest_state(
            policy.default_policy("contactout", has_key=True),
            snap,
            now=utcnow_naive() + timedelta(hours=7),
        )
        assert old.health == "stale" and old.exhausted_until is None


async def _balance(client):
    org = (await client.get("/orgs")).json()[0]["org_id"]
    return (await client.get(f"/orgs/{org}/balance")).json()


@pytest.mark.parametrize(
    "own,status,doc,charge",
    [
        (
            False,
            200,
            {
                "status_code": 200,
                "profile": {"work_email": ["a@example.test"], "phone": ["123"]},
            },
            400000,
        ),
        (False, 200, {"status_code": 200, "profile": {"work_email": []}}, 0),
        (False, 200, {"status_code": 403, "message": "No access"}, 0),
        (False, 403, {"status_code": 403, "message": "Out of credits"}, 0),
        (False, 429, {"status_code": 429}, 0),
        (
            True,
            200,
            {
                "status_code": 200,
                "profile": {"work_email": ["a@example.test"], "phone": ["123"]},
            },
            0,
        ),
    ],
)
async def test_platform_settles_once_and_own_key_wins(
    clients, platform, monkeypatch, own, status, doc, charge
):
    if own:
        await clients.post("/secrets", json={"name": "contactout", "value": "OWN-TEST"})

    async def relay(request, upstream_url, tool, secrets, client, **kwargs):
        assert "/v1/people/linkedin" in upstream_url
        binding = tool.bindings[0]
        assert ("secret_id" in binding) == own
        assert binding["name"] == "token"

        async def stream():
            yield json.dumps(doc).encode()

        async def close():
            pass

        return UpstreamResponse(status, (), stream(), close)

    monkeypatch.setattr(call_service, "relay", relay)
    before = await _balance(clients)
    response = await clients.get(
        "/call/contactout.people.contact.work",
        params={
            "profile": "https://linkedin.com/in/test",
            "email_type": "work",
            "include_phone": "true",
        },
    )
    assert response.status_code == status, response.text
    assert response.json() == doc
    after = await _balance(clients)
    assert before["balance_micro"] - after["balance_micro"] == charge
    entries = after["entries"]["items"]
    closing = [e for e in entries if e["kind"] in ("settle", "release")]
    assert len(closing) == (0 if own else 1)


async def test_split_must_be_explicit_and_stats_are_private(clients, platform):
    response = await clients.get(
        "/call/contactout.people.contact.work",
        params={"profile": "https://linkedin.com/in/test"},
    )
    assert response.status_code == 400
    assert "email_type" in response.text
    response = await clients.get("/call/contactout.account.usage")
    assert response.status_code in (404, 428)


def test_combined_email_array_is_not_billed_twice():
    doc = {
        "status_code": 200,
        "profile": {
            "work_email": ["work@example.test"],
            "personal_email": [],
            "email": ["work@example.test"],
        },
    }
    c = cost("people.enrich")
    assert (
        contactout.observed(
            c, {"body": {"include": ["work_email", "personal_email"]}}, doc
        )
        == 170000
    )


async def test_search_settlement_matches_14_live_results_shape(
    clients, platform, monkeypatch
):
    doc = {
        "status_code": 200,
        "metadata": {"total_results": 5000},
        "profiles": {"one": {"full_name": "Synthetic"}},
    }

    async def relay(request, upstream_url, tool, secrets, client, **kwargs):
        async def stream():
            yield json.dumps(doc).encode()

        async def close():
            pass

        return UpstreamResponse(200, (), stream(), close)

    monkeypatch.setattr(call_service, "relay", relay)
    before = await _balance(clients)
    response = await clients.post(
        "/call/contactout.people.search", json={"page_size": 25, "reveal_info": False}
    )
    assert response.status_code == 200, response.text
    after = await _balance(clients)
    assert before["balance_micro"] - after["balance_micro"] == 20000


async def test_free_verify_never_reserves_or_settles_money(
    clients, platform, monkeypatch
):
    async def relay(request, upstream_url, tool, secrets, client, **kwargs):
        async def stream():
            yield b'{"status_code":200,"data":{"status":"valid"}}'

        async def close():
            pass

        return UpstreamResponse(200, (), stream(), close)

    monkeypatch.setattr(call_service, "relay", relay)
    before = await _balance(clients)
    response = await clients.get(
        "/call/contactout.people.email.verify", params={"email": "test@example.test"}
    )
    assert response.status_code == 200, response.text
    after = await _balance(clients)
    assert before["balance_micro"] == after["balance_micro"]
    assert all(
        e["amount_micro"] == 0
        for e in after["entries"]["items"]
        if e["kind"] in ("reserve", "settle")
    )


async def test_transport_failure_releases_hold(clients, platform, monkeypatch):
    async def relay(*args, **kwargs):
        raise httpx.ReadTimeout("synthetic timeout")

    monkeypatch.setattr(call_service, "relay", relay)
    before = await _balance(clients)
    response = await clients.get(
        "/call/contactout.people.contact.work",
        params={"profile": "https://linkedin.com/in/test", "email_type": "work"},
    )
    assert response.status_code >= 500
    after = await _balance(clients)
    assert before["balance_micro"] == after["balance_micro"]
    assert len([e for e in after["entries"]["items"] if e["kind"] == "release"]) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"status_code": 401},
        {"status_code": 200, "usage": {}},
        {"status_code": 200, "usage": {"count": True, "quota": 1}},
    ],
)
async def test_bad_stats_are_not_valid_observations(platform, payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as c:
        row = await collectors.provider_balance("contactout", c)
    assert row["value"] is None and not row.get("informational")
    assert "PLATFORM-TEST" not in str(row)
    assert sweep.snapshot_from("contactout", row).error


def test_catalog_prices_validate_and_surface_is_bounded():
    from scripts.catalog_validate import check_cost

    cat = store.load()
    entries = [e for e in cat.endpoints if e.get("provider") == "contactout"]
    assert len(entries) == 21
    assert not any("batch" in e["path"] for e in entries)
    errors = []
    for e in entries:
        check_cost(e["cost"], e["id"], errors, [], e["input"])
    assert errors == []
    broken = cost("people.contact.work") | {
        "contactout": {"job": "contact", "rates_micro": {"phone": -1}}
    }
    check_cost(broken, "test", errors, [])
    assert errors


def test_free_checkers_are_not_advertised_as_contact_finders():
    cat = store.load()
    for eid in ("people.work_email.available", "people.personal_email.available", "people.phone.available"):
        assert cat.by_id["contactout." + eid]["capability"].endswith(".availability")
    assert cat.by_id["contactout.people.count"]["capability"] == "people.count"


@pytest.mark.parametrize("body", [b"", b"not json", b"[]", b"{}", b'{"status_code":200}', b'{"status_code":200,"profile":"unexpected"}'])
def test_contact_reveals_require_recognizable_hit_evidence(body):
    from types import SimpleNamespace
    from treg.application.call.settle import _observed_cost_micro
    mk = SimpleNamespace(provider="contactout", endpoint_id="contactout.people.contact.work",
                         cost_type="per_success", unit_micro=0, billed_oauth=False, request_data={})
    assert _observed_cost_micro(mk, body) == 0


def test_catalog_distribution_preserves_ids_and_global_discovery():
    from collections import Counter
    cat = store.load()
    entries = [e for e in cat.endpoints if e.get("provider") == "contactout"]
    assert Counter(e["platform"] for e in entries) == {
        "linkedin": 8, "people": 10, "companies": 2, "account": 1}
    for e in entries:
        assert e["capability"].split(".")[0] == e["platform"]
    assert cat.by_id["contactout.people.contact.work"]["platform"] == "linkedin"
    results, _ = store.search("contactout linkedin work email", cat, limit=100)
    assert any(e["id"] == "contactout.people.contact.work" for e, _ in results)
