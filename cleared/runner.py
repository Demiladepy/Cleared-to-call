"""The batch loop: gate every account, call only the cleared ones, log everything.

The order is the point. Nothing dials before `gate.evaluate` returns ALLOW, and
every account - blocked or called - leaves exactly one line in the audit log and
exactly one structured result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import gate
from .audit import AuditLog, VerificationResult
from .callers import Caller
from .policy import Policy, default_policy
from .revocation import RevocationHit, scan_transcript
from .schema import (
    Account,
    CallReport,
    CallResult,
    Decision,
    InvalidAccountError,
    OUTCOMES,
    TranscriptTurn,
)
from .script import CallScript, missing_disclosure_elements, render_script
from .suppression import SuppressionList

AGENT_SPEAKERS = {"agent", "bot", "assistant", "system"}


def load_accounts(path: str | Path) -> tuple[list[Account], list[str]]:
    """Load an account fixture. Returns usable accounts and rejection messages."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw["accounts"] if isinstance(raw, dict) else raw
    accounts: list[Account] = []
    rejected: list[str] = []
    for row in rows:
        try:
            accounts.append(Account.from_dict(row))
        except InvalidAccountError as error:
            rejected.append(str(error))
    return accounts, rejected


def disclosure_was_given(
    transcript: Sequence[TranscriptTurn], policy: Policy
) -> bool:
    """True when the agent's own words carried every required disclosure element."""
    spoken = " ".join(
        turn.text for turn in transcript if turn.speaker.strip().lower() in AGENT_SPEAKERS
    )
    if not spoken.strip():
        return False
    return not missing_disclosure_elements(spoken, policy)


@dataclass
class RunRecord:
    """Everything that happened for one account, in one place."""

    account: Account
    decision: Decision
    result: CallResult
    script: CallScript
    audit_entry: dict[str, Any]
    report: CallReport | None = None
    revocation: RevocationHit | None = None
    error: str | None = None

    @property
    def transcript(self) -> tuple[TranscriptTurn, ...]:
        return self.report.transcript if self.report else ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": {
                "account_id": self.account.account_id,
                "display_name": self.account.display_name,
                "phone_masked": self.account.masked_phone,
                "timezone": self.account.timezone,
                "amount_due": float(self.account.amount_due),
                "currency": self.account.currency,
            },
            "decision": self.decision.to_dict(),
            "result": self.result.to_dict(),
            "revocation": self.revocation.to_dict() if self.revocation else None,
            "transcript": [turn.to_dict() for turn in self.transcript],
            "error": self.error,
        }


@dataclass
class BatchRun:
    records: list[RunRecord] = field(default_factory=list)
    rejected_rows: list[str] = field(default_factory=list)
    dry_run: bool = True
    policy_id: str = ""
    policy_version: str = ""
    audit_verification: VerificationResult | None = None

    @property
    def results(self) -> list[CallResult]:
        return [record.result for record in self.records]

    @property
    def blocked(self) -> list[RunRecord]:
        return [record for record in self.records if not record.decision.allowed]

    @property
    def called(self) -> list[RunRecord]:
        return [record for record in self.records if record.result.call_placed]

    @property
    def opt_outs(self) -> list[RunRecord]:
        return [record for record in self.records if record.result.opt_out]

    def summary(self) -> dict[str, Any]:
        block_reasons: dict[str, int] = {}
        outcomes: dict[str, int] = {}
        for record in self.records:
            if record.result.block_reason:
                reason = record.result.block_reason
                block_reasons[reason] = block_reasons.get(reason, 0) + 1
            outcomes[record.result.outcome] = outcomes.get(record.result.outcome, 0) + 1
        return {
            "accounts": len(self.records),
            "cleared": len(self.called),
            "blocked": len(self.blocked),
            "opt_outs": len(self.opt_outs),
            "block_reasons": block_reasons,
            "outcomes": outcomes,
            "dry_run": self.dry_run,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "audit": self.audit_verification.to_dict() if self.audit_verification else None,
            "rejected_rows": self.rejected_rows,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "records": [record.to_dict() for record in self.records],
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


IN_FLIGHT_DECISIONS = {"intent", "dispatched"}
TERMINAL_DECISIONS = {"allow", "block"}


def pending_dispatch(
    entries: Sequence[dict[str, Any]], account_id: str
) -> dict[str, Any] | None:
    """The in-flight audit entry for this account, if the call never completed.

    A live call writes `intent` before it dials and `dispatched` once the
    provider returns a run id. The completion entry comes last. If the process
    dies in between, the intent is on disk with no terminal entry after it, and
    that is what this finds.
    """
    pending: dict[str, Any] | None = None
    for entry in entries:
        if entry.get("account_id") != account_id:
            continue
        decision = entry.get("decision")
        if decision in IN_FLIGHT_DECISIONS:
            pending = entry
        elif decision in TERMINAL_DECISIONS:
            pending = None
    return pending


def process_account(
    account: Account,
    *,
    caller: Caller,
    audit: AuditLog,
    suppression: SuppressionList,
    policy: Policy,
    now: datetime,
    dry_run: bool,
) -> RunRecord:
    """Gate one account, then call it only if the gate allowed it."""
    script = render_script(account, policy)
    decision = gate.evaluate(
        account,
        now=now,
        script_text=script.text,
        suppression=suppression,
        policy=policy,
    )

    if not decision.allowed:
        entry = audit.append(
            account_id=account.account_id,
            phone_masked=account.masked_phone,
            decision="block",
            block_reason=decision.block_reason,
            rules_evaluated=decision.rules_evaluated,
            outcome="not_called",
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            dry_run=dry_run,
            timestamp=now,
        )
        return RunRecord(
            account=account,
            decision=decision,
            script=script,
            audit_entry=entry,
            result=CallResult(
                account_id=account.account_id,
                call_placed=False,
                block_reason=decision.block_reason,
                disclosure_given=False,
                outcome="not_called",
                opt_out=False,
                audit_ref=entry["audit_ref"],
            ),
        )

    error: str | None = None
    report: CallReport | None = None
    recovered_from: str | None = None

    # B3. An account with a call already in flight must never be dialled again.
    # Dry runs cannot lose a real call, so the interlock only applies to live
    # ones and the demo's audit log stays one line per account.
    pending = None if dry_run else pending_dispatch(audit.entries(), account.account_id)
    if pending is not None:
        run_id = pending.get("provider_ref")
        recover = getattr(caller, "recover", None)
        if run_id and callable(recover):
            try:
                report = recover(run_id)
                recovered_from = pending["audit_ref"]
            except Exception as caught:  # noqa: BLE001
                error = f"recovery failed for run {run_id}: {type(caught).__name__}: {caught}"
        if report is None:
            # The outcome cannot be recovered, so the only safe answer is to
            # leave it for a human. Dialling again is the one thing we cannot do.
            note = (
                f"a previous call is unreconciled ({pending['audit_ref']}); "
                "refusing to dial again"
            )
            entry = audit.append(
                account_id=account.account_id,
                phone_masked=account.masked_phone,
                decision="unreconciled",
                block_reason=None,
                rules_evaluated=decision.rules_evaluated,
                outcome="not_called",
                policy_id=policy.policy_id,
                policy_version=policy.policy_version,
                dry_run=dry_run,
                provider_ref=run_id,
                timestamp=now,
            )
            return RunRecord(
                account=account,
                decision=decision,
                script=script,
                audit_entry=entry,
                error=error or note,
                result=CallResult(
                    account_id=account.account_id,
                    call_placed=False,
                    block_reason=None,
                    disclosure_given=False,
                    outcome="not_called",
                    opt_out=False,
                    audit_ref=entry["audit_ref"],
                    notes=error or note,
                ),
            )

    if report is None:
        if not dry_run:
            # On disk before the provider is touched, so a crash mid-call is
            # recoverable rather than invisible.
            audit.append(
                account_id=account.account_id,
                phone_masked=account.masked_phone,
                decision="intent",
                rules_evaluated=decision.rules_evaluated,
                outcome=None,
                policy_id=policy.policy_id,
                policy_version=policy.policy_version,
                dry_run=dry_run,
                timestamp=now,
            )

            def _on_dispatched(run_id: str) -> None:
                audit.append(
                    account_id=account.account_id,
                    phone_masked=account.masked_phone,
                    decision="dispatched",
                    rules_evaluated=decision.rules_evaluated,
                    outcome=None,
                    policy_id=policy.policy_id,
                    policy_version=policy.policy_version,
                    dry_run=dry_run,
                    provider_ref=run_id,
                    timestamp=now,
                )

            if hasattr(caller, "dispatch_hook"):
                caller.dispatch_hook = _on_dispatched

        try:
            report = caller.place_call(account, script)
        except Exception as caught:  # a provider failure is an outcome, not a crash
            error = f"{type(caught).__name__}: {caught}"
            report = CallReport(outcome="no_answer", provider_status="ERROR")

    revocation = scan_transcript(report.transcript, policy)
    opt_out = revocation is not None
    outcome = "opt_out" if opt_out else report.outcome
    if outcome not in OUTCOMES:
        error = error or f"caller returned an unknown outcome: {report.outcome!r}"
        outcome = "no_answer"

    if opt_out:
        # Rule 5's enforcement point. This is ours, not the agent's: even if the
        # agent talked past the opt-out, the number is suppressed from here on.
        suppression.add(
            account.phone_e164,
            reason="OPT_OUT",
            source=f"live_revocation:{account.account_id}",
            timestamp=now,
        )

    entry = audit.append(
        account_id=account.account_id,
        phone_masked=account.masked_phone,
        decision="allow",
        block_reason=None,
        rules_evaluated=decision.rules_evaluated,
        outcome=outcome,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        dry_run=dry_run,
        timestamp=now,
    )

    return RunRecord(
        account=account,
        decision=decision,
        script=script,
        audit_entry=entry,
        report=report,
        revocation=revocation,
        error=error,
        result=CallResult(
            account_id=account.account_id,
            call_placed=True,
            block_reason=None,
            disclosure_given=disclosure_was_given(report.transcript, policy),
            outcome=outcome,
            promise_date=None if opt_out else report.promise_date,
            opt_out=opt_out,
            audit_ref=entry["audit_ref"],
            notes=error
            or (f"recovered from {recovered_from} without re-dialling" if recovered_from else None),
        ),
    )


def run_batch(
    accounts: Iterable[Account],
    *,
    caller: Caller,
    audit: AuditLog,
    suppression: SuppressionList,
    policy: Policy | None = None,
    now_provider: Callable[[], datetime] = _utc_now,
    dry_run: bool = True,
    rejected_rows: Sequence[str] = (),
) -> BatchRun:
    """Run the gate over a batch. Blocked accounts are never handed to the caller."""
    active = policy or default_policy()
    run = BatchRun(
        dry_run=dry_run,
        policy_id=active.policy_id,
        policy_version=active.policy_version,
        rejected_rows=list(rejected_rows),
    )
    for account in accounts:
        run.records.append(
            process_account(
                account,
                caller=caller,
                audit=audit,
                suppression=suppression,
                policy=active,
                now=now_provider(),
                dry_run=dry_run,
            )
        )
    run.audit_verification = audit.verify()
    return run
