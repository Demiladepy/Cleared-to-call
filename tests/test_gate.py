"""The pre-dial gate. Each rule gets a pass case and a fail case."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from cleared import gate
from cleared.schema import Account
from cleared.script import render_script

from .conftest import NOW


def clear(account: Account, **changes) -> Account:
    return dataclasses.replace(account, **changes)


def decide(account: Account, policy, *, now=NOW, suppression=None, script_text=None):
    script = render_script(account, policy)
    return gate.evaluate(
        account,
        now=now,
        script_text=script_text if script_text is not None else script.text,
        suppression=suppression,
        policy=policy,
    )


def test_clean_account_is_allowed(account, policy):
    decision = decide(account, policy)
    assert decision.allowed is True
    assert decision.block_reason is None
    assert decision.rules_evaluated == {"R1": "pass", "R2": "pass", "R3": "pass", "R4": "pass"}
    assert decision.policy_id == policy.policy_id


# R1 - call window


@pytest.mark.parametrize(
    "local_hour, allowed",
    [(7, False), (8, True), (12, True), (20, True), (21, False), (23, False)],
)
def test_call_window_boundaries(account, policy, local_hour, allowed):
    # New York is UTC-4 in August, so 08:00 local is 12:00 UTC.
    now = datetime(2026, 8, 28, tzinfo=timezone.utc) + timedelta(hours=local_hour + 4)
    decision = decide(account, policy, now=now)
    assert decision.allowed is allowed
    if not allowed:
        assert decision.block_reason == "OUTSIDE_CALL_WINDOW"


def test_call_window_uses_the_recipient_timezone_not_the_servers(account, policy):
    """13:30 UTC is inside the window in New York and outside it in Los Angeles."""
    east = decide(account, policy, now=NOW)
    west = decide(clear(account, timezone="America/Los_Angeles"), policy, now=NOW)
    assert east.allowed is True
    assert west.allowed is False
    assert west.block_reason == "OUTSIDE_CALL_WINDOW"


def test_call_window_respects_daylight_saving(account, policy):
    """17:30 UTC is 12:30 in New York in January and 13:30 in August."""
    winter = decide(account, policy, now=datetime(2026, 1, 15, 17, 30, tzinfo=timezone.utc))
    summer = decide(account, policy, now=datetime(2026, 8, 15, 17, 30, tzinfo=timezone.utc))
    assert "12:30" in winter.rules[0].detail
    assert "13:30" in summer.rules[0].detail


def test_missing_timezone_blocks_rather_than_guessing(account, policy):
    decision = decide(clear(account, timezone=""), policy)
    assert decision.block_reason == "OUTSIDE_CALL_WINDOW"
    assert "no usable IANA timezone" in decision.rules[0].detail


def test_unknown_timezone_blocks_rather_than_guessing(account, policy):
    decision = decide(clear(account, timezone="Mars/Olympus"), policy)
    assert decision.allowed is False
    assert decision.block_reason == "OUTSIDE_CALL_WINDOW"


def test_naive_clock_is_read_as_utc(account, policy):
    naive = datetime(2026, 8, 28, 13, 30)
    assert decide(account, policy, now=naive).allowed is True


# R2 - consent


def test_no_consent_blocks(account, policy):
    decision = decide(clear(account, consent_on_file=False, consent_timestamp=None), policy)
    assert decision.block_reason == "NO_CONSENT"
    assert decision.rules_evaluated["R2"] == "fail"


def test_consent_flag_without_timestamp_blocks(account, policy):
    decision = decide(clear(account, consent_timestamp=None), policy)
    assert decision.block_reason == "NO_CONSENT"
    assert "no consent timestamp" in decision.rules[1].detail


def test_unparseable_consent_timestamp_blocks(account, policy):
    decision = decide(clear(account, consent_timestamp="last tuesday"), policy)
    assert decision.block_reason == "NO_CONSENT"
    assert "unparseable" in decision.rules[1].detail


# R3 - suppression


def test_suppressed_number_blocks(account, policy):
    decision = decide(account, policy, suppression={account.phone_e164})
    assert decision.block_reason == "ON_SUPPRESSION_LIST"


def test_suppression_accepts_a_callable(account, policy):
    decision = decide(account, policy, suppression=lambda phone: True)
    assert decision.block_reason == "ON_SUPPRESSION_LIST"


def test_suppression_detail_masks_the_number(account, policy):
    decision = decide(account, policy, suppression={account.phone_e164})
    detail = decision.rules[2].detail
    assert "+1******1234" in detail
    assert account.phone_e164 not in detail


def test_other_numbers_are_not_suppressed(account, policy):
    assert decide(account, policy, suppression={"+15550109999"}).allowed is True


# R4 - disclosure


def test_script_without_disclosure_blocks(account, policy):
    decision = decide(account, policy, script_text="Hi, you owe us money. Pay today.")
    assert decision.block_reason == "MISSING_DISCLOSURE"
    assert decision.rules_evaluated["R4"] == "fail"


def test_partial_disclosure_blocks_and_names_what_is_missing(account, policy):
    partial = (
        "This is an automated assistant calling on behalf of Northbridge Lending. "
        "If you would like us to stop calling, say stop calling."
    )
    decision = decide(account, policy, script_text=partial)
    assert decision.block_reason == "MISSING_DISCLOSURE"
    assert "debt_collection_purpose" in decision.rules[3].detail


def test_rendered_script_satisfies_every_disclosure_element(account, policy):
    decision = decide(account, policy)
    assert decision.rules[3].passed is True
    for element in policy.disclosure_elements:
        assert element.id in decision.rules[3].detail


# Ordering and reporting


def test_all_rules_are_evaluated_even_after_the_first_failure(account, policy):
    broken = clear(account, timezone="America/Los_Angeles", consent_on_file=False)
    decision = decide(broken, policy, suppression={broken.phone_e164})
    assert decision.block_reason == "OUTSIDE_CALL_WINDOW"
    assert decision.rules_evaluated == {"R1": "fail", "R2": "fail", "R3": "fail", "R4": "pass"}


def test_block_reason_is_the_first_failing_rule_in_policy_order(account, policy):
    no_consent_and_suppressed = clear(account, consent_on_file=False)
    decision = decide(
        no_consent_and_suppressed, policy, suppression={account.phone_e164}
    )
    assert decision.block_reason == "NO_CONSENT"


def test_decision_serializes_for_the_audit_log(account, policy):
    payload = decide(account, policy).to_dict()
    assert payload["decision"] == "allow"
    assert payload["evaluated_at"].endswith("+00:00")
    assert len(payload["rules"]) == 4


def test_gate_never_reads_the_wall_clock(account, policy):
    """The gate must be a pure function of the `now` it is handed."""
    far_future = NOW + timedelta(days=400)
    first = decide(account, policy, now=NOW)
    second = decide(account, policy, now=NOW)
    third = decide(account, policy, now=far_future)
    assert first.rules_evaluated == second.rules_evaluated
    assert first.rules[0].detail == second.rules[0].detail
    assert third.rules[0].detail != first.rules[0].detail
