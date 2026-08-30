# Submission Pack

Everything needed to ship, plus the four things only you can supply.

## Status

| Phase | State |
| --- | --- |
| 1. Core gate, audit chain, suppression, fixtures, tests | Done — 179 tests green |
| 2. Dry-run batch loop + Agent Skill | Done — passes `validate_repository.py` |
| 3. CALL-E wiring (`plan_call` → `run_call` → `get_call_run`) | Code done, **never run live** |
| 4. Demo app | Done — `python -m demo.app` |
| 5. Submission | Needs you: see below |

## Blocked on you

1. **CALL-E account + CLI login.** `npm install -g @call-e/cli && calle auth login`.
   The live caller reads the CLI token cache; there is no place to paste a key.
2. **A US E.164 number you own**, for the two or three real test calls and the
   recording. CALL-E does not reliably dial many non-US regions, and the whole
   demo assumes US federal rules.
3. **Confirm the jurisdiction assumption: US federal (TCPA / FDCPA / Reg F).**
   Everything in `cleared/policy.json` follows from it. If the target is
   elsewhere, rule 1's window and rule 2's consent basis both change.
4. **Your deadline**, which decides how much of the video is re-shot versus
   taken in one pass.

Until 1 and 2 exist, every command below is dry-run and spends nothing.

## The pull request

Fork `CALLE-AI/awesome-phone-call-agents`, then:

```bash
git clone https://github.com/<your-user>/awesome-phone-call-agents.git
cd awesome-phone-call-agents
python scripts/create_branch.py feat/cleared-to-call-compliance-gate
cp -r <this-repo>/skills/cleared-to-call skills/
python scripts/validate_repository.py
git add skills/cleared-to-call
git commit -m "feat(skills): add cleared-to-call compliance gate skill"
git push -u origin feat/cleared-to-call-compliance-gate
```

The branch name and commit prefix already pass their
`scripts/check_branch_name.py` and naming conventions. `validate_repository.py`
passes with the skill in place — verified locally.

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

Full test suite (179 tests) lives in the companion repo, including a parity test
that runs the Node gate and a Python implementation over the same fixtures and
compares every verdict.
```

## Devpost

- **Tagline:** Your AI can place the call. This decides whether it is allowed to.
- **Repo/PR:** the PR URL from above
- **Demo app:** optional; `python -m demo.app` locally, or deploy `demo/`
- **Video:** unlisted YouTube link, ~3 minutes
- **CALL-E account email:** the address you logged into the CLI with
- **What it does:** a consent-and-compliance gate for AI collection calls —
  it refuses with a named reason when a call would be unlawful, honors "stop
  calling me" live, and emits a tamper-evident record proving each call was
  lawful or provably refused.

## Video script (~3 min)

Set up once, off camera:

```bash
python -m cleared.cli run --now 2026-08-28T13:30:00Z --fresh   # warm the state
python -m demo.app --now 2026-08-28T13:30:00Z --allow-live     # for the live beat
```

| Time | Beat | On screen |
| --- | --- | --- |
| 0:00–0:15 | The stat. "$500–$1,500 per non-compliant collection call, class actions in the millions. That is why lenders will not let AI dial." | Title card |
| 0:15–1:00 | **The refusal montage.** The batch evaluates every account *before* dialing. A-1002 refused: 06:30 local in Los Angeles. A-1003 refused: no consent on file. A-1004 refused: on the opt-out list. Each with its rule detail and audit ref. | Demo page, blocked rows |
| 1:00–2:15 | **The live call.** Click "Call for real" on a cleared account. It opens with the full disclosure. You say "stop calling me." The agent acknowledges and hangs up. The row flips to `opt_out`, the number lands on the suppression list. | Phone + demo page |
| 2:15–2:35 | Click "Re-run batch". That account is now `ON_SUPPRESSION_LIST`. The opt-out is permanent, with nobody in the loop. | Demo page |
| 2:35–2:50 | Another cleared account reaches promise-to-pay; the structured result fills in. | Demo page |
| 2:50–3:00 | The audit chain: every decision, hashed to the one before it, verified. "Every call provably lawful — or provably refused." | Audit panel |

Notes for the shoot:

- Use `--now 2026-08-28T13:30:00Z` so the refusals are identical every take.
- The live beat needs the demo started with `--allow-live` and a fixture edited
  to your own number. Change it back afterwards.
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
