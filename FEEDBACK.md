# Feedback for CALL-E

Written while building **Cleared to Call**, a pre-dial compliance gate for
outbound consumer collection calls, against the live CALL-E MCP surface.

Everything below came from contact with the running platform, not from reading
docs. Where a claim is based on a single observation I say so. Findings are
ordered by how much they would change what an integrator has to build.

**Context that shapes all of it:** US collections calls carry $500–$1,500 in
statutory damages per non-compliant call, and the *operator* is liable, not the
platform. That makes the difference between a structured field and a sentence of
prose a legal difference, not a convenience one.

**What already works well, and why it matters:** the `plan_call` → inspect →
`run_call` split is the single best thing in the API. A two-phase dispatch with a
confirm token is exactly the shape a compliance layer needs, because it creates a
moment where software can refuse. Most of what follows is asking for that same
rigour in the payloads on either side of it.

---

## 1. A result schema exists over REST and not over MCP

**What.** `apps/python/leash` declares a flat `result_schema` on `POST /v1/calls`
and its README documents the constraints in detail (flat scalars, no `oneOf`, no
nullable types). The MCP `plan_call` tool rejects the same key:

```
1 validation error for call[plan_call]
result_schema
  Unexpected keyword argument
```

Introspecting the live tool gives its whole surface — `plan_id`, `to_phones`,
`region`, `language`, `goal`, `scheduled_at`, `retry_confirmation_action`,
`user_input`, `ttl_seconds` — with no field for a declared result under any name.

**Impact.** Every MCP integrator has to ask the agent to *say* the outcome in
prose and then pattern-match it back out. We ship regexes over `post_summary`
that decide whether a consumer promised to pay, disputed the debt, or asked never
to be called again. Those are legally significant claims about a person,
recovered by regex, into an append-only audit log.

Two builders in this repository solved the same problem in opposite ways because
they happened to pick different transports. Nothing signals that the transports
differ in capability.

**Fix.** Accept `result_schema` on `plan_call` with the same subset REST allows.
If that is not on the roadmap, say so in the MCP docs, because right now the
most-cited example of structured results is a REST app and readers assume it
generalises.

---

## 2. `get_call_run` has no structured opt-out signal

**What.** Terminal statuses are transport-level: `COMPLETED`, `NO_ANSWER`,
`VOICEMAIL`, `BUSY`, `DECLINED`. A call in which the recipient said *"take me off
your list and never call me again"* returns `COMPLETED` — identical to a call
that ended with a payment arrangement.

**Impact.** This is the highest-value gap in the API. Under the TCPA an opt-out
must be honoured immediately and permanently, and the only way to detect one over
MCP is to scan the transcript yourself. We do it twice: we instruct the agent to
hang up, then re-read the transcript afterwards and suppress the number
regardless of what the agent did. The second pass exists purely because the
platform cannot tell us.

An integrator who trusts the status field will re-dial someone who revoked
consent. That is the single most expensive mistake available in this domain, and
the API currently makes it the default behaviour.

**Fix.** `opt_out: true` on the run result, with the matched utterance. Even
advisory and best-effort, it converts a silent failure into a visible one.

---

## 3. `DECLINED` conflates a carrier rejection with a person refusing

**What.** Three live calls to Nigerian numbers returned `DECLINED` with
`duration_seconds: 0`, identical start and end timestamps, no transcript, and
`hangup_type: ByCallee`. The phone never rang: this was the carrier rejecting an
international caller ID, not a human declining.

**Impact.** We had mapped `DECLINED` → `refusal`, which is the outcome meaning
*"the consumer refused to arrange payment."* A call that never connected was
being written into a tamper-evident compliance record as a consumer's decision.
We caught it because the duration was zero, and now map every provider status to
`no_answer`: a status describes whether the call *connected*, never what was
*said* on it.

`ByCallee` is actively misleading here. No callee was involved.

**It is not one country and not one status.** A later call to an Indian number,
which `plan_call` had accepted with `ready_to_run: true`, returned the identical
signature under a *different* status: `FAILED`, `duration_seconds: 0`,
`started_at` and `ended_at` both `2026-09-14T05:07:48Z`, `hangup_type: ByCallee`,
no transcript. Two countries, two carriers, two status strings, one underlying
cause, and nothing in the vocabulary distinguishes it from a person hanging up.

**Worse, the platform's own summary misdiagnoses it.** That run's `post_summary`
reads: *"The first call did not connect or complete; the recipient may be busy or
unavailable, so you can confirm retrying in about 45 minutes."* The recipient was
neither busy nor unavailable; their carrier rejected the caller ID, and retrying
in 45 minutes will fail identically. In a collections context that is advice to
re-dial someone who was never reached, and call frequency is itself regulated. An
integrator who surfaces `post_summary` to an operator is passing on a wrong
diagnosis with a recommendation attached.

**Fix.** Separate the transport outcome from any attribution. A `failure_reason`
distinguishing `carrier_rejected`, `unallocated_number`, `user_busy` and
`user_declined` would let integrators tell "we could not reach them" from "they
said no" — a distinction that matters in a regulated record.

---

## 4. Destination reachability is not binary, and is not documented anywhere

**What.** `region: "NG"` is accepted, `plan_call` returns `ready_to_run: false`
with prose, and dialling fails at the carrier. `region: "IN"` returns
`ready_to_run: true` and plans cleanly. The difference is that Nigeria is
reachable only over international lines that Nigerian carriers routinely reject,
which we learned from support material rather than from the API.

No endpoint or document lists which destinations are reachable, or whether a
destination needs a local line provisioned.

**`ready_to_run: true` is not a reachability guarantee.** This is the part that
cost us most. India planned cleanly — `ready_to_run: true`, a plan id, a confirm
token — and then failed at the carrier with zero duration. The only signal that
would have saved the credit is one the platform does not expose. `plan_call` is
otherwise an excellent pre-flight; it validates everything except whether the
call can physically be delivered.

**Impact.** A team picks a demo market, builds for it, and discovers at the first
live call that their market is unreachable. We lost a day to Nigeria, re-sourced
a test number in India, and lost the credit there too. The information exists
inside CALL-E; it just is not queryable.

**Fix.** Either a `get_destination_support(region)` tool or a published matrix
with a "local line required" column, and ideally a reachability check folded into
`plan_call` so `ready_to_run` means what it says. Failing that, make `plan_call`'s refusal
name the cause in a structured field rather than in a sentence.

---

## 5. `plan_call` never echoes the destination in a structured field

**What.** The plan response includes the destination only inside
`confirm_summary`, masked, mid-sentence: *"Ready to place the call immediately to
…4692 in English."*

**Impact.** A compliance layer must verify that the plan it is about to run
targets the number it cleared, and not a different person. The only way to do
that today is to parse prose and match the last four digits. Our first
implementation of that check passed **vacuously** — it found no numbers in the
payload and approved everything, which we only caught by capturing a real
response. A wrong-number check that silently always passes is worse than none.

There is a subtlety that makes prose-parsing genuinely dangerous: our own call
script contains the phrase *"your account ending 1001."* A naive matcher reads
the account number as a destination and refuses every call.

**Fix.** `destination_masked` or `destination_last4` as a field on the plan.
Three characters of JSON removes an entire class of integrator bug.

---

## 6. Consent is invisible to the platform

**What.** Nothing in the plan payload records *why* this number may legally be
called. `metadata` is free-form and not interpreted.

**Impact.** Consent is the precondition regulators ask about first, and it lives
only in the integrator's own database. When the operator is asked to prove a call
was lawful, the platform that placed it holds no part of the answer.

**Fix.** Optional `consent_basis` and `consent_timestamp` stored with the run and
returned by `get_call_run`. You do not need to validate them; storing them beside
the call is most of the value.

---

## 7. No recipient timezone field, while the docs forbid inferring one

**What.** `docs/design-principles.md` says an agent "must not infer timezone from
phone number, country code, locale." Correct, and there is no field in which to
put the timezone instead. `plan_call` takes `region`, which is a routing hint,
not a location.

**Impact.** The 08:00–21:00 local-time rule is the most commonly violated
requirement in US collections. Every integrator rebuilds timezone handling, and
most will get daylight saving wrong, because a stored UTC offset is correct for
half the year. The documented principle has no enforcement surface.

**Fix.** Optional `recipient_timezone` (IANA), and optionally a
platform-enforced local window that refuses the run. A platform-side refusal
would be worth real money to any regulated caller.

---

## 8. No account-level suppression list

**What.** There is no `suppress_number` tool and no platform DNC list.

**Impact.** Every integrator keeps their own, so two integrations on one CALL-E
account can dial someone who opted out of the other, and neither knows. The
opt-out is against the *operator*, not against one codebase.

**Fix.** Even an advisory per-account suppression list, checked at `plan_call`
and refusable at `run_call`, would close this.

---

## 9. No mid-call event channel

**What.** There is no webhook or stream that lets an integrator act during a
call. Opt-out handling is instructions in the goal prompt and nothing more.

**Impact.** "Honour the opt-out" is only ever as reliable as the model. A
compliance obligation is being met by persuasion. Our workaround — re-read the
transcript afterwards and suppress regardless — means the number is protected,
but the call the person asked to end still ran to completion.

**Fix.** A one-way event stream would be enough to let integrators log in real
time; a two-way channel with a `terminate` action would let compliance be
*enforced* rather than requested.

---

## 10. The most-copied sample teaches a disclosure-free blast

**What.** `apps/python/batch-runner`'s example goal instructs the agent to "say
the following message exactly once, then end the call. Do not ask questions. Do
not wait for a response," with no identity or purpose disclosure.

**Impact.** It is the most obvious starting point in the repository, and it is
the shape of an artificial-voice broadcast that needs an identity disclosure
under TCPA 227(d)(3)(A). Sample code propagates further than documentation.

**Fix.** One disclosure line in that sample goal — *"This is an automated call
from <entity>"* — and everyone who copies it starts compliant.

---

## 11. Unknown parameters fail the call rather than being ignored

**What.** Passing `result_schema` to `plan_call` fails the whole call with a
validation error.

**Impact.** Reasonable and strict, but it means a hopeful key taken from the
wrong transport's docs breaks every live run, and you find out with credits. Ours
did. Combined with finding 1, it turns a documentation mismatch into an outage.

**Fix.** Keep the strictness, and name the accepted parameter set in the error.

---

## Summary

If only one thing ships from this list, make it **finding 2**, a structured
`opt_out` on the run result. It is small, it is unambiguous, and it is the
difference between an integrator who honours revocations and one who finds out in
a class action.

Findings 1, 3 and 5 are each a few fields of JSON and each removes a category of
integrator bug that is currently invisible until it reaches a real person.

Happy to supply the captured payloads (numbers masked, credentials stripped) for
any of the above.
