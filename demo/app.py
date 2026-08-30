"""A thin demo view over the gate. Not a product.

    uvicorn demo.app:app --reload
    python -m demo.app --now 2026-08-28T13:30:00Z

Dry run by default: no credentials, no credits, nothing dials. Live calling is
off unless the process is started with --allow-live, and even then it is one
explicit click per already-cleared account.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from cleared.audit import AuditLog
from cleared.callers import FakeCaller
from cleared.policy import load_policy
from cleared.runner import BatchRun, RunRecord, load_accounts, process_account, run_batch
from cleared.schema import parse_iso8601
from cleared.suppression import SuppressionList

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
RUNTIME = ROOT / "runtime"
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

DEFAULT_NOW = "2026-08-28T13:30:00Z"


@dataclass
class DemoState:
    """Whatever the last run produced, plus the switches the operator set."""

    now_input: str = DEFAULT_NOW
    allow_live: bool = False
    accounts_path: Path = FIXTURES / "accounts.json"
    live_region: str = "US"
    run: BatchRun | None = None
    live_records: dict[str, RunRecord] = field(default_factory=dict)
    message: str | None = None
    error: str | None = None

    @property
    def audit_path(self) -> Path:
        return RUNTIME / "audit.jsonl"

    @property
    def suppression_path(self) -> Path:
        return RUNTIME / "suppression.jsonl"

    def resolved_now(self) -> datetime:
        if not self.now_input.strip():
            return datetime.now(timezone.utc)
        return parse_iso8601(self.now_input)


state = DemoState(allow_live=os.environ.get("ALLOW_LIVE", "").lower() in {"1", "true", "yes"})
app = FastAPI(title="Cleared to Call", docs_url=None, redoc_url=None)


def suppression() -> SuppressionList:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if not state.suppression_path.is_file():
        state.suppression_path.write_text(
            (FIXTURES / "suppression.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
        )
    return SuppressionList(state.suppression_path)


def execute_batch(fresh: bool = False) -> None:
    policy = load_policy()
    if fresh:
        state.audit_path.unlink(missing_ok=True)
        state.suppression_path.unlink(missing_ok=True)
        state.live_records.clear()

    accounts, rejected = load_accounts(state.accounts_path)
    now = state.resolved_now()
    state.run = run_batch(
        accounts,
        caller=FakeCaller.from_file(FIXTURES / "scenarios.json", policy=policy),
        audit=AuditLog(state.audit_path),
        suppression=suppression(),
        policy=policy,
        now_provider=lambda: now,
        dry_run=True,
        rejected_rows=rejected,
    )


def view_model() -> dict[str, Any]:
    policy = load_policy()
    run = state.run
    audit = AuditLog(state.audit_path)
    entries = audit.entries()
    return {
        "policy": policy,
        "run": run,
        "summary": run.summary() if run else None,
        "records": [state.live_records.get(record.account.account_id, record) for record in run.records]
        if run
        else [],
        "audit_entries": entries[-14:],
        "audit_total": len(entries),
        "verification": audit.verify(),
        "now_input": state.now_input,
        "allow_live": state.allow_live,
        "message": state.message,
        "error": state.error,
        "suppressed": suppression().entries(),
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    if state.run is None:
        # First paint always starts from the seeded state, so the demo is the
        # same batch every time. Re-running from the page keeps state, which is
        # how an opt-out becomes a permanent block on the next run.
        execute_batch(fresh=True)
    model = view_model()
    state.message = None
    state.error = None
    return TEMPLATES.TemplateResponse(request, "index.html", model)


@app.post("/run")
def run_endpoint(now: str = Form(default=DEFAULT_NOW), fresh: str = Form(default="")) -> RedirectResponse:
    previous = state.now_input
    state.now_input = now
    try:
        execute_batch(fresh=bool(fresh))
        mode = "reset and re-run" if fresh else "re-run"
        state.message = f"Batch {mode} in dry run. Nothing was dialled."
    except ValueError as error:
        # Keep the last clock that worked, so the page still renders.
        state.now_input = previous
        state.error = f"Could not read that clock: {error}"
    return RedirectResponse("/", status_code=303)


@app.post("/call/{account_id}")
def call_endpoint(account_id: str) -> RedirectResponse:
    """Place one real CALL-E call for an account the gate already cleared."""
    if not state.allow_live:
        raise HTTPException(
            status_code=403,
            detail="live calling is off. Restart with --allow-live to enable it.",
        )
    if state.run is None:
        raise HTTPException(status_code=409, detail="run the batch first")

    matches = [record for record in state.run.records if record.account.account_id == account_id]
    if not matches:
        raise HTTPException(status_code=404, detail=f"no such account in this batch: {account_id}")

    record = matches[0]
    if not record.decision.allowed:
        # The gate said no. There is no path from here to a dial.
        raise HTTPException(
            status_code=403,
            detail=f"{account_id} was blocked: {record.result.block_reason}",
        )

    from cleared.calle_caller import CalleCaller

    policy = load_policy()
    live = process_account(
        record.account,
        caller=CalleCaller(
            policy=policy,
            region=state.live_region,
            audit_ref=record.result.audit_ref,
        ),
        audit=AuditLog(state.audit_path),
        suppression=suppression(),
        policy=policy,
        now=datetime.now(timezone.utc),
        dry_run=False,
    )
    state.live_records[account_id] = live
    state.message = (
        f"Live call to {live.account.masked_phone} finished: {live.result.outcome}."
        + (" Number suppressed." if live.result.opt_out else "")
    )
    return RedirectResponse("/", status_code=303)


@app.get("/api/run")
def api_run() -> JSONResponse:
    if state.run is None:
        execute_batch()
    return JSONResponse(state.run.to_dict())


@app.get("/api/audit")
def api_audit() -> JSONResponse:
    audit = AuditLog(state.audit_path)
    return JSONResponse({"verification": audit.verify().to_dict(), "entries": audit.entries()})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Cleared to Call demo view.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--now", default=DEFAULT_NOW, help="Pin the clock for the demo.")
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Enable the per-account live call button. Spends CALL-E credits.",
    )
    parser.add_argument(
        "--accounts",
        default=str(FIXTURES / "accounts.json"),
        help="Account fixture to show. Use a gitignored file for a recording batch.",
    )
    parser.add_argument(
        "--region",
        default="US",
        help="Two-letter ISO country code used for live calls, e.g. NG.",
    )
    args = parser.parse_args()

    state.now_input = args.now
    state.allow_live = args.allow_live
    state.accounts_path = Path(args.accounts)
    state.live_region = args.region
    if args.allow_live:
        print("LIVE CALLING IS ENABLED: the call button will dial a real number.")

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
