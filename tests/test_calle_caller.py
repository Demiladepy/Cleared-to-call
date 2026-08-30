"""Parsing and safety checks for the live CALL-E caller.

No network, no credentials, no provider. These cover the parts that decide what
a real call meant, plus the plan check that stops a call going to the wrong
number.
"""

from __future__ import annotations

import pytest

from cleared.callers import CallerError, build_call_metadata, build_plan_arguments
from cleared.calle_caller import (
    extract_outcome,
    extract_promise_date,
    extract_status,
    extract_summary,
    extract_transcript,
    plan_targets_only,
    resolve_server_url,
    token_cache_path,
)
from cleared.revocation import scan_transcript
from cleared.schema import TranscriptTurn
from cleared.script import render_call_goal, render_script


def test_server_url_is_built_from_base_and_channel():
    assert resolve_server_url("https://example.com/", "openagent_oauth", None) == (
        "https://example.com/mcp/openagent_oauth"
    )


def test_an_explicit_server_url_wins():
    assert resolve_server_url("https://example.com", "c", "https://other.example/mcp") == (
        "https://other.example/mcp"
    )


def test_the_token_cache_path_is_keyed_by_server_url(tmp_path):
    first = token_cache_path(str(tmp_path), "https://example.com/mcp/a")
    second = token_cache_path(str(tmp_path), "https://example.com/mcp/b")
    assert first != second
    assert first.name == "token.json"


# Plan inspection


def test_a_plan_for_the_cleared_number_is_accepted():
    plan = {"structuredContent": {"to_phones": ["+15550101234"], "ready_to_run": True}}
    plan_targets_only(plan, "+15550101234")


def test_a_plan_for_a_different_number_is_refused():
    plan = {"structuredContent": {"to_phones": ["+15550109999"]}}
    with pytest.raises(CallerError, match="other than the"):
        plan_targets_only(plan, "+15550101234")


def test_a_plan_with_an_extra_number_anywhere_is_refused():
    plan = {"content": [{"text": "Calling +15550101234 and also +15550109999"}]}
    with pytest.raises(CallerError):
        plan_targets_only(plan, "+15550101234")


def test_a_plan_with_no_numbers_is_not_blocked():
    plan_targets_only({"structuredContent": {"ready_to_run": True}}, "+15550101234")


# Response parsing


def test_status_is_read_from_a_nested_payload():
    assert extract_status({"structuredContent": {"call": {"status": "completed"}}}) == "COMPLETED"


def test_summary_is_read_from_a_nested_payload():
    assert extract_summary({"result": {"post_summary": "Promise to pay on 2026-09-05."}}) == (
        "Promise to pay on 2026-09-05."
    )


def test_a_list_transcript_is_normalized():
    payload = {
        "structuredContent": {
            "transcript": [
                {"role": "BOT", "text": "This is an automated assistant."},
                {"role": "USER", "text": "Stop calling me."},
            ]
        }
    }
    transcript = extract_transcript(payload)
    assert [turn.speaker for turn in transcript] == ["agent", "recipient"]
    assert transcript[1].text == "Stop calling me."


def test_an_inline_transcript_is_split_into_turns():
    payload = {
        "transcript": "[00:00:00] BOT: Hello, this is an automated assistant. "
        "[00:00:06] USER: Take me off your list."
    }
    transcript = extract_transcript(payload)
    assert len(transcript) == 2
    assert transcript[0].speaker == "agent"
    assert transcript[1].speaker == "recipient"
    assert transcript[1].text == "Take me off your list."


def test_a_normalized_transcript_feeds_the_opt_out_check(policy):
    payload = {"transcript": "[00:00:00] BOT: Hello. [00:00:04] USER: Stop calling me."}
    hit = scan_transcript(extract_transcript(payload), policy)
    assert hit is not None
    assert hit.matched_phrase == "stop calling"


def test_unknown_speaker_labels_are_kept_but_not_treated_as_the_recipient(policy):
    payload = {"transcript": [{"role": "ivr", "text": "Stop calling me."}]}
    transcript = extract_transcript(payload)
    assert transcript[0].speaker == "ivr"
    assert scan_transcript(transcript, policy) is None


def test_an_empty_response_yields_no_transcript():
    assert extract_transcript({"structuredContent": {}}) == ()


# Outcome mapping


@pytest.mark.parametrize(
    "summary, expected",
    [
        ("Outcome: promise_to_pay on 2026-09-05", "promise_to_pay"),
        ("The recipient disputed the balance.", "dispute"),
        ("Outcome: refusal", "refusal"),
        ("Outcome: opt_out, number must be suppressed", "opt_out"),
        ("Outcome: no_answer", "no_answer"),
    ],
)
def test_the_reported_outcome_is_read_from_the_summary(summary, expected):
    assert extract_outcome(summary, (), "COMPLETED") == expected


def test_opt_out_wins_over_any_other_reported_outcome():
    summary = "The recipient asked to opt out, then said promise_to_pay."
    assert extract_outcome(summary, (), "COMPLETED") == "opt_out"


def test_a_provider_status_is_used_when_the_agent_reported_nothing():
    assert extract_outcome(None, (), "NO_ANSWER") == "no_answer"
    assert extract_outcome(None, (), "VOICEMAIL") == "no_answer"
    assert extract_outcome(None, (), "DECLINED") == "refusal"


def test_an_unparseable_completed_call_with_no_transcript_is_no_answer():
    assert extract_outcome(None, (), "COMPLETED") == "no_answer"


def test_only_agent_turns_are_read_for_the_outcome():
    transcript = (TranscriptTurn("recipient", "I promise to pay, honestly"),)
    assert extract_outcome(None, transcript, "COMPLETED") == "refusal"


def test_a_promise_date_is_extracted_only_for_a_promise():
    summary = "Outcome: promise_to_pay on 2026-09-05"
    assert extract_promise_date(summary, ()) == "2026-09-05"
    assert extract_promise_date("Outcome: dispute", ()) is None


# The call task handed to CALL-E


def test_the_call_goal_carries_the_disclosure_verbatim(account, policy):
    script = render_script(account, policy)
    goal = render_call_goal(script)
    assert script.disclosure in goal
    assert "Do not skip, shorten, or paraphrase" in goal


def test_the_call_goal_instructs_the_agent_to_honor_an_opt_out(account, policy):
    goal = render_call_goal(render_script(account, policy))
    assert "end the call" in goal
    assert "do not ask why" in goal.lower()
    for outcome in ("promise_to_pay", "dispute", "refusal", "no_answer", "opt_out"):
        assert outcome in goal


def test_the_plan_payload_targets_exactly_one_number(account, policy):
    arguments = build_plan_arguments(account, render_script(account, policy))
    assert arguments["to_phones"] == [account.phone_e164]
    assert arguments["region"] == "US"
    assert account.phone_e164 not in arguments["user_input"]


def test_provider_metadata_carries_only_a_masked_number(account, policy):
    meta = build_call_metadata(account, "aud_12345678", policy)
    payload = meta["call-e/customerMetadata"]
    assert payload["phone_masked"] == "+1******1234"
    assert payload["audit_ref"] == "aud_12345678"
    assert account.phone_e164 not in str(payload)
