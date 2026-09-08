---
title: ContactOut — LinkedIn enrichment, Starter billing and independent credit pools
status: implemented; live connect and core surface verified, capacity policy confirmation pending
sources:
  - src/treg/catalog/contactout.yaml
  - src/treg/application/call/contactout.py
  - src/treg/web/logos/contactout.svg
  - tests/test_contactout.py
  - tests/test_contactout_live.py
related:
  - architecture/catalog.md
  - architecture/auth-secrets.md
  - architecture/money.md
  - ops/capacity.md
---

# ContactOut

`oauth_providers.CONTACTOUT` is a pasted-key provider. It injects the raw credential in the
`token` header and verifies via free `GET /v1/stats`, requiring both HTTP success and
`status_code: 200`. A zero allowance does not invalidate a credential. The existing credential
ladder makes an org's tool/key win over the platform key and bypass treg billing and capacity checks.
`Settings.platform_key_contactout` reads `TREG_PLATFORM_KEY_CONTACTOUT`; `render.yaml` declares
the server slot and forwards it to the worker. The existing platform provider allow-list still
controls serving. No credential is committed or copied into a platform Secret row.

## Surface and selectors

The catalog covers count, personal/work email and phone availability, single email verification,
people and company search, company domain enrichment, email-to-LinkedIn, decision makers,
LinkedIn contact/profile enrichment, person enrichment and profile-from-email, plus own-account stats.
Free availability/count tools have distinct capabilities so they cannot be advertised as free
contact finders or profile searches. Work/personal contact splits use `email_type`; person
enrichment splits use `include`.
Phone-only contact uses `email_type=none&include_phone=true`. All split rows retain real upstream
paths. Platform calls must explicitly supply each required fixed selector; the resolver rejects a
mismatched selector before reserving or contacting the provider. BYOK keeps its ordinary relay.

Search `data_types` filters availability; it is NOT a reveal selector. Search/decision-maker
`reveal_info=true` can reveal both email types and phones. The work-only recipe is search without
reveal followed by `people.contact.work`. LinkedIn profile enrichment documents `profile_only`,
not `email_type`; profile-from-email only documents `include=work_email`. Those tools do not promise
work-only contact responses. The logo is a neutral lettermark placeholder.

Deferred: synchronous/asynchronous LinkedIn batches (v1/v2), batch email verification. Hashed-email
and campaign endpoints are also outside this requested enrichment surface.

## Starter cost model

The account owner's supplied commercial rate is authoritative:

| Unit | USD |
|---|---:|
| Work email hit | 0.15 |
| Personal email hit | 0.25 |
| Phone hit | 0.25 |
| Profile/company returned by search; company found by domain enrichment | 0.02 |
| Email-to-LinkedIn served call | 0.06 |
| Count, availability, single email verification | 0 |

Contact charges are per profile with the requested type present, not per address/phone in its
array. Work plus phone is $0.40; personal plus phone is $0.50. Person enrichment adds $0.02 when
it finds a profile. Search and decision makers add $0.02 per returned profile even without reveal.
Availability booleans and `metadata.total_results` are never billing evidence. An echoed input
email and combined email arrays are not billed again as personal-email reveals.

`cost.contactout.rates_micro` carries integer micro-USD rates in YAML. `contactout.estimate`
calculates a hold from actual request selectors, input domains, or a maximum 25-row page.
`contactout.observed` derives the final charge from returned contacts and records. The existing
reserve/settle/release lifecycle owns all money and failure cleanup. This is derived billing,
not a claim that ContactOut reports dollar costs in each response. Responses without recognizable hit evidence, including malformed payloads,
misses and embedded errors, cost zero rather than settling the maximum contact hold.

The agreed LinkedIn-profile enrichment rate is contact-only, even though public ContactOut docs
mention a search credit for profile-only/no-contact responses. Treg does not add that surcharge.
Single email verification remains free under current commercial terms; recheck if ContactOut
starts charging verifier credits. No public plan estimate replaces these commercial rates.

## Capacity: informational only

`collectors._contactout` reads `/v1/stats` through the existing worker sweep and balance script.
It preserves email, phone and search counters in the observation note with `value=None` and
`informational=True`. `snapshot_from` preserves that as a successful informational observation;
`latest_state` never derives exhaustion from it and ages it stale after the existing six-hour limit.
No independent scheduler, automatic purchase, overflow route, or guessed exhaustion signature is added.
The existing sweep cadence remains unchanged; no new 15-minute polling schedule is installed.

The public docs distinguish two meanings: prepaid `quota` is already remaining credits; postpaid
`remaining` is quota minus count. Never subtract count from a prepaid quota. The three pools are
not interchangeable and cannot honestly become one provider-wide remaining balance. The account
owner has asked ContactOut whether exhaustion stops requests or permits overages, whether pools
are isolated, and how quickly stats update / whether 15-minute free polling is supported. Until
answered, funding mode stays unknown and the observations cannot block calls.

## Live evidence — 2026-09-08

Against `https://api.contactout.com/`, using the private root-env platform credential:

- Stats: valid credential HTTP 200; garbage credential HTTP 401. The full treg
  `/connections/token` flow returned 200 and 422 respectively in an isolated test database;
  stored test credentials were removed afterward.
- Count, all availability checkers, single verification: HTTP 200.
- No-reveal people search returned one profile; company search two companies; domain enrichment
  one company; no-reveal decision makers ten profiles. All HTTP 200.
- Synthetic LinkedIn targets returned 404 on work/personal/phone contact and person enrichment;
  LinkedIn profile enrichment returned 200 with an empty profile. Synthetic email targets returned
  404 on email-to-LinkedIn and profile-from-email. These prove miss behavior, not positive contact hits.
- Before: email count/quota 0/6000; phone 0/3000; search 0/5000. Immediately after: email and phone
  unchanged; search count/quota 0/4986. The 14-credit decline matches the returned search/company
  records and confirms prepaid quota semantics. It does not prove a general update-latency guarantee.

No real contact responses were saved. Combined contact-hit billing is covered by synthetic tests;
positive contact reveals and reveal-search upstream charges were not live-verified. The catalog
therefore does not label their prices as observed. The opt-in `test_contactout_live` checks only
free connection probes, never runs in ordinary CI, and does not print credentials.
