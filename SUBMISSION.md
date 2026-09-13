# Submission Pack

Everything needed to ship, plus the four things only you can supply.

## Status

| Phase | State |
| --- | --- |
| 1. Core gate, audit chain, suppression, fixtures, tests | Done — 277 tests green |
| 2. Dry-run batch loop + Agent Skill | Done — `validate_repository.py` passes, verified |
| 3. CALL-E wiring (`plan_call` → `run_call` → `get_call_run`) | Run live: 3 calls dispatched, 4 bugs fixed, **none connected yet** |
| 4. Demo app | Done — live at https://clearedtocall.vercel.app |
| 5. Submission | You: video + Devpost (see below) |

## Done (engineering)

- 277 tests green, demo live at https://clearedtocall.vercel.app
- CALL-E CLI wired; preflight, capture-run, parse-run commands work
- Agent Skill ready; upstream PR needs one force-push (see below)
- Devpost copy ready in [`DEVPOST.md`](DEVPOST.md)
- Shoot checklist in [`VIDEO-SHOOT.md`](VIDEO-SHOOT.md)

## Left for you (two items)

### 1. Demo video (~3 min)

See [`VIDEO-SHOOT.md`](VIDEO-SHOOT.md). Most of the video uses the public demo
with no phone. The live opt-out beat needs:

- **Prior express consent** on file for `A-9001` (the gate will refuse
  `NO_CONSENT` until you set `consent_on_file: true` and a timestamp after they
  agree)
- **08:00–21:00 in the recipient's timezone** (`Asia/Kolkata` for India)
- **`--region IN`** for an Indian number
- Preflight showing `ready_to_run: true` before spending a credit

Say in the video that the policy encodes US federal rules while your test
recipient is in India; the gate reads `account.timezone`, not the country code.

### 2. Devpost form

Copy fields from [`DEVPOST.md`](DEVPOST.md). Paste the feedback survey from the
section below. Add your unlisted YouTube URL last.

## The pull request

**Open:** https://github.com/CALLE-AI/awesome-phone-call-agents/pull/574 (12 files,
`skills/cleared-to-call/` only).

**Only `skills/cleared-to-call/` goes in the PR.** The Python package, the demo
and the fixtures stay in your own repo and get linked from the Devpost entry.

### PR title

```text
feat(skills): add cleared-to-call, a pre-dial compliance gate for outbound calls
```

### PR body

```markdown
## What this adds

`skills/cleared-to-call/` — an Agent Skill that decides whether an outbound
call to a consumer is allowed to happen, before anything dials.

Outbound collection and payment-reminder calls are the highest-volume outbound
voice use case and the most legally exposed one. In the US a non-compliant call
carries $500–$1,500 in statutory damages, and the operator is liable, not the
software. This skill puts the preconditions in front of the dialer.

Four rules run pre-dial: the recipient's local call window (08:00–21:00, from
the account's IANA timezone, never inferred from the number), prior express
consent with a timestamp, the opt-out/DNC suppression list, and the mandatory
identity and purpose disclosure being present in the rendered script. A fifth
rule runs during the call: a live revocation ends the call, records `opt_out`,
and suppresses the number permanently.

Every decision — allow or refuse — appends one line to a hash-chained audit log,
so an edited, deleted, or reordered entry is detectable.

It pairs with `skills/ledger-collections-call`, which lists consumer FDCPA
collections under *When Not To Use*: this is that missing layer.

## Why it belongs here

It wraps the existing CALL-E call workflow rather than replacing it. The skill
never dials: it clears an account and hands it to whatever CALL-E route the host
already uses (`plan_call` → `run_call` → `get_call_run`). Provider separation is
preserved, and any of the outbound use cases in this repo can sit behind it.

## Contents

- `SKILL.md` — pre-dial gate, in-call revocation handler, safety boundaries,
  dry-run behaviour, how CALL-E is invoked
- `references/policy.md` — the five rules, cited authorities, and the property
  each one enforces
- `references/safety.md` — consent, E.164 masking, opt-out permanence, detection
  limits, sensitive-content boundaries
- `references/examples.md` — worked batches including every refusal case
- `scripts/` — self-contained Node implementation: `validate-input.mjs`,
  `evaluate-account.mjs`, `check-revocation.mjs`
- `assets/policy.json` — the rules as data, plus a fictional example batch

## Safety

- dry run by default; no script in this skill can place a call
- reserved fictional `555-01xx` numbers throughout
- phone numbers masked in every summary and audit entry
- explicit user intent required; no timezone, consent, or number is ever guessed
- opt-out is immediate and permanent
- no credentials in code, no recurring schedules, no hidden side effects

## Verification

```bash
python scripts/validate_repository.py            # passes
node skills/cleared-to-call/scripts/validate-input.mjs \
  --file skills/cleared-to-call/assets/example-accounts.json
```

Full test suite (277 tests) lives in the companion repo, including a parity test
that runs the Node gate and a Python implementation over the same fixtures and
compares every verdict.
```

## Devpost

- **Tagline:** Your AI can place the call. This decides whether it is allowed to.
- **Repo/PR:** the PR URL from above
- **Demo app:** https://clearedtocall.vercel.app (public, read-only: live
  calling is disabled on the deployment and the page says so)
- **Video:** unlisted YouTube link, ~3 minutes
- **CALL-E account email:** the address you logged into the CLI with
- **What it does:** a consent-and-compliance gate for AI collection calls —
  it refuses with a named reason when a call would be unlawful, honors "stop
  calling me" live, and emits a tamper-evident record proving each call was
  lawful or provably refused.

## Video script (~3 min)

Set up once, off camera:

```bash
# In fixtures/demo-live.json, set A-9001 to the consenting person's number AND
# their real IANA timezone (America/New_York, Asia/Kolkata, Asia/Singapore,
# Australia/Sydney). Rule 1 reads that timezone; it never guesses from the number.

# Confirm it dials and the destination can be checked, for free.
python -m cleared.cli preflight --accounts fixtures/demo-live.json --account-id A-9001 --region US

# Start the demo with the live button armed. Use the same --region as preflight.
python -m demo.app --accounts fixtures/demo-live.json --region US --allow-live
```

Record while it is between 08:00 and 21:00 where the recipient is. Outside that
window the gate correctly refuses the live call, which is right but costs a take.
If preflight reports the destination as unchecked, `--execute` refuses too;
decide about `--allow-unverified-destination` off camera, not during the take.

| Time | Beat | On screen |
| --- | --- | --- |
| 0:00–0:15 | The stat. "$500–$1,500 per non-compliant collection call, class actions in the millions. That is why lenders will not let AI dial." | Title card |
| 0:15–0:45 | **The refusal montage.** The batch evaluates every account *before* dialing. A-1002 refused: 06:30 local in Los Angeles. A-1003 refused: no consent on file. A-1004 refused: on the opt-out list. Each with its rule detail and audit ref. | Demo page, blocked rows |
| 0:45–1:00 | **The timezone proof.** Switch the time control from *Mid-morning* to *Late evening*. New York flips ALLOW → BLOCK; Los Angeles flips BLOCK → ALLOW. Same instant, opposite verdicts, because the gate reads each recipient's own stored timezone. | Demo page, segmented control |
| 1:00–2:15 | **The live call.** Click "Call for real" on a cleared account. It opens with the full disclosure. You say "stop calling me." The agent acknowledges and hangs up. The row flips to `opt_out`, the number lands on the suppression list. | Phone + demo page |
| 2:15–2:35 | Click **Run batch** again. That account is now `ON_SUPPRESSION_LIST`. The opt-out is permanent, with nobody in the loop. | Demo page |
| 2:35–2:50 | Another cleared account reaches promise-to-pay; the structured result fills in. | Demo page |
| 2:50–3:00 | The audit chain: every decision, hashed to the one before it, verified. "Every call provably lawful — or provably refused." | Audit panel |

Notes for the shoot:

- The time control defaults to *Mid-morning, US East*, so the three refusals are
  identical on every take. **Reset demo** restores the starting state between takes.
- The live beat needs `--allow-live` and `--accounts fixtures/demo-live.json`.
  That fixture is gitignored and holds your real number; the committed fixtures
  stay fictional, so nothing needs changing back before you push.
- The live call re-checks the gate against *real* time, not the selected preset.
  Before 07:00 UTC it will refuse — correct behaviour, but it will cost you a take.
- Budget three real calls: one rehearsal, one for the opt-out beat, one spare.
  You have about twenty.
- Do not show the terminal during `calle auth login`.

## Most Valuable Feedback survey

Observations from building against the CALL-E surface, most useful first.

1. **`plan_call` has no timezone field.** It takes `region` and `language`, so
   nothing in the platform can tell a 09:00 call from a 03:00 one. Every
   integrator has to rebuild recipient-local-time logic, and most will get
   daylight saving wrong. An optional `recipient_timezone` plus a
   platform-enforced local window would remove an entire class of liability.
2. **No opt-out signal in `get_call_run`.** Terminal statuses are transport-level
   (`COMPLETED`, `NO_ANSWER`, `VOICEMAIL`). A call in which the recipient
   demanded to never be called again returns `COMPLETED`, identical to a
   successful one. Integrators must parse `post_summary` free text to notice.
   A structured `opt_out: true` on the run result would be the single highest
   value addition in the API.
3. **No account-level suppression.** There is no `suppress_number` tool and no
   platform DNC list, so every integrator keeps their own. Two integrations on
   one CALL-E account can dial someone who opted out of the other, and neither
   knows. Even an advisory, per-account suppression list would help.
4. **Consent is invisible to the platform.** Nothing in the plan payload records
   why this number may be called. A `consent_basis` + `consent_timestamp` pair,
   stored with the run, would make consent auditable at the provider rather than
   only in the caller's own database.
5. **The `batch-runner` example goal is a compliance trap.** It instructs the
   agent to "say the following message exactly once, then end the call. Do not
   ask questions." with no identity or purpose disclosure. As the most copied
   sample in the repo, it teaches the shape of a prerecorded-voice blast that
   would need an artificial-voice identity disclosure under TCPA
   227(d)(3)(A). Adding a disclosure line to that sample would propagate widely.
6. **No mid-call event channel.** An opt-out can only be handled by prompt
   instructions to the agent. There is no webhook or callback that lets an
   integrator terminate a call on a policy trigger, so "honor the opt-out" is
   only ever as reliable as the model. A mid-call event stream, even one-way,
   would let compliance be enforced rather than requested.
7. **`docs/design-principles.md` already says the right thing about timezones**
   ("must not infer timezone from phone number, country code, locale...") but
   that principle has no enforcement anywhere in the API surface. The gap
   between the documented principle and the available fields is where
   integrators fail.

### Found by placing live calls

These came from three real dispatched calls and the review of the work around
them, not from reading documentation.

8. **A call that never rang comes back `DECLINED`.** Three calls returned
   `DECLINED` with `duration_seconds: 0`, `hangup_type: ByCallee`, identical
   start and end times, and no transcript. The phone never rang: the carrier
   rejected the international caller ID. `DECLINED` reads as "the person
   declined", and `ByCallee` says the same. Any integration that maps statuses to
   outcomes will record a consumer as having refused a call they never received.
   In collections that is a false statement in a legal record. A distinct status
   or reason code for carrier rejection would prevent it.
9. **Planning succeeds for calls that cannot connect.** On one account, NG
   destinations plan and dispatch, then fail at the carrier. On another, the same
   destination is refused at `plan_call` with a clear reason. Whether a
   destination is served by a local or an international line is account-specific
   and invisible in the plan response, so `ready_to_run: true` is not evidence a
   call will ring. Exposing the line type, and warning where international caller
   ID is routinely rejected, would let an integrator stop before spending credit.
10. **The MCP result shape silently changes what integrations read.** fastmcp 3.x
    returns a `CallToolResult` object without `model_dump`, and depending on the
    tool the payload arrives on `structured_content` or only as JSON text in
    `content`. Code that reads the wrong one sees `ready_to_run` as missing, which
    looks exactly like an unsupported destination. Two developers on this project
    hit it independently. Documenting the shape, or always populating
    `structured_content`, would remove it.
11. **MCP and REST diverge on structured results.** `plan_call` over MCP rejects
    `result_schema` with `Unexpected keyword argument`, and because unknown keys
    fail the call rather than being ignored, trying it breaks every live run.
    `POST /v1/calls` accepts task-level `result_schema` and
    `recipient_result_schema` (used by `skills/ledger-collections-call`). An
    MCP-first integrator is pushed back onto regex over the agent's prose for the
    very outcome that matters most, including whether the person opted out.
12. **The destination is only echoed masked.** The plan response shows `…9724`,
    never the full number. That is good for privacy, but it leaves an integrator
    only four digits to confirm the plan will dial the person they cleared. A
    stable keyed hash of the destination, or accepting an expected-destination
    value and refusing on mismatch server-side, would make that check exact.
13. **Transcript speaker labels are undocumented.** Opt-out detection has to read
    what the *recipient* said, so it depends on knowing which label marks the
    recipient. No connected call has been available to learn the vocabulary, and
    the docs do not state it. A wrong guess means a missed opt-out, silently.

## Prior art in the repo, and how this differs

`apps/python/consent-gate` (CALL-E ConsentGate) covers adjacent ground: consent
basis, a fixed AI disclosure, timezone and calling window, DNC suppression, and
an offline redacted manifest. Worth reading before the PR, and worth mentioning
honestly if a judge raises it.

What is different here:

- **Form.** ConsentGate is an app under `apps/python/`. This is an installable
  Agent Skill under `skills/`, portable to any skills host, no Python required.
- **Domain.** ConsentGate restricts itself to an allowlist of low-risk
  administrative purposes that explicitly excludes financial content — a
  collections call is out of scope for it by design. This is built for exactly
  that call, with per-rule citations to TCPA, FDCPA and Regulation F.
- **Rule 5.** ConsentGate is a pre-flight. This also enforces the live
  revocation, and re-reads the transcript after the call so suppression happens
  even when the agent talks past the opt-out.
- **Tamper-evidence.** A redacted manifest versus an append-only hash chain with
  a verifier and tests that prove edits, deletions and reorderings are caught.
- **Parity.** The shipped Node gate and the tested Python gate read one policy
  file, and a test compares their verdicts account by account.

`skills/ledger-collections-call` is the other collections skill upstream, and it
is a complement rather than a competitor: it lists *"Consumer FDCPA / statutory
debt-collection engines"* under **When Not To Use**. That is the layer this skill
provides. The two compose: this gate decides whether a consumer collection call
may happen at all, and a caller such as that one places it.
