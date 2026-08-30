"""Callers: the thing that actually places a call once the gate has cleared it.

Two implementations, one interface:

- `FakeCaller` replays a scripted conversation. Nothing is dialled, no credits
  are spent, and the transcript it returns goes through exactly the same
  revocation detection as a live one.
- `CalleCaller` places a real call through the CALL-E MCP server.

The gate never talks to a caller. A caller is only ever reached for an account
that already has an ALLOW decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .policy import Policy, default_policy
from .revocation import detect_revocation
from .schema import Account, CallReport, TranscriptTurn
from .script import CallScript, render_call_goal, render_user_input

OPT_OUT_ACKNOWLEDGEMENT = (
    "Understood. I will remove your number and you will not receive further "
    "calls from us. Goodbye."
)


class Caller(Protocol):
    """Places exactly one call for one cleared account."""

    def place_call(self, account: Account, script: CallScript) -> CallReport: ...


@dataclass
class ScriptedCall:
    """One rehearsed conversation for the dry-run caller."""

    outcome: str
    turns: tuple[tuple[str, str], ...] = ()
    promise_date: str | None = None
    answered: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ScriptedCall":
        return cls(
            outcome=str(raw.get("outcome", "no_answer")),
            turns=tuple(
                (str(turn.get("speaker", "recipient")), str(turn.get("text", "")))
                for turn in raw.get("turns", ())
            ),
            promise_date=raw.get("promise_date"),
            answered=bool(raw.get("answered", True)),
        )


@dataclass
class FakeCaller:
    """Dry-run caller. Replays scripted recipient turns against the real script.

    The agent's opening turns are the real rendered disclosure, so a dry run
    exercises the same disclosure check and the same opt-out handling a live
    call does.
    """

    scenarios: dict[str, ScriptedCall] = field(default_factory=dict)
    policy: Policy | None = None
    calls_placed: list[str] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str | Path, policy: Policy | None = None) -> "FakeCaller":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            scenarios={
                account_id: ScriptedCall.from_dict(scenario)
                for account_id, scenario in raw.items()
            },
            policy=policy,
        )

    def place_call(self, account: Account, script: CallScript) -> CallReport:
        active = self.policy or default_policy()
        self.calls_placed.append(account.account_id)
        scenario = self.scenarios.get(
            account.account_id, ScriptedCall(outcome="no_answer", answered=False)
        )

        if not scenario.answered:
            return CallReport(
                outcome="no_answer",
                transcript=(),
                provider_status="NO_ANSWER",
                raw={"simulated": True},
            )

        transcript: list[TranscriptTurn] = [
            TranscriptTurn("agent", script.disclosure),
            TranscriptTurn("agent", script.body),
        ]
        outcome = scenario.outcome
        promise_date = scenario.promise_date

        for speaker, text in scenario.turns:
            transcript.append(TranscriptTurn(speaker, text))
            if speaker.lower() in {"agent", "bot"}:
                continue
            if detect_revocation(text, active):
                # Same behaviour the live agent is instructed to follow:
                # acknowledge once, then hang up. Nothing after this is spoken.
                transcript.append(TranscriptTurn("agent", OPT_OUT_ACKNOWLEDGEMENT))
                outcome = "opt_out"
                promise_date = None
                break

        return CallReport(
            outcome=outcome,
            transcript=tuple(transcript),
            promise_date=promise_date,
            provider_status="COMPLETED",
            raw={"simulated": True},
        )


DEFAULT_BASE_URL = "https://seleven-mcp-sg.airudder.com"
DEFAULT_CHANNEL = "openagent_oauth"
DEFAULT_CACHE_ROOT = "~/.calle-mcp/cli"
TERMINAL_STATUSES = {
    "BUSY",
    "CANCELED",
    "CANCELLED",
    "COMPLETED",
    "DECLINED",
    "EXPIRED",
    "FAILED",
    "NO_ANSWER",
    "VOICEMAIL",
}
STATUS_TO_OUTCOME = {
    "BUSY": "no_answer",
    "CANCELED": "no_answer",
    "CANCELLED": "no_answer",
    "DECLINED": "refusal",
    "EXPIRED": "no_answer",
    "FAILED": "no_answer",
    "NO_ANSWER": "no_answer",
    "VOICEMAIL": "no_answer",
}


class CallerError(RuntimeError):
    """A call could not be placed or could not be followed to a terminal status."""


def build_plan_arguments(
    account: Account, script: CallScript, *, region: str = "US", language: str = "English"
) -> dict[str, Any]:
    """The `plan_call` payload for one cleared account."""
    return {
        "to_phones": [account.phone_e164],
        "region": region,
        "language": language,
        "goal": render_call_goal(script),
        "user_input": render_user_input(account),
    }


def build_call_metadata(account: Account, audit_ref: str | None, policy: Policy) -> dict[str, Any]:
    """Business metadata attached to the MCP tool call for tracing.

    The phone number is masked here: this travels with the provider record.
    """
    return {
        "call-e/customerMetadata": {
            "account_id": account.account_id,
            "phone_masked": account.masked_phone,
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "gate": "cleared-to-call",
            "pre_dial_decision": "allow",
            "audit_ref": audit_ref,
        }
    }
