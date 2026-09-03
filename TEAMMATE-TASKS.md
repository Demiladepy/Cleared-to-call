# Backend work — teammate handoff

Read this before touching anything. The project is a compliance **gate**, not a
dialer: its whole value is that it refuses calls correctly and can prove it. A
change that makes the gate more permissive is a bug even when it makes a demo
smoother.

Current state: 190 tests green, the CALL-E repository validator passes with the
skill installed, and the whole loop runs in dry run with no credentials.

```bash
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m cleared.cli run --now 2026-08-28T13:30:00Z --fresh
```

## Ground rules

1. **Never widen the policy to make something pass.** `cleared/policy.json` is
   the legal position. If a batch is blocked, the record gets fixed, not the
   rule.
2. **Predicates stay pure.** Everything in `cleared/gate.py` takes `now` as an
   argument and reads no clock, no file, no socket. That is what makes the
   policy testable at any instant in any timezone. Keep it that way.
3. **The audit log is append-only.** Never rewrite, reorder, or backdate an
   entry. If you change what goes into an entry, bump `policy_version`.
4. **No real phone numbers in committed files.** `fixtures/demo-live.json` holds
   a real number and is gitignored. Fixtures use the reserved `+1555010xxxx`
   range only.
5. **Two gates must agree.** `cleared/gate.py` (Python) and
   `skills/cleared-to-call/scripts/gate-core.mjs` (Node) implement the same
   rules, because the Agent Skill ships standalone.
   `tests/test_skill_scripts.py` runs both over every fixture and compares.
   Change one, change the other, or that test fails.

---

## Before submission

### B1 — Verify live response parsing against a real call — **blocking**

**Where:** `cleared/calle_caller.py:197` `extract_transcript`, `:213`
`extract_outcome`, `:228` `extract_promise_date`

**Problem.** These read the outcome out of free text with regexes over
`post_summary` and the agent's turns. They were written against the shapes
documented in `apps/python/batch-runner`, and have never seen a real
`get_call_run` payload. If CALL-E returns a different shape, a real
`promise_to_pay` silently degrades to `refusal`, and the demo's structured
result is wrong on camera.

**Do.**

1. Place one real call (`cleared.cli run --execute --account-id A-9001 --region NG`).
2. Save the raw `get_call_run` response to `tests/data/call_run_sample.json`,
   with the phone number masked and any token stripped.
3. Add tests that feed that payload through `extract_*` and assert the outcome,
   the promise date, and the speaker labels on the transcript.
4. Fix the extractors against reality.

**Acceptance.** A test proves the real payload shape parses correctly, and
`normalize_speaker` maps CALL-E's actual speaker labels onto `agent` /
`recipient`. That second one matters most: `scan_transcript` only reads
recipient turns, so a mislabelled speaker means a **missed opt-out**.

### B2 — Replace outcome scraping with a declared result schema — **high value**

**Where:** `cleared/callers.py` `build_plan_arguments`, `cleared/script.py`
`CALL_GOAL_TEMPLATE`

**Problem.** We ask the agent to say the outcome in prose and then pattern-match
it. CALL-E supports a `result_schema` on the call, which makes the outcome a
returned field instead of a guess.

**Constraints** (from `apps/python/leash/README.md` in the CALL-E repo, verified
live by that author): the schema subset is flat scalars only. `oneOf` is
rejected at create time, and so is a nullable type like `["string","null"]`.
Use string enums with an explicit "no clear answer" member rather than nulls.
Anything that survives create but fails extraction nulls the **entire** result
object, so keep nothing load-bearing nested.

**Do.** Declare `outcome` (enum: the six values in `cleared/schema.py:OUTCOMES`)
and `promise_date` (string, `""` when absent). Prefer the returned field, keep
the regex path as a fallback, and record which one was used.

**Acceptance.** `plan_call` accepts the schema, and a live call returns an
outcome that was never inferred from prose. Rule 5 stays independent: the
transcript re-scan in `cleared/runner.py:process_account` must keep running even
when the schema reports no opt-out.

### B3 — Duplicate-call prevention — **DONE**

**Where:** `cleared/runner.py:process_account`, `cleared/calle_caller.py:_place_call`

**Problem.** The audit entry is written *after* the call completes. If the
process dies between `run_call` and that write, the account looks uncalled and a
re-run dials the same person a second time. Two calls where the law permitted
one is exactly the failure this project exists to prevent.

**Do.** Write an `intent` audit entry *before* `run_call`, carrying the
`run_id`. On startup, any intent with no matching completion is reconciled by
calling `get_call_run` for that `run_id` — never by dialing again.

**Acceptance.** Kill the process mid-call, re-run the batch, and confirm no
second call is placed and the outcome is recovered from the provider.

**Done.** `cleared/runner.py:pending_dispatch` plus the interlock in
`process_account`. A live call now writes `intent` before the provider is
touched and `dispatched` the moment a run id exists (`CalleCaller.dispatch_hook`);
`CalleCaller.recover` finishes an in-flight run by polling `get_call_run`.
Nine tests in `tests/test_duplicate_calls.py` cover it, including a simulated
mid-call kill, recovery without re-dialling, and a recovered opt-out still
reaching the suppression list.

Two things worth knowing:

- A refusal writes `decision: "unreconciled"`, which is deliberately **not**
  terminal. Writing `block` there cleared the pending state, so the next run
  believed the account was settled and would have dialled again. A test caught
  that; do not "tidy" it back to `block`.
- Dry runs write no `intent` entry. A simulated call cannot be lost, and the
  demo's audit panel stays one line per account.

Still simulated: the kill is a raised `KeyboardInterrupt`, not a real `SIGKILL`
between processes. Worth doing for real once B1 gives you a live run id.

---

## After submission — roadmap, not hackathon scope

Do not start these before the deadline. They are listed so the scope boundary is
explicit rather than accidental, and each one is a genuine production gap.

### B4 — Concurrency-safe writes

`cleared/audit.py` caches the last hash in memory (`_cached_last_hash`) and
appends. Two processes writing the same log fork the chain and verification
fails. `cleared/suppression.py` has the same problem, and there the failure is
worse: a lost write means a suppressed number gets dialed. Needs a file lock or
a single-writer store, keeping the hash-chain semantics intact.

### B5 — A suppression store that scales

The list is JSONL loaded fully into memory on every run. Correct for 7 rows,
untenable for a real lender's DNC list. SQLite with an index on the normalized
number, behind the existing `SuppressionList` interface so nothing else changes.

### B6 — Async result intake

`cleared/calle_caller.py:_poll` blocks for up to 900 seconds per call, and
`run_batch` is strictly serial. A 500-account batch is unusable. The CALL-E repo
has a `apps/python/webhook-result-receiver` precedent worth following.

### B7 — Policy provenance

`policy_version` is a hand-edited string. It should be a hash of the policy file,
recorded in every audit entry, so an auditor can prove which exact ruleset was in
force for a given call.

---

## Explicitly out of scope

These were fenced off deliberately at the start. Adding any of them fails the
timeline and dilutes the pitch:

- a general temporal-logic model checker — the `temporal_form` strings in
  `policy.json` are specification for reviewers, and the README says so
- multi-jurisdiction support — US federal only; another region is another policy
  file, reviewed by someone qualified, not a branch inside a rule
- any rule beyond the five: no frequency caps, no reassigned-number database, no
  state-by-state variation, no litigator suppression
- real CRM or lender integration
- an auth system, a dashboard, or an SMS channel

If one of these looks necessary, raise it before writing code.
