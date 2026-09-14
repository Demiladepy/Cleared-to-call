**Feedback from building a compliance gate on CALL-E (MCP)**

**Cleared to Call** decides whether an outbound collections call is *allowed* before CALL-E dials. From live runs. Full write-up: https://github.com/Demiladepy/Cleared-to-call/blob/main/FEEDBACK.md

The `plan_call` → inspect → `run_call` split is the best thing in the API: a confirm token creates a moment where software can refuse.

**1. A result schema exists over REST but not over MCP.** `leash` declares a flat `result_schema` on `POST /v1/calls`. MCP `plan_call` rejects it (`Unexpected keyword argument`) with no equivalent field. So MCP integrators recover outcomes by regex over `post_summary` — pattern-matching whether a consumer promised to pay or disputed a debt, into a legal record.

**2. No structured opt-out in `get_call_run`.** "Never call me again" returns `COMPLETED`, identical to a successful call — the only way to catch a revocation is scanning the transcript yourself. Anyone trusting the status will re-dial someone who revoked consent. If one thing ships from this list, make it `opt_out: true` on the run result.

**3. Unconnected calls look like a person refusing.** NG: `DECLINED`. IN: `FAILED`. Both `duration_seconds: 0`, identical start/end timestamps, no transcript, `hangup_type: ByCallee` — the phone never rang; the carrier rejected international caller ID. We'd mapped `DECLINED` → "consumer refused to pay" and were writing that into an append-only audit log. Two countries, two statuses, one cause, and nothing distinguishes it from a human hanging up. The IN run's `post_summary` even said "the recipient may be busy or unavailable, retry in about 45 minutes" — a wrong diagnosis with a retry attached.

**4. `ready_to_run: true` is not a reachability guarantee.** IN planned cleanly, then failed at the carrier. `plan_call` validates everything except whether the call can be delivered. A destination matrix with a "local line required" column would save days.


Captured payloads available.
