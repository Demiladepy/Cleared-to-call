"""Capturing a real `get_call_run` payload without capturing a real person.

B1 needs the provider's actual response shape committed to the repository so the
extractors can be tested against reality rather than against a guess. The
payload arrives carrying the number that was dialled and the credentials used to
dial it, and neither may ever reach a committed file.
"""

from __future__ import annotations

import json

from cleared.calle_caller import CalleCaller, redact_payload
from cleared.schema import mask_phone

TERMINAL_PAYLOAD = {
    "structuredContent": {
        "status": "COMPLETED",
        "to_phones": ["+2348012345678"],
        "confirm_token": "eyJhbGciOi",
        "post_summary": "Reached +2348012345678. Recorded a promise to pay on 2026-09-20.",
        "transcript": [
            {"speaker": "bot", "text": "This is Northbridge Lending about your account."},
            {"speaker": "user", "text": "I can pay on the twentieth."},
        ],
    }
}


def test_an_e164_number_is_masked_wherever_it_appears():
    payload = {"structuredContent": {"to_phones": ["+2348012345678"]}}
    assert redact_payload(payload) == {
        "structuredContent": {"to_phones": [mask_phone("+2348012345678")]}
    }


def test_a_number_embedded_in_free_text_is_masked():
    payload = {"post_summary": "Called +2348012345678 and reached the account holder."}
    assert "+2348012345678" not in redact_payload(payload)["post_summary"]
    assert mask_phone("+2348012345678") in redact_payload(payload)["post_summary"]


def test_a_token_value_is_stripped_rather_than_masked():
    payload = {"confirm_token": "eyJhbGciOi", "access_token": "secret", "plan_id": "p-1"}
    redacted = redact_payload(payload)
    assert redacted["confirm_token"] == "<redacted>"
    assert redacted["access_token"] == "<redacted>"
    assert redacted["plan_id"] == "p-1"


def test_an_authorization_header_is_stripped():
    payload = {"headers": {"Authorization": "Bearer abc.def.ghi"}}
    assert redact_payload(payload)["headers"]["Authorization"] == "<redacted>"


def test_the_transcript_survives_redaction_intact():
    payload = {
        "transcript": [
            {"speaker": "bot", "text": "This is a call about your account."},
            {"speaker": "user", "text": "Stop calling me."},
        ]
    }
    assert redact_payload(payload)["transcript"] == payload["transcript"]


def test_redaction_does_not_mutate_the_original_payload():
    payload = {"to_phones": ["+2348012345678"], "confirm_token": "abc"}
    redact_payload(payload)
    assert payload == {"to_phones": ["+2348012345678"], "confirm_token": "abc"}


def test_a_redacted_payload_carries_no_digits_of_the_subscriber_number():
    payload = {"nested": {"deep": [{"phone": "+2348012345678"}]}}
    assert "8012345" not in str(redact_payload(payload))


# Capturing the terminal payload from a live run


def test_no_payload_is_written_when_no_capture_path_is_set(tmp_path):
    caller = CalleCaller()
    caller._report_from(TERMINAL_PAYLOAD, "run-1")
    assert list(tmp_path.iterdir()) == []


def test_the_terminal_payload_is_captured_to_the_given_path(tmp_path):
    target = tmp_path / "call_run_sample.json"
    caller = CalleCaller(capture_path=target)

    caller._report_from(TERMINAL_PAYLOAD, "run-1")

    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["structuredContent"]["status"] == "COMPLETED"


def test_a_captured_payload_carries_no_real_number_and_no_token(tmp_path):
    target = tmp_path / "call_run_sample.json"
    caller = CalleCaller(capture_path=target)

    caller._report_from(TERMINAL_PAYLOAD, "run-1")

    text = target.read_text(encoding="utf-8")
    assert "+2348012345678" not in text
    assert "eyJhbGciOi" not in text
    assert mask_phone("+2348012345678") in text


def test_a_captured_payload_keeps_the_transcript_the_extractors_must_parse(tmp_path):
    target = tmp_path / "call_run_sample.json"
    caller = CalleCaller(capture_path=target)

    caller._report_from(TERMINAL_PAYLOAD, "run-1")

    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["structuredContent"]["transcript"] == [
        {"speaker": "bot", "text": "This is Northbridge Lending about your account."},
        {"speaker": "user", "text": "I can pay on the twentieth."},
    ]


def test_capturing_does_not_change_the_report_that_is_returned(tmp_path):
    plain = CalleCaller()._report_from(TERMINAL_PAYLOAD, "run-1")
    captured = CalleCaller(capture_path=tmp_path / "s.json")._report_from(TERMINAL_PAYLOAD, "run-1")
    assert plain == captured


# The CLI flag that arms capture for a live run


def _run_args(**overrides):
    from cleared.cli import build_parser

    argv = ["run", "--execute", *[item for pair in overrides.items() for item in pair]]
    return build_parser().parse_args(argv)


def test_a_live_run_captures_nothing_unless_asked(policy):
    from cleared.cli import build_caller

    caller = build_caller(_run_args(), policy, dry_run=False)
    assert caller.capture_path is None


def test_the_capture_flag_arms_the_live_caller(policy, tmp_path):
    from cleared.cli import build_caller

    target = str(tmp_path / "call_run_sample.json")
    caller = build_caller(_run_args(**{"--capture-payload": target}), policy, dry_run=False)
    assert str(caller.capture_path) == target
