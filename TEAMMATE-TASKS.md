# Backend work — teammate handoff

Read this before touching anything. The project is a compliance **gate**, not a
dialer: its whole value is that it refuses calls correctly and can prove it. A
change that makes the gate more permissive is a bug even when it makes a demo
smoother.

Current state: 231 tests green, the CALL-E repository validator passes with the
skill installed, and the whole loop runs in dry run with no credentials.

```bash
# Windows
.venv\Scripts\python.exe -m pytest
# macOS / Linux
.venv/bin/python -m pytest
.venv/bin/python -m cleared.cli run --now 2026-08-28T13:30:00Z --fresh
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

### B1 — Verify live response parsing against a real call — **blocking, half done**

**Where:** `cleared/calle_caller.py` extractors; `tests/data/call_run_declined.json`
(real, captured) and `tests/data/call_run_sample.json` (synthetic)

**Acceptance.** A test proves the real payload shape parses correctly, and
`normalize_speaker` maps CALL-E's actual speaker labels onto `agent` /
`recipient`. That second one matters most: `scan_transcript` only reads
recipient turns, so a mislabelled speaker means a **missed opt-out**.

**Done.** Three live calls were placed. The captured payload is committed at
`tests/data/call_run_declined.json` and `tests/test_real_payloads.py` runs the
extractors over it. Confirmed against real bytes: `extract_status` reads the
top-level `status`, `extract_summary` finds `result.post_summary`, and
`extract_transcript` returns empty when `result.transcript` is null. There are
two ways to capture the next one, and both write through `sanitize_call_run`:

- `cleared.cli run --execute --capture-payload PATH` saves the payload during a
  batch run;
- `cleared.cli capture-run --run-id <id>` fetches a run that already happened,
  and `parse-run` runs the extractors over a saved file (see `LIVE-TEST.md`).

Three bugs came out of the live calls, all fixed:

- **fastmcp 3.x returns a `CallToolResult` object with no `model_dump`**, and
  `_call_tool` tested for exactly that method, so every extractor read `None`
  and `--execute` could not get past `plan_call did not return
  ready_to_run=true`. `normalize_tool_result` now reads `structured_content`,
  then `data`, then JSON in `content[i].text`, then `model_dump`. The text path
  matters: without it, a server that answers only in text makes a dialable plan
  look like `ready_to_run: false`. The wrong-number check had also been passing
  vacuously, since it could see no numbers in an object it could not read.
- `DECLINED` mapped to `refusal`, so a call that never rang was recorded as the
  consumer refusing to pay. Every provider status now maps to `no_answer`: a
  status says whether the call connected, never what was said on it.
- `_report_from` kept only the summary and status, so a live call left no
  evidence. Fixed by the two capture paths above.

**Still open, and it is the acceptance criterion.** `normalize_speaker` has
never seen a real speaker label, because no call has connected. `AGENT_LABELS`
and `RECIPIENT_LABELS` remain guesses, and being wrong there is a silently
missed opt-out.

**Nigeria: two observations that disagree.** Recorded as seen, not reconciled:

- On one account, `preflight` against `+234` returned `ready_to_run: false` with
  a block reason naming the supported regions (US, SG, AU, IN — English).
- On another, three calls across two Nigerian carriers (0915 and 0704) planned
  and dispatched with real run ids, then came back `DECLINED` with
  `duration_seconds: 0`, `hangup_type: ByCallee`, and identical start and end
  times. The phone never rang. CALL-E's docs say NG is reachable only over their
  *international* lines, "primarily intended for testing", and international
  caller ID into Nigeria is routinely rejected at the carrier.

The likeliest explanation is that the two accounts have different lines enabled,
not that either reading is wrong. Either way, neither produces a connected call.

**Do next.** Place the one validating call to a number in a region served by a
local line (US, IN, SG or AU), or ask the CALL-E team to enable a local NG line.
Speaker labels are not region-specific, so any connected call anywhere closes
this. Capture it, commit it next to the declined sample, and add the speaker
assertions to `tests/test_real_payloads.py`.

### B2 — Declared result schema — **not possible on MCP; structured fields read when present**

**Where:** `cleared/callers.py` `build_plan_arguments`,
`cleared/calle_caller.py` `resolve_call_outcome`, `CallReport.raw`

**The brief's premise is false on this channel.** `plan_call` has no
`result_schema` parameter. Sending one fails at create time with
`1 validation error for call[plan_call] / result_schema / Unexpected keyword
argument`, and because an unknown key fails the call rather than being ignored,
attempting it breaks every live run. Declaring a schema at plan time appears to
be REST-only. Introspecting the live MCP tool gives its entire surface:

```text
plan_id  to_phones  region  language  goal
scheduled_at  retry_confirmation_action  user_input  ttl_seconds
```

`tests/test_calle_caller.py` pins that set, so a hopeful key cannot silently
break the live path again.

**What is done instead.** When `get_call_run` includes a `structured_result`,
the extractors use it and record `outcome_source: structured`; otherwise they
infer from prose and record `outcome_source: inferred`. Rule 5 re-scans the
transcript independently either way. Note that the only committed sample with a
`structured_result` is the synthetic one; the real declined payload has none, so
whether CALL-E ever returns it on MCP is still unverified.

The original B2 brief, with the schema-subset constraints from
`apps/python/leash/README.md`, is in this file's git history for whoever
revisits it against a REST integration.

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
