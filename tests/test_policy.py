"""The policy is data. These tests hold the data itself to its contract."""

from __future__ import annotations

import json

import pytest

from cleared.policy import PolicyError, load_policy, parse_policy
from cleared.script import check_disclosure, render_script


def test_the_packaged_policy_declares_exactly_the_five_rules(policy):
    assert [rule.id for rule in policy.rules] == ["R1", "R2", "R3", "R4", "R5"]
    assert [rule.name for rule in policy.rules] == [
        "call_window",
        "consent_on_file",
        "not_suppressed",
        "disclosure_ready",
        "live_revocation",
    ]


def test_four_rules_run_before_dialling_and_one_runs_during_the_call(policy):
    assert [rule.id for rule in policy.pre_dial_rules] == ["R1", "R2", "R3", "R4"]
    assert [rule.id for rule in policy.in_call_rules] == ["R5"]


def test_every_rule_carries_an_authority_and_a_temporal_form(policy):
    for rule in policy.rules:
        assert rule.authority, f"{rule.id} has no cited authority"
        assert rule.temporal_form.startswith("G("), f"{rule.id} has no temporal form"
        assert rule.requirement


def test_the_call_window_is_the_federal_one(policy):
    assert policy.window_label == "08:00-21:00"
    assert policy.jurisdiction == "US-FEDERAL"
    assert policy.timezone_source == "account.timezone"


def test_block_reasons_are_unique_and_match_the_documented_set(policy):
    reasons = [rule.block_reason for rule in policy.pre_dial_rules]
    assert reasons == [
        "OUTSIDE_CALL_WINDOW",
        "NO_CONSENT",
        "ON_SUPPRESSION_LIST",
        "MISSING_DISCLOSURE",
    ]
    assert policy.rule("R5").block_reason == "OPT_OUT"


def test_the_rendered_script_satisfies_every_disclosure_element(account, policy):
    checks = check_disclosure(render_script(account, policy).text, policy)
    assert [check.element_id for check in checks] == [
        "caller_identity",
        "automated_voice_identity",
        "debt_collection_purpose",
        "opt_out_instruction",
    ]
    assert all(check.present for check in checks)


def test_the_disclosure_alone_carries_every_element(account, policy):
    """The mandatory content has to be in the opening, not scattered later."""
    checks = check_disclosure(render_script(account, policy).disclosure, policy)
    assert all(check.present for check in checks)


def test_the_script_never_leaks_the_full_account_number(account, policy):
    script = render_script(account, policy)
    assert account.phone_e164 not in script.text


def test_an_unknown_rule_id_is_an_error(policy):
    with pytest.raises(PolicyError):
        policy.rule("R99")


def test_a_policy_without_rules_is_rejected():
    with pytest.raises(PolicyError):
        parse_policy(
            {
                "policy_id": "x",
                "policy_version": "1",
                "call_window": {"start_local": "08:00", "end_local": "21:00"},
                "rules": [],
                "disclosure_elements": [{"id": "a", "description": "b", "patterns": ["c"]}],
            }
        )


def test_a_policy_with_a_broken_window_is_rejected():
    with pytest.raises(PolicyError):
        parse_policy(
            {
                "policy_id": "x",
                "policy_version": "1",
                "call_window": {"start_local": "eight", "end_local": "21:00"},
                "rules": [
                    {
                        "id": "R1",
                        "name": "n",
                        "stage": "pre_dial",
                        "block_reason": "B",
                        "requirement": "r",
                    }
                ],
                "disclosure_elements": [{"id": "a", "description": "b", "patterns": ["c"]}],
            }
        )


def test_a_missing_policy_file_is_an_error(tmp_path):
    with pytest.raises(PolicyError):
        load_policy(tmp_path / "nope.json")


def test_the_skill_ships_the_same_policy_as_the_package():
    """The Agent Skill must not drift from the tested gate."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    package_policy = json.loads((root / "cleared" / "policy.json").read_text(encoding="utf-8"))
    skill_policy = json.loads(
        (root / "skills" / "cleared-to-call" / "assets" / "policy.json").read_text(encoding="utf-8")
    )
    assert package_policy == skill_policy
