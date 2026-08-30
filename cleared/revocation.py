"""Rule 5: detect a live revocation of consent.

The recipient revoking consent is the one compliance event that cannot be
decided before dialing. The detector here is deliberately boring: normalize the
utterance, then look for one of the revocation phrases in the policy.

It runs in two places, and they are not the same thing:

- the call agent is instructed to end the call the moment it hears a revocation;
- this detector re-reads the transcript afterwards and is what actually writes
  the opt-out to the suppression list.

The second one is the enforcement point we control, so a missed opt-out by the
agent still ends with a suppressed number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .policy import Policy
from .schema import TranscriptTurn

RECIPIENT_SPEAKERS = {"recipient", "user", "customer", "human", "consumer", "callee"}

_APOSTROPHES = str.maketrans({"'": "", "’": "", "`": ""})
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def normalize_utterance(text: str) -> str:
    """Lowercase, drop apostrophes, collapse everything else to single spaces.

    This is what makes "Don't call me again!" and "do not call me again" the
    same string as far as matching is concerned.
    """
    lowered = text.lower().translate(_APOSTROPHES)
    collapsed = _NON_WORD_RE.sub(" ", lowered)
    return f" {collapsed.strip()} "


def detect_revocation(text: str, policy: Policy) -> str | None:
    """Return the matched revocation phrase, or None."""
    if not text:
        return None
    haystack = normalize_utterance(text)
    for phrase in policy.revocation_phrases:
        needle = normalize_utterance(phrase).strip()
        if needle and f" {needle} " in haystack:
            return phrase
    return None


@dataclass(frozen=True)
class RevocationHit:
    turn_index: int
    speaker: str
    text: str
    matched_phrase: str

    def to_dict(self) -> dict[str, object]:
        return {
            "turn_index": self.turn_index,
            "speaker": self.speaker,
            "text": self.text,
            "matched_phrase": self.matched_phrase,
        }


def scan_transcript(
    turns: Iterable[TranscriptTurn], policy: Policy
) -> RevocationHit | None:
    """Return the first recipient turn that revokes consent, if any.

    Only recipient turns count. The agent saying "you can tell me to stop
    calling" is a required disclosure, not an opt-out.
    """
    for index, turn in enumerate(turns):
        if turn.speaker.strip().lower() not in RECIPIENT_SPEAKERS:
            continue
        matched = detect_revocation(turn.text, policy)
        if matched:
            return RevocationHit(
                turn_index=index,
                speaker=turn.speaker,
                text=turn.text,
                matched_phrase=matched,
            )
    return None
