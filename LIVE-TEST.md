# Live CALL-E testing guide

Everything you can run locally to validate the integration before spending credits.

## What works without a phone call

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m cleared.cli preflight --accounts fixtures/demo-live.json --account-id A-9001 --region US
.venv\Scripts\python.exe -m cleared.cli parse-run
```

- **preflight** — gate + `plan_call`, no credit spent
- **parse-run** — runs extractors against `tests/data/call_run_sample.json`

## Nigeria (+234) numbers

CALL-E recognizes Nigerian destinations but **does not dial them** on the
`openagent_oauth` channel. Preflight returns `ready_to_run: false` with a block
reason naming supported regions (US, SG, AU, IN — English).

The gate still works with `Africa/Lagos` timezone. Only the provider dial is blocked.

## Placing one live call

You need:

1. `calle auth login` (already done if `calle auth status --json` shows `"usable": true`)
2. A phone number in a **supported** country on account `A-9001` in `fixtures/demo-live.json`
3. Matching `--region` (e.g. US number → `--region US`)

```powershell
# Free check
.venv\Scripts\python.exe -m cleared.cli preflight --accounts fixtures/demo-live.json --account-id A-9001 --region US

# Live call (1 credit) — only when preflight shows ready_to_run: true
.venv\Scripts\python.exe -m cleared.cli run --execute --accounts fixtures/demo-live.json --account-id A-9001 --region US --fresh

# Or via the demo UI
.venv\Scripts\python.exe -m demo.app --accounts fixtures/demo-live.json --region US --allow-live
```

On the call, say **"Stop calling me"** after the disclosure to exercise Rule 5.

## After a live call (B1)

Save the provider payload (phone masked, tokens stripped):

```powershell
.venv\Scripts\python.exe -m cleared.cli capture-run --run-id <run_id_from_audit>
.venv\Scripts\python.exe -m cleared.cli parse-run --file tests/data/call_run_sample.json
.venv\Scripts\python.exe -m pytest tests/test_call_run_sample.py
```

Replace `tests/data/call_run_sample.json` with the captured file if extractors need fixing.

## Structured results (B2)

The MCP `plan_call` tool **does not accept** `result_schema` (rejected as an
unexpected keyword). When CALL-E returns `structured_result` on `get_call_run`,
extractors prefer it and record `outcome_source: structured` in the call report.
Otherwise they fall back to prose matching (`outcome_source: inferred`).
