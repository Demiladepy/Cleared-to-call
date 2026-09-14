**Feedback from building a compliance gate on CALL-E (MCP)**

**Cleared to Call** decides whether an outbound collections call is *allowed* before CALL-E dials. All from live runs. Full write-up: https://github.com/Demiladepy/Cleared-to-call/blob/main/FEEDBACK.md

The `plan_call` → inspect → `run_call` split is the best thing in the API: two-phase dispatch with a confirm token is exactly what a compliance layer needs — it creates a moment where software can refuse. The rest asks for the same rigour either side of it.

**1. A result schema exists over REST but not over MCP.** `leash` declares a flat `result_schema` on `POST /v1/calls`. MCP `plan_call` rejects it (`Unexpected keyword argument`) and has no equivalent field. So MCP integrators recover outcomes by regex over `post_summary` — pattern-matching whether a consumer promised to pay or disputed a debt, into a legal record.

**2. No structured opt-out in `get_call_run`.** "Never call me again" returns `COMPLETED`, identical to a successful call. The only way to catch a revocation is scanning the transcript yourself. Anyone trusting the status will re-dial someone who revoked consent. If one thing ships from this list, make it `opt_out: true` on the run result.

**3. `DECLINED` conflates carrier rejection with a person refusing.** Three NG calls: `DECLINED`, `duration_seconds: 0`, no transcript, `hangup_type: ByCallee`. The phone never rang — the carrier rejected international caller ID. We'd mapped `DECLINED` → "consumer refused to pay" and were writing it into an append-only audit log. Caught only because duration was zero.

**4.** Reachability is invisible until you spend a credit (`NG` → `ready_to_run: false`, `IN` → connects); a destination matrix with a "local line required" column would save days. And `plan_call` never echoes the destination structurally, only masked in `confirm_summary` prose — so verifying a plan targets the cleared number means parsing prose.

Happy to share the captured payloads.
