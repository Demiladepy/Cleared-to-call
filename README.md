# Cleared to Call

**A consent-and-compliance gate for AI collection calls.**
The call either happens lawfully, or provably does not.

Everyone is building AI that can place the call. This is the layer that decides
whether the call is *allowed to happen* — and refuses, with a named reason, when
it is not.

**[Live demo →](https://clearedtocall.vercel.app)** — a batch of seven fictional
accounts, three refused before dialing. Read-only: live calling is disabled on
the public deployment, so nothing there can dial anyone.

---

## The problem

Automated collection calls are the highest-volume use case in outbound voice AI,
and the most legally exposed one. In the United States a single non-compliant
call carries statutory damages of **$500–$1,500**, and class actions settle in
the millions. The rules are specific and unforgiving:

- call only between 08:00 and 21:00 **in the recipient's local time**
- call only with prior express consent on file
- never call a number on the opt-out or do-not-call list
- open with a mandatory identity and purpose disclosure
- honor "stop calling me" immediately, and permanently

When an AI agent gets any of that wrong, the **operator** is liable, not the
software. Which is why lenders who would happily let AI dial, don't.

`cleared-to-call` is the missing precondition: a gate that runs before the
dialer, refuses when the law says no, honors a live opt-out mid-call, and emits
a tamper-evident record proving each decision either way.

> *"You built collections AI that scales. This is the layer that lets it scale
> safely — so the call either happens lawfully, or provably doesn't."*

---

## What this is

Three pieces, in dependency order:

| | |
| --- | --- |
| **`skills/cleared-to-call/`** | The portable Agent Skill. This is the contribution: `SKILL.md`, the policy and safety references, and a self-contained Node implementation of the gate. Installs into any Agent Skills host and wraps any outbound CALL-E use case. |
| **`cleared/`** | A small Python package holding the same gate, the hash-chained audit log, the suppression list and the batch runner. Pure predicates, no I/O, 190 tests. |
| **`demo/`** | A thin FastAPI view that runs the batch over fixtures and shows every decision, refusal reason, transcript and audit entry. A demo, not a product. |

The Node and Python gates read **the same `policy.json`**, and a test runs both
over the same fixtures and compares the verdicts, so the shipped skill can never
drift from the tested implementation.

## What this is not

It is not a dialer, and it does not reimplement calling. CALL-E places the call;
this decides whether CALL-E is allowed to. It is not a compliance product, not
legal advice, and not a claim that a calling program is lawful — it enforces
five specific preconditions under US federal law, proves it did, and refuses
when it cannot.

---

## The policy: five rules

The rules live in [`cleared/policy.json`](cleared/policy.json) as a declarative
spec — the gate reads them, nothing hard-codes a threshold.

| # | Rule | Check | On failure |
| --- | --- | --- | --- |
| 1 | **Call window** | Local time at the recipient is inside 08:00–21:00, using the IANA timezone stored on the account | `OUTSIDE_CALL_WINDOW` |
| 2 | **Consent on file** | `consent_on_file` is true and a parseable `consent_timestamp` exists | `NO_CONSENT` |
| 3 | **Not suppressed** | The number is not on the opt-out / DNC list | `ON_SUPPRESSION_LIST` |
| 4 | **Disclosure ready** | The rendered script carries all four required disclosure elements | `MISSING_DISCLOSURE` |
| 5 | **Live revocation** | *(during the call)* On revocation: end the call, record `opt_out`, suppress the number | outcome `opt_out` |

Rules 1–4 are the pre-dial gate. All four are always evaluated — the reported
block reason is the first failure in policy order, but the audit entry records
every rule.

Rule 5 is enforced twice: the call task tells the agent to stop, and the
transcript is re-read afterwards. **The second check is the one that writes the
suppression entry**, so an agent that talks past an opt-out still ends with the
number suppressed and the promise discarded.

Each rule carries the property it enforces, stated the way a temporal-logic
specification would state it:

```text
R1  G(dial -> local_time_within(08:00, 21:00))
R2  G(dial -> consent_on_file & consent_timestamp_present)
R3  G(suppressed(number) -> G(!dial(number)))
R4  G(dial -> script_contains(all disclosure_elements))
R5  G(revocation_detected -> F(end_call & suppressed(number)))
```

These are specification, not implementation: the gate evaluates the runtime
checks, it does not model-check the formulas. They are in the policy file
because a rule stated as a property is reviewable in a way that a rule buried in
code is not. Details and citations: [`references/policy.md`](skills/cleared-to-call/references/policy.md).

**Never guessed:** timezone comes from the account record only — never from the
area code, country code, locale, or server clock. An area code says where a
number was issued, not where the person is standing. A missing timezone is a
block, not an assumption.

---

## Quickstart

Requires Python 3.11+ and Node 20+. No credentials, no credits, nothing dials.

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[demo,dev]"
```

Run the batch in dry run:

```bash
python -m cleared.cli run --now 2026-08-28T13:30:00Z --fresh
```

```text
ACCOUNT   DECISION REASON / OUTCOME       DISCLOSED  AUDIT
A-1001    ALLOW    promise_to_pay         yes        aud_188921a1
A-1002    BLOCK    OUTSIDE_CALL_WINDOW    -          aud_78d57285
A-1003    BLOCK    NO_CONSENT             -          aud_27087e66
A-1004    BLOCK    ON_SUPPRESSION_LIST    -          aud_68e9f7a9
A-1005    ALLOW    opt_out                yes        aud_faf58034
A-1006    ALLOW    no_answer              -          aud_6d925ab8
A-1007    ALLOW    dispute                yes        aud_6fa076e8

7 account(s): 3 blocked, 4 called, 1 opt-out(s).
  opt-out - A-1005 said "Stop calling me. I do not want these calls." -> number suppressed

Audit chain: verified (7 entries).
```

Run it again without `--fresh` and A-1005 is now blocked `ON_SUPPRESSION_LIST`.
The opt-out is permanent, with no further human action.

Other commands:

```bash
python -m cleared.cli evaluate --account-id A-1002   # one decision, as JSON
python -m cleared.cli verify                          # verify the audit chain
python -m cleared.cli policy                          # print the active policy
```

The demo view:

```bash
python -m demo.app --now 2026-08-28T13:30:00Z
```

Tests:

```bash
python -m pytest
```

### The skill on its own

The skill has no dependencies beyond Node and needs nothing from this
repository:

```bash
cd skills/cleared-to-call
node scripts/validate-input.mjs --file ../../fixtures/accounts.json
node scripts/evaluate-account.mjs --file ../../fixtures/accounts.json \
  --account-id A-1002 --now 2026-08-28T13:30:00Z
node scripts/check-revocation.mjs --utterance "stop calling me"
```

Exit codes: `0` allow, `2` block, `3` revocation detected, `1` unusable input.

---

## Placing real calls

Dry run is the default everywhere. Live calling needs an explicit flag, a
logged-in CALL-E CLI, and a number you are authorized to call.

```bash
npm install -g @call-e/cli
calle auth login
pip install -e ".[live]"

python -m cleared.cli run --execute --account-id A-1001
```

`DRY_RUN=1` in the environment refuses `--execute` outright.

The cleared path is: `plan_call` → **inspect the plan** → `run_call` →
`get_call_run` → transcript → opt-out re-check. A plan that names any number
other than the cleared account's is refused before it runs.

The demo's per-account call button exists only when the app is started with
`--allow-live`, and a blocked account has no path to a dial under any flag.

**Fixtures use reserved fictional `555-01xx` numbers.** Replace one with a real
number only for a test call to a phone you own.

---

## The audit record

One append-only JSONL line per decision, blocked or called. Each line carries
the hash of the line before it:

```json
{"account_id":"A-1002","audit_ref":"aud_78d57285","block_reason":"OUTSIDE_CALL_WINDOW",
 "decision":"block","dry_run":true,"hash":"91fae5a8...","outcome":"not_called",
 "phone_masked":"+1******1235","policy_id":"us-federal-collections","policy_version":"1.0.0",
 "prev_hash":"bc97e26d...","rules_evaluated":{"R1":"fail","R2":"pass","R3":"pass","R4":"pass"},
 "timestamp":"2026-08-28T13:30:00+00:00"}
```

Editing, deleting, or reordering any entry breaks the chain from that point on,
and `verify` reports the first bad index. Recomputing the tampered entry's own
hashes does not help — the next entry's `prev_hash` no longer matches.

Phone numbers in the log are always masked. The full number lives in exactly two
places: the suppression list, which has to match real dial targets, and the dial
payload itself.

---

## Layout

```text
cleared/           gate, policy loader, audit chain, suppression, runner, CALL-E caller
  policy.json      the five rules, the call window, the disclosure elements
demo/              FastAPI view over a batch run
fixtures/          7 fictional accounts covering every branch, seed suppression list
skills/cleared-to-call/
  SKILL.md         progressive-disclosure entry point
  references/      policy.md, safety.md, examples.md
  scripts/         self-contained Node gate + helpers
  assets/          policy.json (identical to the package copy, checked by a test)
tests/             190 tests: rules, audit chain, revocation, runner, parity, demo
```

## Scope and limits

Deliberately not built:

- **One jurisdiction.** US federal (TCPA / FDCPA / Reg F) only. Another region
  needs another policy file reviewed by someone qualified to write it, not a
  branch inside an existing rule.
- **Five rules.** No state-level variations, frequency caps, reassigned-number
  database, or litigator suppression.
- **No verification engine.** The temporal properties are specification. Rules
  compile to simple runtime checks, and that is the whole point.
- **Fixtures only.** No lender, CRM, or servicing-system integration.
- Not legal advice. The operator remains responsible for their compliance
  position.

Roadmap, in rough order of value: state-level call-window and frequency rules;
EU/UK and APAC policy files; a reassigned-number check before R2; consent
provenance in the audit entry; signed audit anchors so the chain can be attested
externally.

## License

MIT.
