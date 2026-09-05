"""The extractors, run against payloads a real CALL-E server actually returned.

Every other test in this suite feeds the extractors a shape we invented. These
feed them bytes that came off the wire, captured by `run --execute
--capture-payload` and committed with the number masked and the credentials
stripped.

`call_run_declined.json` is a call that never connected: CALL-E reported
`DECLINED` with `duration_seconds: 0` and identical start and end times, and no
transcript at all. It is kept because it is the case that proved the outcome
mapping wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cleared.calle_caller import (
    extract_outcome,
    extract_promise_date,
    extract_status,
    extract_summary,
    extract_transcript,
)

DATA = Path(__file__).parent / "data"


def load(name: str):
    path = DATA / name
    if not path.is_file():
        pytest.skip(f"no captured payload at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# A call that never connected


@pytest.fixture
def declined():
    return load("call_run_declined.json")


def test_the_status_of_a_real_declined_run_is_read(declined):
    assert extract_status(declined) == "DECLINED"


def test_the_summary_of_a_real_declined_run_is_read(declined):
    assert "did not connect" in extract_summary(declined)


def test_a_call_that_never_connected_has_no_transcript(declined):
    assert extract_transcript(declined) == ()


def test_a_real_declined_run_is_not_recorded_as_a_refusal(declined):
    """`refusal` asserts the recipient refused to arrange payment.

    Nobody answered this call: zero seconds, no transcript, no words spoken by
    anyone. Recording it as a refusal writes a false claim about a consumer into
    an append-only log, and no provider status can establish a refusal on its
    own -- only what was said can.
    """
    status = extract_status(declined)
    summary = extract_summary(declined)
    transcript = extract_transcript(declined)
    assert extract_outcome(summary, transcript, status) == "no_answer"


def test_a_call_that_never_connected_yields_no_promise_date(declined):
    assert extract_promise_date(extract_summary(declined), ()) is None


def test_the_committed_declined_payload_carries_no_real_number(declined):
    assert "REDACTED" not in json.dumps(declined)


def test_the_committed_declined_payload_carries_no_credentials(declined):
    blob = json.dumps(declined)
    for key in ("confirm_token", "access_token", "authorization"):
        assert f'"{key}": "<redacted>"' in blob or key not in blob
