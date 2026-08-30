"""Data model for Cleared to Call.

Everything here is a plain, frozen value object. No I/O, no clock reads, no
network. The gate, the audit log and the demo app all speak these types.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")

BLOCK_REASONS = (
    "OUTSIDE_CALL_WINDOW",
    "NO_CONSENT",
    "ON_SUPPRESSION_LIST",
    "MISSING_DISCLOSURE",
)

OUTCOMES = (
    "promise_to_pay",
    "dispute",
    "refusal",
    "no_answer",
    "opt_out",
    "not_called",
)


class InvalidAccountError(ValueError):
    """Raised when a fixture row cannot be trusted enough to dial from."""


def mask_phone(phone: str) -> str:
    """Mask a phone number for logs and summaries: +1******1234."""
    if not phone:
        return ""
    if len(phone) <= 6:
        return phone[0] + "*" * (len(phone) - 1)
    return phone[:2] + "*" * (len(phone) - 6) + phone[-4:]


def parse_iso8601(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting a trailing Z, as an aware UTC datetime."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp is missing a UTC offset: {value}")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class Account:
    """One consumer account in an outbound batch."""

    account_id: str
    display_name: str
    phone_e164: str
    timezone: str
    amount_due: Decimal
    currency: str
    consent_on_file: bool
    consent_timestamp: str | None = None
    creditor_name: str = "Northbridge Lending"

    @property
    def masked_phone(self) -> str:
        return mask_phone(self.phone_e164)

    @property
    def consent_at(self) -> datetime | None:
        if not self.consent_timestamp:
            return None
        try:
            return parse_iso8601(self.consent_timestamp)
        except ValueError:
            return None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Account":
        problems = validate_account_dict(raw)
        if problems:
            account_id = raw.get("account_id") or "<unknown>"
            raise InvalidAccountError(f"{account_id}: " + "; ".join(problems))
        return cls(
            account_id=str(raw["account_id"]),
            display_name=str(raw["display_name"]),
            phone_e164=str(raw["phone_e164"]),
            timezone=str(raw["timezone"]),
            amount_due=Decimal(str(raw["amount_due"])),
            currency=str(raw["currency"]),
            consent_on_file=bool(raw["consent_on_file"]),
            consent_timestamp=(
                str(raw["consent_timestamp"]) if raw.get("consent_timestamp") else None
            ),
            creditor_name=str(raw.get("creditor_name") or "Northbridge Lending"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "display_name": self.display_name,
            "phone_e164": self.phone_e164,
            "timezone": self.timezone,
            "amount_due": float(self.amount_due),
            "currency": self.currency,
            "consent_on_file": self.consent_on_file,
            "consent_timestamp": self.consent_timestamp,
            "creditor_name": self.creditor_name,
        }


REQUIRED_ACCOUNT_FIELDS = (
    "account_id",
    "display_name",
    "phone_e164",
    "timezone",
    "amount_due",
    "currency",
    "consent_on_file",
)


def validate_account_dict(raw: Any) -> list[str]:
    """Return a list of human-readable problems. Empty list means the row is usable.

    Shape validation only. Whether the account may be *called* is the gate's job.
    """
    if not isinstance(raw, dict):
        return ["account must be a JSON object"]

    problems: list[str] = []
    for key in REQUIRED_ACCOUNT_FIELDS:
        if key not in raw or raw[key] is None or raw[key] == "":
            problems.append(f"missing required field: {key}")

    phone = raw.get("phone_e164")
    if isinstance(phone, str) and phone and not E164_RE.match(phone):
        problems.append(f"phone_e164 is not E.164: {mask_phone(phone)}")

    tz = raw.get("timezone")
    if isinstance(tz, str) and tz and "/" not in tz:
        problems.append(f"timezone is not an IANA zone name: {tz}")

    amount = raw.get("amount_due")
    if amount is not None:
        try:
            Decimal(str(amount))
        except (InvalidOperation, ValueError):
            problems.append(f"amount_due is not a number: {amount!r}")

    consent = raw.get("consent_on_file")
    if consent is not None and not isinstance(consent, bool):
        problems.append("consent_on_file must be true or false")

    stamp = raw.get("consent_timestamp")
    if stamp:
        try:
            parse_iso8601(str(stamp))
        except ValueError as error:
            problems.append(f"consent_timestamp is not a UTC ISO-8601 timestamp: {error}")

    return problems


@dataclass(frozen=True)
class RuleResult:
    """The outcome of one policy rule against one account."""

    rule_id: str
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Decision:
    """The pre-dial verdict. `allowed` is true only when every rule passed."""

    account_id: str
    allowed: bool
    block_reason: str | None
    rules: tuple[RuleResult, ...]
    evaluated_at: datetime
    policy_id: str
    policy_version: str
    local_time: str | None = None

    @property
    def rules_evaluated(self) -> dict[str, str]:
        return {rule.rule_id: ("pass" if rule.passed else "fail") for rule in self.rules}

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "decision": "allow" if self.allowed else "block",
            "block_reason": self.block_reason,
            "rules": [rule.to_dict() for rule in self.rules],
            "evaluated_at": self.evaluated_at.astimezone(timezone.utc).isoformat(),
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "local_time": self.local_time,
        }


@dataclass(frozen=True)
class CallResult:
    """The per-account record a batch run emits, blocked or called."""

    account_id: str
    call_placed: bool
    block_reason: str | None
    disclosure_given: bool
    outcome: str
    opt_out: bool
    audit_ref: str
    promise_date: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "call_placed": self.call_placed,
            "block_reason": self.block_reason,
            "disclosure_given": self.disclosure_given,
            "outcome": self.outcome,
            "promise_date": self.promise_date,
            "opt_out": self.opt_out,
            "audit_ref": self.audit_ref,
        }


@dataclass(frozen=True)
class TranscriptTurn:
    speaker: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"speaker": self.speaker, "text": self.text}


@dataclass(frozen=True)
class CallReport:
    """What a caller (fake or CALL-E) returns after one call attempt."""

    outcome: str
    transcript: tuple[TranscriptTurn, ...] = field(default_factory=tuple)
    promise_date: str | None = None
    provider_status: str | None = None
    provider_run_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
