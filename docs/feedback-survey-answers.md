# CALL-E feedback survey — paste-ready answers

Full write-up with evidence: [`FEEDBACK.md`](../FEEDBACK.md)

---

## Which CALL-E interfaces did you use?

**Tick: MCP, CLI, SKILL. Untick SDK and API.**

- **MCP** — everything went through the MCP server (`plan_call`, `run_call`,
  `get_call_run`) over streamable HTTP with fastmcp. This is where every finding
  below comes from.
- **CLI** — `calle auth login`, `calle auth status --json`. The live caller reads
  the CLI's token cache.
- **SKILL** — authored an Agent Skill, submitted as a PR to
  `awesome-phone-call-agents`.
- **Not SDK** — fastmcp is a third-party MCP client, not a CALL-E SDK.
- **Not API** — never called the REST surface directly. Worth being precise here,
  because finding 1 is *about* the gap between REST and MCP.

---

## What is a calling-related problem you face on a regular basis?

Deciding whether an outbound call is **allowed to happen** before it is placed.

Every batch means re-deriving the recipient's local time from their stored
timezone, checking consent is on file with a timestamp, and checking our own
suppression list — none of which the platform can hold or enforce. Then after the
call, working out whether the person opted out means reading prose, because no
status distinguishes "they asked to never be called again" from "the call
completed normally".

In US collections that is not a convenience problem. A non-compliant call carries
$500–$1,500 in statutory damages and the operator is liable, not the platform.

---

## On a scale of 1 to 10, how painful would you rank this problem?

**9**

It is the reason lenders who would happily let AI dial don't. The calling is
solved; proving the call was allowed is not.

---

## What bugs or issues did you run into while using CALL-E, if any?

**1. A result schema exists over REST but not over MCP.** `apps/python/leash`
declares a flat `result_schema` on `POST /v1/calls`. MCP `plan_call` rejects the
same key: `1 validation error for call[plan_call] / result_schema / Unexpected
keyword argument`. Introspecting the live tool shows the full accepted set
(`plan_id`, `to_phones`, `region`, `language`, `goal`, `scheduled_at`,
`retry_confirmation_action`, `user_input`, `ttl_seconds`) with no field for a
declared result. So every MCP integrator recovers outcomes by regex over
`post_summary`. We are pattern-matching whether a consumer promised to pay or
disputed a debt, into a legal record.

**2. No structured opt-out signal in `get_call_run`.** A call where the recipient
says "never call me again" returns `COMPLETED` — identical to a successful one.
The only way to detect a revocation is to scan the transcript yourself. An
integrator who trusts the status will re-dial someone who revoked consent.

**3. `DECLINED` conflates carrier rejection with a person refusing.** Three live
calls to Nigerian numbers returned `DECLINED`, `duration_seconds: 0`, identical
start/end timestamps, no transcript, `hangup_type: ByCallee`. The phone never
rang — the carrier rejected international caller ID. We had mapped `DECLINED` to
"consumer refused to pay" and were writing that into an append-only audit log.
Caught it only because duration was zero. `ByCallee` is actively wrong here: no
callee was involved.

**4. Destination reachability is invisible until you spend a credit.** `NG`
plans with `ready_to_run: false`; `IN` plans cleanly and connects. Nigeria is
reachable only over international lines that local carriers reject — which we
learned from support material, not the API. No endpoint or doc says which
destinations work.

**5. `plan_call` never echoes the destination in a structured field.** It appears
only inside `confirm_summary` prose, masked: "Ready to place the call immediately
to …4692". To verify a plan targets the number we cleared, we parse a sentence.
Our first version of that check passed vacuously — found no numbers, approved
everything — and we only caught it by capturing a real payload. A wrong-number
check that silently always passes is worse than no check.

**6. Unknown parameters fail the whole call** rather than being ignored. Correct
and strict, but combined with (1) it turns a docs mismatch into an outage you
discover with credits.

---

## What would have given you a better experience with our CALL-E documentation?

1. **A capability parity table for MCP vs REST vs SDK.** We assumed `leash`'s
   `result_schema` generalised because nothing said it was REST-only. That
   assumption cost live runs.
2. **A destination support matrix** with a "local line required" column. One
   table would have saved us a day and a re-sourced test number.
3. **Document the transcript speaker labels.** Opt-out detection depends on
   knowing which label is the recipient. Ours are still guesses, and a wrong
   guess means a silently missed opt-out.
4. **`docs/design-principles.md` says never infer timezone from a phone number** —
   which is right, and there is no `recipient_timezone` field to put it in
   instead. The principle has no enforcement surface.
5. **Say that unknown `plan_call` keys hard-fail**, and name the accepted set in
   the error.

---

## How likely are you to use CALL-E in the future?

**9**

The `plan_call` → inspect → `run_call` split is the best thing in the API. A
two-phase dispatch with a confirm token is exactly the shape a compliance layer
needs, because it creates a moment where software can refuse. Most of my feedback
is asking for that same rigour in the payloads either side of it.

---

## Is there any other feedback you'd like to provide?

If one thing ships from this list, make it the structured `opt_out` on the run
result. It is small, unambiguous, and the difference between an integrator who
honours revocations and one who finds out in a class action.

Findings 1, 3 and 5 are each a few fields of JSON, and each removes a category of
bug that stays invisible until it reaches a real person.

One more, because sample code propagates further than docs:
`apps/python/batch-runner`'s example goal says "say the following message exactly
once, then end the call. Do not ask questions" with no identity or purpose
disclosure. It is the most obvious starting point in the repo and it is the shape
of an artificial-voice broadcast that needs an identity disclosure under TCPA
227(d)(3)(A). One disclosure line in that sample and everyone who copies it
starts compliant.

Happy to share the captured payloads (numbers masked, credentials stripped).

---

## Are you open to being contacted?

**Yes** — and the email you used with `calle auth login`.
