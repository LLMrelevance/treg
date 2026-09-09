---
title: Feedback - private intake for problems and suggestions
status: shipped
sources:
  - src/treg/feedback_contract.py
  - src/treg/domain/feedback/__init__.py
  - src/treg/domain/feedback/reports.py
  - src/treg/domain/feedback/reviews.py
  - src/treg/hints.py
  - src/treg/application/feedback.py
  - src/treg/routers/feedback.py
  - src/treg/alembic/versions/0025_feedback.py
  - src/treg/alembic/versions/0026_callreview.py
  - src/treg/web/feedback.md
  - tests/test_feedback.py
  - tests/test_reviews.py
  - tests/test_hints.py
related:
  - architecture/data-model.md
  - architecture/mcp-oauth.md
  - architecture/super-admin.md
  - interface/skill.md
  - interface/cli.md
---

# Feedback

`FeedbackCategory` is the shared four-value vocabulary: `quality`, `pricing`, `friction`, `other`.
`FeedbackIn` accepts only `category`, `message` (trimmed, 1-2,000 characters), optional `call_ids`
(at most 100 bounded opaque references, deduplicated), and optional public `endpoint_id`.
Guidance encourages proactive reports of small annoyances and observed friction even after successful workarounds, without requiring
a proven bug. It directs agents to pass references in `call_ids` (CLI `--call-id`), not only prose,
and to continue the task after reporting an issue once. Extra fields are rejected. Privacy instructions ask callers to replace sensitive values and omit
raw payloads; free-text content is not guaranteed anonymous or automatically sanitized.

`POST /feedback` uses `require_member`, including agent identities and the existing public-demo
write restriction. `application.feedback.submit` commits the report and a per-team rate-limit
hit in one transaction before acknowledging HTTP 201 with `feedback_id` and `status: received`.
The intake allows 30 reports per team per hour. It spends no balance, calls no provider and uses
no best-effort audit writer. Storage failures cannot produce a success acknowledgement.

`call_ids` and `endpoint_id` remain submitted claims. `verified_call_ids` is the subset found in
the submitting team's `CallRecord.call_ref` or `LedgerEntry.call_id`; no cross-team lookup runs.
Missing or delayed audit records do not reject a report. Verified provenance does not establish
that the reported problem is true. Intake does not rank providers or adjust charges.

`GET /feedback/{feedback_id}` returns the report only to its team; other teams receive 404.
`GET /admin/feedback` uses `require_superadmin` and the admin pool, with category filtering and
bounded descending-ID pagination (`limit`, `before`, `next_before`). It returns internal
attribution too. There is no external notification, issue sync, public feed, or review workflow.
`Feedback` participates in `ORG_SCOPED_MODELS`, so team deletion removes its reports.

CLI `cmd_feedback` sends the same payload to its configured registry, reading a prepared message
from stdin when the message argument is `-` (a terminal is rejected instead of blocking).
`cmd_feedback_get` retrieves a report through the same team-scoped HTTP read. The CLI rejects
empty or oversized messages locally and emits structured errors without echoing rejected input;
transport failures leave submission outcomes explicitly unconfirmed. Both MCP surfaces expose `feedback` with an enum in
their input schema, relay to the same HTTP intake, and declare a non-destructive, non-idempotent
local write. Their existing call permissions and transport boundaries remain distinct.

`skill.md` mentions feedback in its description and links to `{BASE}/feedback.md`, served by
`feedback_md` with the deployment's base URL. Detailed syntax and privacy guidance live in that
one document; CLI help and MCP share `FEEDBACK_DESCRIPTION`. The plugin generator propagates the
short skill instructions to each installation format. Self-hosted submissions stay on the
configured registry.

## Call review storage

`CallReview` (`callreview`, revision 0026) stores one rating per unique `call_id`, with team and
caller identity, server-attributed endpoint/provider, optional `routed_via`, `invited`, request
`client`, usefulness, optional reason and creation time. Endpoint/time has a composite index.
`ORG_SCOPED_MODELS` includes reviews for team deletion. Reviews never create feedback reports.
`ReviewUsefulness` is `useful`, `partly`, `not_useful`, or `not_sure`; shared guidance asks agents
to rate after using the result and continue their task.


`application.feedback.submit_review` owns one transaction. It looks up call references only in
its caller's team (audit and ledger); a missing audit record receives a retryable 404, including
ledger-only evidence, which lacks status/provider/cache attribution. Own-tool records receive
400. A routed parent uses its successful child's endpoint/provider when present, retaining the
parent endpoint as `routed_via`; otherwise it retains parent attribution. `invited` is recomputed
from a 2xx, non-cached record and the current review sampling rate. Retries return the original
ID and `already_reviewed`; a unique index also arbitrates concurrent submissions. The sole writer
is `domain.feedback.reviews`; the moved `reports` module preserves feedback behavior.

`hints.sampled(kind, sample_id)` hashes `kind:sample_id` with SHA-256 into the same 64-bit bucket
construction for both kinds. `TREG_REVIEW_SAMPLE_RATE` and `TREG_FEEDBACK_HINT_RATE` are bounded
0..1 floats, default 0. Sampling is local and deterministic under the current configuration.

`POST /reviews` uses `require_member` and returns 201 (`review_id`, `status: received`) on
insertion or 200 on retry. `ReviewIn` rejects extra fields, requires a bounded `CallReference`
and usefulness enum, and trims an optional 1-200 character reason with feedback's privacy rules.
`GET /admin/reviews` is superadmin-only, uses the admin pool, and provides bounded descending-ID
pagination with optional `endpoint_id`. It is excluded from OpenAPI; there is no team read route.

## Optional review and feedback invitations

`routers.call.call_tool` sets `X-Treg-Review: requested` after constructing the streaming response,
before streaming starts. Only resolved catalog calls (including routed parents) with a 2xx status,
no idempotent-replay header, no `X-Treg-Cache: hit` archive signal, and a sampled call reference
qualify. An own tool never qualifies, even if its name matches a catalog endpoint. The whole hook
is best-effort, has no database or body access, and does not change call service exits or writes.
Plain HTTP gets only the header. Both MCP transports retain `call_id` and use their single hint
slot with priority replay > 402 > review > feedback. Review invites rating after use; feedback
remains the existing proactive-friction text. The upstream body is unchanged.

The config-driven sampler replaces the PostHog flag poller completely; both MCP lifespans only
own their transport lifecycle. Feedback hints remain limited to successful calls without a higher
priority hint; missing call references use a fresh sampling ID without inventing a public call ID.
`mcp_hint_attached` is a best-effort analytics event with `kind`, `surface` and available `call_id`,
never upstream contents or credentials. Attachment does not prove display or reading. No session
reminder cap or adaptive sampling is implemented.
