---
title: Enrich Arena — paid comparisons, one-click feedback, and visible waterfalls
status: shipped
sources:
  - src/treg/domain/arena.py
  - src/treg/application/arena.py
  - src/treg/routers/arena.py
  - src/treg/models.py
  - src/treg/alembic/versions/0026_enrich_arena.py
  - src/treg/domain/governance/teams.py
  - src/treg/routers/auth.py
  - src/treg/bootstrap.py
  - src/treg/web/enrich-arena.html
  - src/treg/web/enrich-arena/arena.js
  - src/treg/web/enrich-arena/arena.css
  - src/treg/web/logos/apollo.svg
  - src/treg/web/logos/branddev.svg
  - src/treg/web/logos/companyenrich.svg
  - src/treg/web/logos/findymail.svg
  - src/treg/web/logos/hunter.svg
  - src/treg/web/logos/icypeas.svg
  - src/treg/web/logos/leadmagic.svg
  - src/treg/web/logos/leadsforge.svg
  - src/treg/web/logos/lusha.svg
  - src/treg/web/logos/pdl.svg
  - src/treg/web/logos/predictleads.svg
  - src/treg/web/logos/thecompaniesapi.svg
  - src/treg/web/logos/tomba.svg
  - src/treg/web/sitetrack.js
  - tests/test_enrich_arena.py
  - tests/js/enrich-arena.test.cjs
related:
  - architecture/catalog.md
  - architecture/money.md
  - architecture/composition.md
  - interface/dashboard.md
---

# Enrich Arena

`/enrich-arena` is a public standalone Vue page, with its own navigation and bundled assets.
Its visual system follows the treg redesign reference (`https://treg-design.vercel.app/#start`):
Geist Pixel headings, Google Sans Flex body text, DM Mono for technical values, a cool gray canvas,
white rounded cards with fine borders, black actions and restrained teal status accents. A static
pixel texture echoes the reference background, while the standalone account header stays compact.
The entry screen leads with only “Enrich Arena”. The composer has a compact
task-tab strip. Small task tabs sit below the query and input type on the same row as Run.
The tab track is warm gray with a white selected tab, distinct from the pale input surface.
Waterfall/Battle controls are centered in the fighter card, with Waterfall first and step/crossed-swords icons, VS connectors
for battles and directional step arrows for waterfalls. Battle retains the internal `compare` mode value. Changing mode updates the query
and invalidates pricing without dispatching; controls are locked during a run. Old result cards
retain their recorded mode when a new query is being configured. Waterfall is the default; explicit choices in a restored draft or
URL remain respected. The header has no bottom rule and the hero uses tighter spacing.
Task tabs support arrow keys, Home/End and roving focus.
There is no Options section or editable estimate limit. Vendor avatars act as native toggle
buttons: enabled fighters have a check, excluded fighters remain dimmed and clickable. Selection
updates pricing and the saved login draft without dispatching. All services start enabled; task
or input-type changes reset the selection. No enabled services blocks submission. Avatars lock
during execution, and historical sessions restore their recorded vendor set. The existing API
$10 admission ceiling remains; the page no longer carries a hidden user budget from old drafts.
Selecting a task or input type immediately
shows a compact vendor/pricing table, including before login or input completion. `/arena/tasks`
provides catalog estimates per input variant using verified adapters, canonical derivations and
request pricing (including adapter constants and margin), with no team reads, stored quotes or
upstream calls. A ready team quote replaces these public estimates and supplies the actual
waterfall order and own-key pricing. Matching results replace the preview after dispatch; editing
the query restores its pricing preview while preserving the previous result below it. Results appear below the composer, which stays
visible with its inputs and controls disabled while a run is in progress. After completion, users
can edit the inputs and submit another run. A compact table shows each vendor's bundled logo,
key result fields, status, cost, timing and one-click thumbs up/down actions. Full fields and original
responses expand beneath a row by clicking it or pressing Enter/Space while focused. Row action
buttons act independently without toggling details. Waterfall uses the same table with numbered steps and timing bars;
its stop reason appears as a short footer. Neither mode has a results heading or aggregate-charge
bar. Either thumb records a per-result rating immediately; there is no
selection/submit step, reveal gate, none-useful button or skip button. Work-email rows flag a
returned email domain that differs from the requested company domain (subdomains are accepted),
while preserving the vendor’s answer. This signals a possible mismatch, not automatic invalidity.
Vendor icons are bundled locally from official sites, with source URLs in asset comments.
Original PNG/ICO icons are embedded in SVG wrappers to preserve the shared logo paths; existing
vector marks remain vectors. Brand.dev now redirects to Context.dev and uses its current icon.
Initials appear only as an image-load fallback.
A compact pixel-fighter lineup sits above pricing and results, using those same logos as heads.
It is idle before dispatch and follows each real attempt: running punches, hits celebrate,
misses fall, errors/timeouts show an alert, and uncalled services remain on the bench. Compare
animates concurrent active attempts; waterfall animates only its active step. A current thumbs-up winner receives a crown. A thumbs-down rating immediately overrides the celebration/crown with the
defeated pose and “Thumbs down” label; changing to thumbs up restores the recorded outcome pose.
One thumbs-up makes that result the winner, with a crown and Winner label, in either mode.
Fastest and Cheapest belts still compare successful, non-downvoted results when there is only
one thumbs-up, so those performance badges may belong to a different vendor than the winner. With multiple
thumbs-up results, Fastest and Cheapest compare only those results, and their metric winners get
crowns. No unrated vendor can outrank a thumbs-up vendor. Changing a rating recomputes the winner
and belts immediately; saved historical comparison evaluations remain intact but do not drive
these current winner indicators. Without thumbs-up votes, completed Battles with at least two
successful, non-downvoted results show Fastest and Cheapest
badges as pixel-edged belts across the fighters’ waists and as compact labels in result rows.
The belts move with the sprite and counter-mirror their text for facing opponents. They compare recorded duration and actual settled charge,
respectively; ties share badges, genuine zero charges qualify, and an unknown metric withholds
that metric's award. Running sessions never show provisional awards. Hits indicate returned data, not a ranking or verified correctness. Terminal
runs stop all looping motion; reduced-motion preferences disable animation. The lineup scrolls
horizontally for larger cohorts and has per-vendor accessible status labels.
Results start directly with the fighter card, with no query-summary row, aggregate progress count
or New query link. Inputs remain editable after completion; Stop sits in the fighter toolbar
only during an active run.
Visitors can select a task, input variant, execution mode, and services before signing in.
Submission checks the existing browser session. Email login stays on the page; Google/GitHub
accept only the allowlisted `/enrich-arena` return destination. A ten-minute session-storage draft
preserves the query through login. New users can create a team here. Valid inputs for signed-in
users fetch a quote after an 800 ms pause. The compact Run button displays the estimated cost
(waterfall: “Run from” the cheapest selected vendor’s quoted price); per-service prices appear below the input. The page
has no price-confirmation modal. Clicking a current affordable quote dispatches immediately.
An absent or expired quote is first refreshed on the button; login never automatically spends.
Changes to identity, mode, team, budget or services invalidate the displayed quote and discard
late responses. A completed run consumes its quote; a fresh run gets a new quote.
Insufficient-credit buttons open `/app#billing` with the Arena team selected via the dashboard's
`treg-active` preference, preserving an unsubmitted draft. A 402 at start uses the same path.
The navigation shows a labeled Team switcher only for users with multiple teams; a single team
is selected automatically without an extra dropdown.

Tasks are work email, person enrichment, company enrichment, phone lookup, email verification,
and email-to-LinkedIn. The catalog's existing contracts and adapters define supported inputs and
normalized result fields. Name-based comparisons require both first and last name, validated
before quote creation or upstream dispatch; use a LinkedIn URL when that input is unavailable.
Each provider contributes one eligible synchronous endpoint. Bulk jobs,
asynchronous submissions and personal-email finders are excluded from the work-email task.
`?capability=people.email.find&mode=waterfall` opens a task/mode directly.

## Execution and costs

`POST /arena/plans` validates input, applies the normal team's routing eligibility and freezes
the exact adapter requests, endpoint/adapter hashes, identity, ordering, estimates and budget in
an encrypted `ArenaRun`. A quote expires after five minutes. Creating it does not reserve credits.
Battle requires credit for the sum of estimates. Waterfall sorts selected vendors by their exact
quoted price, starts with credit for only the cheapest vendor (including an exact balance match),
and displays “Run from” that price. Own-key calls have a zero treg price. Each subsequent attempt
still passes the normal runtime balance check and the remaining run-budget check. These are admission limits at quoted prices, not a guarantee
against a higher provider-reported final charge. The UI discloses that difference.

`POST /arena/runs/{id}/start` rechecks catalog hashes and available credits. A conditional database
update claims the run once, including across web workers. Duplicate starts never dispatch again.
Execution is owned by an in-process task: compare has four concurrent legs per run; waterfall
executes serially. Each leg captures current membership/policy and calls the existing
`application.call.service.execute_call` against the direct endpoint. The ordinary call runtime
owns pricing, credential priority, authorization, reserves, settlement and cancellation cleanup.
Arena never writes balances or holds. Own keys remain unmetered by treg. Aggregator overflow is
disabled for these comparisons, and archive lookup is bypassed so runs measure fresh calls.

Database sessions are short and closed before upstream requests. Attempt state and call references
persist before dispatch. Each leg has a 90-second deadline and the run has a 240-second deadline.
Cancellation is polled between writes and interrupts in-flight tasks through the normal call
cleanup. Shutdown drains Arena owners before closing the shared HTTP client. A process-lost run
becomes interrupted after its persisted deadline; it is never automatically retried. Unknown
charges remain unknown until a durable ledger settlement/release can resolve them on a later read.
Actual run starts are limited to 100 per user/hour, with an admission check for three active runs.
Automatic price previews do not count toward that limit. Quote creation retires the oldest unused
quotes to retain at most 100 per user, preserving completed/running sessions. Start admission
serializes on the user row across drafts and teams; hitting the execution limit still allows pricing.

Waterfall uses ascending quoted prices, retaining planner order for ties, and the bounded error fallback policy. It stops
at the first structural hit: the adapter supplies the contract's required fields. Found work email
does not mean verified deliverability; phone found does not mean a live line. A negative mailbox
verification verdict is a successful answer. Each step shows queued/running, found/no match,
error/timeout, skipped/not attempted, timing, charge, and the reason for stopping or skipping.

## Additional vendor calls and issue reports

Each uncalled (`not_attempted` or `skipped`) result has a priced Try action. Completed attempts
never expose a rerun action, including errors, timeouts and interrupted manual attempts.
`POST /arena/runs/{id}/attempts/{attempt_id}/plan` rechecks current routing access for that exact
endpoint and computes the real adapter-request estimate. It stores a five-minute quote in the
run's encrypted payload without reserving credits or calling a vendor. The UI dispatches only
at or below the displayed estimate; an increase requires another click at the new price.
Insufficient balance uses the existing team top-up flow.

The matching `/start` locks the owned run, checks the quote, catalog hashes, credit admission and
active-run cap, then claims the uncalled attempt once. It reopens the same session for only that
vendor using the ordinary call runtime. This explicit extra call is admitted separately from the
original waterfall budget. Original identity, earlier results, reports, and call references stay
intact. The result carries `manual: true`; timing continues after the previous attempts. Duplicate
starts, expired quotes and already-attempted vendors cannot dispatch. Manual cancellation and
process loss use the same settlement and interruption handling as initial runs.

Thumbs up and thumbs down save immediately through
`POST /arena/runs/{id}/attempts/{attempt_id}/rating`, for returned results in either mode.
The selected thumb updates optimistically; failed saves restore the prior selection and show an error.
Thumbs up has no follow-up form. Thumbs down opens optional reason and note fields after beginning
the independent rating save; closing them keeps the rating. Either reason or note may be supplied,
and a later click on thumbs down reopens the details. The first submitted details are idempotent
through `/report`; they never overwrite an upvote from another tab. Historical reports remain intact.

Ratings store the current up/down value, creation/update timestamps and attributed context in the
attempt's encrypted payload. Repeating the same rating is idempotent; choosing the other thumb
updates it. Ratings and optional reports are private to the session owner/team, survive additional
calls, and travel with history/export under the same 30-day retention. Terminal payload reads,
ratings, reports and manual claims serialize on the run row so writes cannot erase one another.
Feedback never changes the vendor's output, hit classification or charges.

## Visible vendors and durable feedback

`GET /arena/runs/{id}` returns private, attributed snapshots, including existing runs. The UI polls
every 1.5 seconds while running and shows each vendor's queued, running or completed result as it
arrives. Comparison rows retain their frozen randomized display order; waterfall steps retain
execution order. Provider, endpoint, raw response, cost and timing are visible without voting.
Viewing a result never creates an evaluation or makes a paid call.

The page collects per-result thumbs in both modes. The previous
`POST /arena/runs/{id}/evaluations` API remains available for compatibility, including winner,
tie/none/cannot-judge/skip kinds and `POST .../reveal`. Its first evaluation remains immutable under
a run-row lock and unique run-id constraint. New thumbs do not create or rewrite those historical
comparison votes.
Version 2 evaluations carry `feedback_context: attributed`, the frozen cohort fingerprint,
exposed candidate ids/order, outcome and adapter versions. This distinguishes vendor-visible
preferences from historical blind votes. Existing evaluation rows remain immutable. The legacy
`revealed_at` column records the evaluation event; it no longer controls result visibility or
eligibility to submit the first vote. No schema change is required for this presentation change.

History appears as a left-hand session list anchored near the viewport edge, loaded for the
active team and hidden when empty. The Arena column is centered independently in the remaining
space, with a capped width on larger screens.
Selecting a session restores its inputs, mode and results with a read; New query clears the
current inputs/results while keeping saved sessions. The list refreshes after dispatch and
completion, ignores responses from a previously selected team, and clears on logout/team change.
Session switching is disabled during an active run. On narrow screens it becomes a horizontally
scrollable strip above the composer. There is no history modal. History/export do not invoke services. There is no public leaderboard or feedback-reporting
dashboard in this release; durable evaluations are the source for later analysis, not lossy audit.

## Privacy and operation

Runs and evaluations belong to both their creator and active team. Another team member cannot
read the creator's runs. Inputs, response snapshots and comments are encrypted with the existing
Fernet key. Results are bounded to 256 KB per provider. Access expires after 30 days; a bounded
lazy sweep deletes expired runs and evaluations on history/quote requests. Team deletion removes
evaluations before runs. Normal ledger retention remains unchanged.

The page opts out of PostHog autocapture and session recording through `sitetrack.js`'s
`data-private-page` flag. Inputs stay out of URLs. Mutating endpoints use the existing same-origin
guard. All Arena routes belong to the control role (and default all role), including interactive
paid execution; the dataplane's `/call/` contract is unchanged.

Alembic revision `0025` creates `arenarun` and `arenaevaluation`. Run `python -m treg upgrade`
before serving the new release. Tests cover auth/private access, aggregate admission, direct billing,
own keys, cancellation, duplicate start/vote, attributed progress/results and pre-charge name validation, waterfall progression and OAuth return.

Frontend billing-flow checks: `node --test tests/js/enrich-arena.test.cjs` exercises inline pricing,
price invalidation, login gating, duplicate clicks, quote expiry and the correct-team top-up link.
