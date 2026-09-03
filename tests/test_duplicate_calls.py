"""B3: an account with a call in flight must never be dialled a second time.

The audit entry used to be written only after a call completed. If the process
died between the dial and that write, the account looked uncalled and the next
run rang the same person again. Two calls where the law permitted one is the
exact failure this project exists to prevent.
"""

from __future__ import annotations

import pytest

from cleared.audit import AuditLog
from cleared.runner import pending_dispatch, process_account
from cleared.schema import CallReport, TranscriptTurn
from cleared.suppression import SuppressionList

from .conftest import NOW


class CrashesMidCall:
    """Dispatches to the provider, then dies before the outcome is recorded."""

    def __init__(self, run_id: str = "run_abc123") -> None:
        self.run_id = run_id
        self.dispatch_hook = None
        self.dials = 0

    def place_call(self, account, script):
        self.dials += 1
        if self.dispatch_hook is not None:
            self.dispatch_hook(self.run_id)
        raise KeyboardInterrupt("process killed mid-call")


class RecoveringCaller:
    """Knows the outcome of an earlier run, and refuses to place new calls."""

    def __init__(self, script_text: str) -> None:
        self.dials = 0
        self.recoveries: list[str] = []
        self.dispatch_hook = None
        self._script_text = script_text

    def place_call(self, account, script):
        self.dials += 1
        raise AssertionError("place_call must not be reached for an in-flight account")

    def recover(self, run_id: str) -> CallReport:
        self.recoveries.append(run_id)
        return CallReport(
            outcome="promise_to_pay",
            promise_date="2026-09-30",
            transcript=(
                TranscriptTurn("agent", self._script_text),
                TranscriptTurn("recipient", "I can pay on the thirtieth."),
            ),
            provider_status="COMPLETED",
            provider_run_id=run_id,
        )


@pytest.fixture
def live(tmp_path, policy, account):
    from cleared.script import render_script

    return {
        "audit": AuditLog(tmp_path / "audit.jsonl"),
        "suppression": SuppressionList(tmp_path / "suppression.jsonl"),
        "policy": policy,
        "account": account,
        "script_text": render_script(account, policy).text,
    }


def run(live, caller):
    return process_account(
        live["account"],
        caller=caller,
        audit=live["audit"],
        suppression=live["suppression"],
        policy=live["policy"],
        now=NOW,
        dry_run=False,
    )


def test_intent_is_on_disk_before_the_provider_is_touched(live):
    caller = CrashesMidCall()
    with pytest.raises(KeyboardInterrupt):
        run(live, caller)

    entries = live["audit"].entries()
    decisions = [entry["decision"] for entry in entries]
    assert decisions == ["intent", "dispatched"]
    assert entries[1]["provider_ref"] == "run_abc123"
    assert live["audit"].verify().ok is True


def test_a_crashed_call_is_still_pending_afterwards(live):
    caller = CrashesMidCall()
    with pytest.raises(KeyboardInterrupt):
        run(live, caller)

    pending = pending_dispatch(live["audit"].entries(), live["account"].account_id)
    assert pending is not None
    assert pending["decision"] == "dispatched"
    assert pending["provider_ref"] == "run_abc123"


def test_the_rerun_recovers_the_outcome_without_dialling_again(live):
    crashed = CrashesMidCall()
    with pytest.raises(KeyboardInterrupt):
        run(live, crashed)

    caller = RecoveringCaller(live["script_text"])
    record = run(live, caller)

    assert caller.dials == 0, "the account was dialled a second time"
    assert caller.recoveries == ["run_abc123"]
    assert record.result.outcome == "promise_to_pay"
    assert record.result.promise_date == "2026-09-30"
    assert "without re-dialling" in record.result.notes


def test_a_recovered_account_is_settled_and_does_not_recover_twice(live):
    with pytest.raises(KeyboardInterrupt):
        run(live, CrashesMidCall())
    run(live, RecoveringCaller(live["script_text"]))

    assert pending_dispatch(live["audit"].entries(), live["account"].account_id) is None


class CannotRecover:
    """A provider route with no recovery path at all."""

    dispatch_hook = None

    def __init__(self) -> None:
        self.dials = 0

    def place_call(self, account, script):
        self.dials += 1
        raise AssertionError("place_call must not be reached for an in-flight account")


def test_an_unrecoverable_call_is_refused_rather_than_redialled(live):
    with pytest.raises(KeyboardInterrupt):
        run(live, CrashesMidCall())

    caller = CannotRecover()
    record = run(live, caller)

    assert caller.dials == 0
    assert record.result.call_placed is False
    assert record.result.outcome == "not_called"
    assert "refusing to dial again" in record.result.notes
    # It stays unreconciled: a human decides, not a retry loop.
    assert pending_dispatch(live["audit"].entries(), live["account"].account_id) is not None


def test_recovery_failure_does_not_fall_through_to_a_dial(live):
    with pytest.raises(KeyboardInterrupt):
        run(live, CrashesMidCall())

    class RecoveryExplodes(CannotRecover):
        def recover(self, run_id):
            raise RuntimeError("provider unreachable")

    caller = RecoveryExplodes()
    record = run(live, caller)
    assert caller.dials == 0
    assert record.result.call_placed is False
    assert "provider unreachable" in record.result.notes


def test_a_live_opt_out_is_still_detected_on_a_recovered_call(live):
    """Rule 5 must not be bypassed by the recovery path."""
    with pytest.raises(KeyboardInterrupt):
        run(live, CrashesMidCall())

    class RecoversAnOptOut(CannotRecover):
        def recover(self, run_id):
            return CallReport(
                outcome="promise_to_pay",
                transcript=(TranscriptTurn("recipient", "Stop calling me."),),
                provider_run_id=run_id,
            )

    record = run(live, RecoversAnOptOut())
    assert record.result.outcome == "opt_out"
    assert record.result.opt_out is True
    assert live["suppression"].contains(live["account"].phone_e164)


def test_dry_runs_do_not_write_intent_entries(live):
    """A simulated call cannot be lost, so the demo log stays one line each."""
    from cleared.callers import FakeCaller

    record = process_account(
        live["account"],
        caller=FakeCaller(scenarios={}, policy=live["policy"]),
        audit=live["audit"],
        suppression=live["suppression"],
        policy=live["policy"],
        now=NOW,
        dry_run=True,
    )
    assert [entry["decision"] for entry in live["audit"].entries()] == ["allow"]
    assert record.result.call_placed is True


def test_a_normal_live_call_leaves_no_pending_entry(live):
    class Answers(CannotRecover):
        def place_call(self, account, script):
            self.dials += 1
            return CallReport(outcome="dispute", provider_status="COMPLETED")

    caller = Answers()
    record = run(live, caller)
    assert caller.dials == 1
    assert record.result.outcome == "dispute"
    assert pending_dispatch(live["audit"].entries(), live["account"].account_id) is None
    assert live["audit"].verify().ok is True
