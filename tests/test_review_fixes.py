"""Regression tests for the four issues raised reviewing PR #1.

1. The wrong-number check could never fire: CALL-E echoes a masked destination,
   and only full numbers were compared.
2. Redaction masked only numbers written with a `+`.
3. A failed payload capture could turn a connected call into a provider failure.
4. `--capture-payload` overwrote one file per batch, keeping only the last call.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cleared.calle_caller import (
    CalleCaller,
    CallerError,
    looks_like_epoch,
    plan_targets_only,
    redact_payload,
    require_verified_destination,
    sanitize_call_run,
)

CLEARED = "+2348012349724"

TERMINAL = {
    "status": "COMPLETED",
    "result": {"post_summary": "Reached the account holder.", "transcript": None},
}


# 1. The destination check -----------------------------------------------------


@pytest.mark.parametrize(
    "plan",
    [
        {"confirm_summary": "Call …9724 in English, region NG."},
        {"confirm_summary": "Call ...9724 in English."},
        {"destination": "+2********9724"},
        {"structuredContent": {"confirm_summary": "Dialling …9724"}},
    ],
)
def test_a_masked_destination_matching_the_last_four_is_accepted(plan):
    assert plan_targets_only(plan, CLEARED) == "last_four"


@pytest.mark.parametrize(
    "plan",
    [
        {"confirm_summary": "Call …0000 in English."},
        {"destination": "+1******1234"},
        {"confirm_summary": "Call ...5555 now."},
    ],
)
def test_a_masked_destination_for_someone_else_is_refused(plan):
    """This is the case that used to pass: the plan was aimed at another person."""
    with pytest.raises(CallerError, match="last four"):
        plan_targets_only(plan, CLEARED)


def test_the_account_tail_in_our_own_goal_text_is_not_mistaken_for_a_number():
    """The disclosure says "account ending 1001". Reading that as a destination
    would refuse every live call, so word-based masks are deliberately not matched."""
    plan = {
        "goal": "Hello, this is an automated assistant about your account ending 1001.",
        "confirm_summary": "Call …9724 in English.",
    }
    assert plan_targets_only(plan, CLEARED) == "last_four"


def test_a_full_number_still_takes_precedence():
    assert plan_targets_only({"to_phones": [CLEARED]}, CLEARED) == "full_number"


def test_a_plan_naming_no_destination_is_reported_as_unverified():
    assert plan_targets_only({"ready_to_run": True}, CLEARED) == "unverified"


def test_the_dialler_refuses_an_unverifiable_plan_by_default():
    """A check that cannot run is not a pass."""
    with pytest.raises(CallerError, match="cannot be checked"):
        require_verified_destination({"ready_to_run": True}, CLEARED, allow_unverified=False)


def test_the_operator_can_explicitly_accept_an_unverifiable_plan():
    result = require_verified_destination({"ready_to_run": True}, CLEARED, allow_unverified=True)
    assert result == "unverified"


def test_opting_in_does_not_let_a_wrong_number_through():
    with pytest.raises(CallerError):
        require_verified_destination(
            {"confirm_summary": "Call …0000."}, CLEARED, allow_unverified=True
        )


def test_the_live_caller_refuses_unverifiable_plans_unless_told_otherwise():
    assert CalleCaller().allow_unverified_destination is False


def test_the_cli_flag_arms_the_opt_in(policy):
    from cleared.cli import build_caller, build_parser

    args = build_parser().parse_args(["run", "--execute", "--allow-unverified-destination"])
    assert build_caller(args, policy, dry_run=False).allow_unverified_destination is True


# 2. Numbers written without a plus ----------------------------------------------


@pytest.mark.parametrize(
    "raw, leaked",
    [
        ("Reached 2348012349724 on the second ring.", "2348012349724"),
        ("callee 08012349724", "08012349724"),
        ("dialled 15550109876", "15550109876"),
    ],
)
def test_a_bare_number_in_text_is_masked(raw, leaked):
    redacted = redact_payload({"post_summary": raw})["post_summary"]
    assert leaked not in redacted
    assert leaked[-4:] in redacted, "the last four digits should survive, as in the audit log"


def test_a_number_stored_as_an_integer_is_masked():
    redacted = redact_payload({"callee": 2348012349724})
    assert "2348012349724" not in json.dumps(redacted)


@pytest.mark.parametrize("stamp", [1788305394792, "1788305394", "1788305394792"])
def test_timestamps_are_left_alone(stamp):
    """Masking them would make a captured sample useless and no safer."""
    assert redact_payload({"at": stamp}) == {"at": stamp}


def test_booleans_survive_redaction():
    assert redact_payload({"ready_to_run": True}) == {"ready_to_run": True}


def test_redaction_is_idempotent():
    once = redact_payload({"s": "call 2348012349724 and +2348012349724"})
    assert redact_payload(once) == once


def test_epoch_detection_does_not_swallow_phone_shapes():
    assert looks_like_epoch("1788305394792")
    assert looks_like_epoch("1788305394")
    assert not looks_like_epoch("2348012349724")
    assert not looks_like_epoch("15550109876")


# An independent guard over committed data. Deliberately not built from the
# production regexes: a guard that shares the bug it guards against cannot catch it.
_BARE_RUN = re.compile(r"(?<![0-9+*])[0-9]{10,15}(?![0-9])")


def _is_timestamp(digits: str) -> bool:
    return len(digits) in (10, 13) and digits[:2] in {"15", "16", "17", "18", "19"}


def test_no_committed_payload_contains_a_bare_phone_number():
    data = Path(__file__).parent / "data"
    for path in sorted(data.glob("*.json")):
        runs = [run for run in _BARE_RUN.findall(path.read_text(encoding="utf-8")) if not _is_timestamp(run)]
        assert runs == [], f"{path.name} contains {len(runs)} unmasked digit run(s)"


# 3. A capture must never corrupt the call's record --------------------------------


def test_a_failed_capture_does_not_raise(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    caller = CalleCaller(capture_path=blocker / "sample.json")

    report = caller._report_from(TERMINAL, "run-1")

    assert report.provider_status == "COMPLETED"
    failures = [event for event in caller.events if event.get("event") == "capture_failed"]
    assert len(failures) == 1


def test_a_failed_capture_leaves_the_outcome_unchanged(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    plain = CalleCaller()._report_from(TERMINAL, "run-1")
    broken = CalleCaller(capture_path=blocker / "sample.json")._report_from(TERMINAL, "run-1")
    assert (broken.outcome, broken.provider_status) == (plain.outcome, plain.provider_status)


# 4. One file per call -------------------------------------------------------------


def test_a_batch_keeps_every_capture(tmp_path):
    caller = CalleCaller(capture_path=tmp_path / "call.json")
    for run_id in ("run-a", "run-b", "run-c"):
        caller._report_from(TERMINAL, run_id)

    written = sorted(path.name for path in tmp_path.glob("*.json"))
    assert written == ["call-run-b.json", "call-run-c.json", "call.json"]


def test_a_run_id_placeholder_names_each_file(tmp_path):
    caller = CalleCaller(capture_path=str(tmp_path / "call-{run_id}.json"))
    caller._report_from(TERMINAL, "run-a")
    caller._report_from(TERMINAL, "run-b")
    assert sorted(path.name for path in tmp_path.glob("*.json")) == [
        "call-run-a.json",
        "call-run-b.json",
    ]


def test_every_capture_is_sanitized_the_same_way(tmp_path):
    payload = {"status": "COMPLETED", "note": "called 2348012349724", "access_token": "abc"}
    caller = CalleCaller(capture_path=tmp_path / "call.json")
    caller._report_from(payload, "run-a")
    written = json.loads((tmp_path / "call.json").read_text(encoding="utf-8"))
    assert written == json.loads(json.dumps(sanitize_call_run(payload), sort_keys=True))
