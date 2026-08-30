"""The batch loop end to end, in dry run: gate, call, opt-out, audit."""

from __future__ import annotations

import shutil

import pytest

from cleared.audit import AuditLog
from cleared.callers import FakeCaller, ScriptedCall
from cleared.runner import disclosure_was_given, load_accounts, process_account, run_batch
from cleared.schema import CallReport, TranscriptTurn
from cleared.suppression import SuppressionList

from .conftest import NOW


@pytest.fixture
def batch(tmp_path, fixtures_dir, policy):
    accounts, rejected = load_accounts(fixtures_dir / "accounts.json")
    working_suppression = tmp_path / "suppression.jsonl"
    shutil.copyfile(fixtures_dir / "suppression.jsonl", working_suppression)
    suppression = SuppressionList(working_suppression)
    caller = FakeCaller.from_file(fixtures_dir / "scenarios.json", policy=policy)
    audit = AuditLog(tmp_path / "audit.jsonl")
    run = run_batch(
        accounts,
        caller=caller,
        audit=audit,
        suppression=suppression,
        policy=policy,
        now_provider=lambda: NOW,
        dry_run=True,
        rejected_rows=rejected,
    )
    return run, caller, suppression, audit


def record_for(run, account_id):
    return next(record for record in run.records if record.account.account_id == account_id)


def test_the_fixture_batch_loads_cleanly(batch):
    run, _, _, _ = batch
    assert len(run.records) == 7
    assert run.rejected_rows == []


def test_every_account_gets_exactly_one_result_and_one_audit_line(batch):
    run, _, _, audit = batch
    assert len(run.results) == len(run.records)
    entries = audit.entries()
    assert len(entries) == len(run.records)
    assert [entry["account_id"] for entry in entries] == [
        record.account.account_id for record in run.records
    ]


def test_blocked_accounts_are_never_handed_to_the_caller(batch):
    run, caller, _, _ = batch
    blocked_ids = {record.account.account_id for record in run.blocked}
    assert blocked_ids == {"A-1002", "A-1003", "A-1004"}
    assert blocked_ids.isdisjoint(set(caller.calls_placed))


def test_each_block_reason_is_the_expected_one(batch):
    run, _, _, _ = batch
    assert record_for(run, "A-1002").result.block_reason == "OUTSIDE_CALL_WINDOW"
    assert record_for(run, "A-1003").result.block_reason == "NO_CONSENT"
    assert record_for(run, "A-1004").result.block_reason == "ON_SUPPRESSION_LIST"


def test_blocked_results_are_marked_not_called(batch):
    run, _, _, _ = batch
    for record in run.blocked:
        assert record.result.call_placed is False
        assert record.result.outcome == "not_called"
        assert record.result.disclosure_given is False
        assert record.result.opt_out is False
        assert record.result.audit_ref.startswith("aud_")


def test_a_cleared_account_reaches_a_promise_to_pay(batch):
    run, _, _, _ = batch
    result = record_for(run, "A-1001").result
    assert result.call_placed is True
    assert result.block_reason is None
    assert result.outcome == "promise_to_pay"
    assert result.promise_date == "2026-09-05"
    assert result.disclosure_given is True
    assert result.opt_out is False


def test_every_placed_call_opened_with_the_full_disclosure(batch):
    run, _, _, _ = batch
    for record in run.called:
        if record.result.outcome == "no_answer":
            continue
        assert record.result.disclosure_given is True


def test_a_live_opt_out_ends_the_call_and_suppresses_the_number(batch):
    run, _, suppression, _ = batch
    record = record_for(run, "A-1005")
    assert record.result.opt_out is True
    assert record.result.outcome == "opt_out"
    assert record.result.promise_date is None
    assert record.revocation is not None
    assert record.revocation.matched_phrase == "stop calling"
    assert suppression.contains(record.account.phone_e164)
    assert record.transcript[-1].speaker == "agent"
    assert "remove your number" in record.transcript[-1].text


def test_a_suppressed_number_is_blocked_on_the_next_run(batch, tmp_path, policy):
    run, caller, suppression, _ = batch
    opted_out = record_for(run, "A-1005").account
    audit = AuditLog(tmp_path / "audit2.jsonl")
    second = run_batch(
        [opted_out],
        caller=caller,
        audit=audit,
        suppression=suppression,
        policy=policy,
        now_provider=lambda: NOW,
    )
    assert second.records[0].result.block_reason == "ON_SUPPRESSION_LIST"
    assert second.records[0].result.call_placed is False


def test_an_unanswered_call_is_recorded_without_a_disclosure(batch):
    run, _, _, _ = batch
    result = record_for(run, "A-1006").result
    assert result.call_placed is True
    assert result.outcome == "no_answer"
    assert result.disclosure_given is False


def test_a_dispute_is_captured(batch):
    run, _, _, _ = batch
    assert record_for(run, "A-1007").result.outcome == "dispute"


def test_the_audit_chain_verifies_after_the_batch(batch):
    run, _, _, audit = batch
    assert run.audit_verification.ok is True
    assert audit.verify().entries_checked == 7


def test_the_summary_counts_every_branch(batch):
    run, _, _, _ = batch
    summary = run.summary()
    assert summary["accounts"] == 7
    assert summary["blocked"] == 3
    assert summary["cleared"] == 4
    assert summary["opt_outs"] == 1
    assert summary["block_reasons"] == {
        "OUTSIDE_CALL_WINDOW": 1,
        "NO_CONSENT": 1,
        "ON_SUPPRESSION_LIST": 1,
    }
    assert summary["audit"]["ok"] is True


def test_results_match_the_documented_shape(batch):
    run, _, _, _ = batch
    payload = record_for(run, "A-1001").result.to_dict()
    assert set(payload) == {
        "account_id",
        "call_placed",
        "block_reason",
        "disclosure_given",
        "outcome",
        "promise_date",
        "opt_out",
        "audit_ref",
    }


def test_no_unmasked_number_reaches_the_audit_log(batch, tmp_path):
    _, _, _, audit = batch
    text = audit.path.read_text(encoding="utf-8")
    for suffix in range(1234, 1241):
        assert f"+1555010{suffix}" not in text


# Enforcement independent of the agent's own behaviour


class TalksPastTheOptOut:
    """A caller that ignores the opt-out and keeps selling. It must not matter."""

    def place_call(self, account, script):
        return CallReport(
            outcome="promise_to_pay",
            promise_date="2026-09-30",
            transcript=(
                TranscriptTurn("agent", script.disclosure),
                TranscriptTurn("recipient", "Take me off your list."),
                TranscriptTurn("agent", "Could you pay half today instead?"),
                TranscriptTurn("recipient", "Fine, the thirtieth."),
            ),
        )


def test_an_agent_that_ignores_the_opt_out_still_ends_in_suppression(
    tmp_path, account, policy
):
    suppression = SuppressionList(tmp_path / "suppression.jsonl")
    audit = AuditLog(tmp_path / "audit.jsonl")
    record = process_account(
        account,
        caller=TalksPastTheOptOut(),
        audit=audit,
        suppression=suppression,
        policy=policy,
        now=NOW,
        dry_run=True,
    )
    assert record.result.outcome == "opt_out"
    assert record.result.opt_out is True
    assert record.result.promise_date is None
    assert suppression.contains(account.phone_e164)


class Explodes:
    def place_call(self, account, script):
        raise RuntimeError("provider unreachable")


def test_a_provider_failure_is_recorded_not_raised(tmp_path, account, policy):
    audit = AuditLog(tmp_path / "audit.jsonl")
    record = process_account(
        account,
        caller=Explodes(),
        audit=audit,
        suppression=SuppressionList(tmp_path / "s.jsonl"),
        policy=policy,
        now=NOW,
        dry_run=True,
    )
    assert record.result.outcome == "no_answer"
    assert "provider unreachable" in record.error
    assert audit.verify().ok is True


class ReturnsNonsense:
    def place_call(self, account, script):
        return CallReport(outcome="settled_in_full")


def test_an_unknown_outcome_is_rejected(tmp_path, account, policy):
    record = process_account(
        account,
        caller=ReturnsNonsense(),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        suppression=SuppressionList(tmp_path / "s.jsonl"),
        policy=policy,
        now=NOW,
        dry_run=True,
    )
    assert record.result.outcome == "no_answer"
    assert "unknown outcome" in record.error


def test_disclosure_check_reads_agent_turns_only(policy, account):
    from cleared.script import render_script

    script = render_script(account, policy)
    assert disclosure_was_given([TranscriptTurn("agent", script.text)], policy) is True
    assert disclosure_was_given([TranscriptTurn("recipient", script.text)], policy) is False
    assert disclosure_was_given([], policy) is False


def test_the_fake_caller_defaults_to_no_answer_for_unscripted_accounts(account, policy):
    from cleared.script import render_script

    caller = FakeCaller(scenarios={}, policy=policy)
    report = caller.place_call(account, render_script(account, policy))
    assert report.outcome == "no_answer"
    assert report.transcript == ()


def test_the_fake_caller_speaks_the_real_disclosure(account, policy):
    from cleared.script import render_script

    script = render_script(account, policy)
    caller = FakeCaller(
        scenarios={account.account_id: ScriptedCall(outcome="refusal", turns=(("recipient", "No."),))},
        policy=policy,
    )
    report = caller.place_call(account, script)
    assert report.transcript[0].text == script.disclosure
    assert disclosure_was_given(report.transcript, policy) is True
