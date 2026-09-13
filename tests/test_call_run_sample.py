"""Extractor tests against the committed get_call_run fixture."""

from __future__ import annotations

import json
from pathlib import Path

from cleared.calle_caller import (
    extract_status,
    extract_summary,
    extract_transcript,
    resolve_call_outcome,
)
from cleared.policy import default_policy
from cleared.revocation import scan_transcript

FIXTURE = Path(__file__).resolve().parent / "data" / "call_run_sample.json"


def load_sample() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_file_exists():
    assert FIXTURE.is_file()


def test_the_sample_call_run_parses_to_opt_out():
    payload = load_sample()
    status = extract_status(payload)
    summary = extract_summary(payload)
    transcript = extract_transcript(payload)
    outcome, promise_date, source = resolve_call_outcome(payload, summary, transcript, status)

    assert status == "COMPLETED"
    assert "opt_out" in (summary or "")
    assert [turn.speaker for turn in transcript] == ["agent", "recipient", "agent"]
    assert transcript[1].text == "Stop calling me. I do not want these calls."
    assert outcome == "opt_out"
    assert promise_date is None
    assert source == "structured"


def test_the_sample_transcript_triggers_rule_five():
    payload = load_sample()
    hit = scan_transcript(extract_transcript(payload), default_policy())
    assert hit is not None
    assert hit.matched_phrase == "stop calling"
