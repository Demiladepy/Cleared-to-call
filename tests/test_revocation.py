"""Rule 5: recognising a live revocation of consent."""

from __future__ import annotations

import pytest

from cleared.revocation import detect_revocation, normalize_utterance, scan_transcript
from cleared.schema import TranscriptTurn


@pytest.mark.parametrize(
    "utterance",
    [
        "Stop calling me.",
        "stop calling",
        "Please don't call me again.",
        "Do not call this number again!",
        "Take me off your list.",
        "take me off the list, thanks",
        "Remove me from your list please",
        "I want to opt out.",
        "opt me out",
        "No more calls.",
        "Quit calling, seriously.",
        "Stop contacting me about this.",
        "Put me on your do not call list.",
    ],
)
def test_revocation_phrases_are_detected(utterance, policy):
    assert detect_revocation(utterance, policy) is not None


@pytest.mark.parametrize(
    "utterance",
    [
        "Yes, this is me.",
        "I can pay on September fifth.",
        "That balance is not mine.",
        "Can you call back tomorrow instead?",
        "I am at work right now.",
        "",
    ],
)
def test_ordinary_replies_are_not_revocations(utterance, policy):
    assert detect_revocation(utterance, policy) is None


def test_punctuation_and_apostrophes_do_not_matter(policy):
    assert detect_revocation("DON'T CALL ME!!!", policy) is not None
    assert detect_revocation("dont call me", policy) is not None
    assert detect_revocation("Don’t call me", policy) is not None


def test_normalization_pads_for_whole_phrase_matching():
    assert normalize_utterance("Don't call me!") == " dont call me "


def test_only_recipient_turns_count(policy):
    """The agent's own opt-out disclosure must not trip the detector."""
    transcript = [
        TranscriptTurn("agent", "If you would like us to stop calling, say stop calling."),
        TranscriptTurn("recipient", "Fine, what do I owe?"),
    ]
    assert scan_transcript(transcript, policy) is None


def test_the_first_recipient_revocation_is_returned(policy):
    transcript = [
        TranscriptTurn("agent", "This is an automated assistant."),
        TranscriptTurn("recipient", "Who is this?"),
        TranscriptTurn("recipient", "Stop calling me."),
        TranscriptTurn("recipient", "Take me off your list."),
    ]
    hit = scan_transcript(transcript, policy)
    assert hit is not None
    assert hit.turn_index == 2
    assert hit.matched_phrase == "stop calling"


def test_speaker_labels_are_matched_case_insensitively(policy):
    transcript = [TranscriptTurn("USER", "Stop calling me.")]
    assert scan_transcript(transcript, policy) is not None


def test_unknown_speaker_labels_are_ignored(policy):
    transcript = [TranscriptTurn("voicemail_system", "Stop calling me.")]
    assert scan_transcript(transcript, policy) is None


def test_an_empty_transcript_has_no_revocation(policy):
    assert scan_transcript([], policy) is None
