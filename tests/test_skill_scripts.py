"""The skill ships its own Node gate. It must agree with the tested Python one.

The Agent Skill is installed on its own, without this package, so `gate-core.mjs`
reimplements rules 1 to 5 against the same `policy.json`. These tests run both
implementations over the same fixtures and compare the verdicts.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cleared.gate import evaluate
from cleared.revocation import detect_revocation
from cleared.runner import load_accounts
from cleared.script import render_script
from cleared.suppression import SuppressionList

from .conftest import NOW

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "cleared-to-call"
NOW_ARG = "2026-08-28T13:30:00Z"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_script(name: str, *args: str) -> tuple[int, dict]:
    process = subprocess.run(
        ["node", str(SKILL / "scripts" / name), *args],
        capture_output=True,
        text=True,
        cwd=SKILL,
    )
    payload = json.loads(process.stdout) if process.stdout.strip() else {}
    return process.returncode, payload


@pytest.fixture(scope="module")
def fixture_accounts():
    accounts, _ = load_accounts(ROOT / "fixtures" / "accounts.json")
    return accounts


def test_node_and_python_gates_agree_on_every_fixture_account(fixture_accounts, policy):
    suppression = SuppressionList(ROOT / "fixtures" / "suppression.jsonl")
    for account in fixture_accounts:
        expected = evaluate(
            account,
            now=NOW,
            script_text=render_script(account, policy).text,
            suppression=suppression,
            policy=policy,
        )
        code, actual = run_script(
            "evaluate-account.mjs",
            "--file",
            str(ROOT / "fixtures" / "accounts.json"),
            "--account-id",
            account.account_id,
            "--now",
            NOW_ARG,
            "--suppression",
            str(ROOT / "fixtures" / "suppression.jsonl"),
        )
        assert actual["decision"] == ("allow" if expected.allowed else "block"), account.account_id
        assert actual["block_reason"] == expected.block_reason, account.account_id
        assert actual["rules_evaluated"] == expected.rules_evaluated, account.account_id
        assert code == (0 if expected.allowed else 2)


def test_the_node_gate_reports_the_same_rule_details(fixture_accounts, policy):
    account = fixture_accounts[0]
    expected = evaluate(
        account,
        now=NOW,
        script_text=render_script(account, policy).text,
        suppression=None,
        policy=policy,
    )
    _, actual = run_script(
        "evaluate-account.mjs",
        "--file",
        str(ROOT / "fixtures" / "accounts.json"),
        "--account-id",
        account.account_id,
        "--now",
        NOW_ARG,
    )
    assert [rule["detail"] for rule in actual["rules"]] == [rule.detail for rule in expected.rules]


def test_the_node_gate_blocks_a_script_without_the_disclosure():
    code, payload = run_script(
        "evaluate-account.mjs",
        "--file",
        str(ROOT / "fixtures" / "accounts.json"),
        "--account-id",
        "A-1001",
        "--now",
        NOW_ARG,
        "--script",
        "Pay us today.",
    )
    assert payload["block_reason"] == "MISSING_DISCLOSURE"
    assert code == 2


def test_input_validation_accepts_the_fixture_batch():
    code, payload = run_script("validate-input.mjs", "--file", str(ROOT / "fixtures" / "accounts.json"))
    assert code == 0
    assert payload["unusable"] == 0
    assert payload["usable"] == 7


def test_input_validation_rejects_a_bad_row():
    row = {
        "account_id": "A-9999",
        "display_name": "X",
        "phone_e164": "5550101234",
        "timezone": "EST",
        "amount_due": 10,
        "currency": "USD",
        "consent_on_file": "yes",
    }
    code, payload = run_script("validate-input.mjs", "--account-json", json.dumps(row))
    assert code == 2
    problems = " ".join(payload["report"][0]["problems"])
    assert "E.164" in problems
    assert "IANA" in problems
    assert "true or false" in problems
    assert "5550101234" not in problems


def test_an_invalid_account_is_never_cleared():
    code, payload = run_script(
        "evaluate-account.mjs", "--account-json", json.dumps({"account_id": "A-9999"})
    )
    assert payload["decision"] == "block"
    assert payload["block_reason"] == "INVALID_INPUT"
    assert code == 1


@pytest.mark.parametrize(
    "utterance",
    ["stop calling me", "please dont call me again", "take me off your list", "opt me out"],
)
def test_node_and_python_revocation_detectors_agree(utterance, policy):
    code, payload = run_script("check-revocation.mjs", "--utterance", utterance)
    assert payload["revoked"] is True
    assert code == 3
    assert detect_revocation(utterance, policy) == payload["matched_phrase"]


def test_the_node_detector_ignores_the_agents_own_disclosure(tmp_path, policy, account):
    transcript = [
        {"speaker": "agent", "text": render_script(account, policy).disclosure},
        {"speaker": "recipient", "text": "Fine, what do I owe?"},
    ]
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(transcript), encoding="utf-8")
    code, payload = run_script("check-revocation.mjs", "--transcript", str(path))
    assert payload["revoked"] is False
    assert code == 0


def test_the_node_detector_finds_the_recipient_revocation(tmp_path, policy, account):
    transcript = [
        {"speaker": "agent", "text": render_script(account, policy).disclosure},
        {"speaker": "recipient", "text": "Take me off your list."},
    ]
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(transcript), encoding="utf-8")
    code, payload = run_script("check-revocation.mjs", "--transcript", str(path))
    assert payload["revoked"] is True
    assert payload["turn_index"] == 1
    assert payload["action"] == "end_call_and_suppress"
    assert code == 3
