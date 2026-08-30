"""The pre-dial gate: rules 1 to 4.

Every predicate here is pure. It takes an account, a clock reading and the
already-rendered script, and returns a `RuleResult`. It reads no files, opens no
sockets and never calls `datetime.now()` itself, which is what makes the whole
policy testable at any instant of any day in any timezone.

The gate returns ALLOW only when all four pre-dial rules pass. On failure the
reported `block_reason` is the first failing rule in policy order, but every
rule is still evaluated so the audit record shows the full picture.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Container
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .policy import Policy, default_policy
from .schema import Account, Decision, RuleResult
from .script import missing_disclosure_elements

SuppressionCheck = Callable[[str], bool] | Container[str]


def _is_suppressed(phone: str, suppression: SuppressionCheck | None) -> bool:
    if suppression is None:
        return False
    if callable(suppression):
        return bool(suppression(phone))
    return phone in suppression


def local_time_for(account: Account, now: datetime) -> datetime | None:
    """The recipient's wall-clock time, or None when their timezone is unusable.

    The timezone comes from the account record only. It is never derived from
    the phone number, the country code, the operator's locale, or server time.
    """
    if not account.timezone:
        return None
    try:
        zone = ZoneInfo(account.timezone)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(zone)


def rule_call_window(account: Account, now: datetime, policy: Policy) -> RuleResult:
    """R1: dial only inside the recipient's local call window."""
    rule = policy.rule("R1")
    local = local_time_for(account, now)
    if local is None:
        return RuleResult(
            rule.id,
            rule.name,
            False,
            f"no usable IANA timezone on the account record: {account.timezone!r}",
        )

    local_clock = local.time()
    inside = policy.window_start <= local_clock < policy.window_end
    stamp = local.strftime("%Y-%m-%d %H:%M")
    return RuleResult(
        rule.id,
        rule.name,
        inside,
        f"local time {stamp} ({account.timezone}) is "
        f"{'inside' if inside else 'outside'} {policy.window_label}",
    )


def rule_consent_on_file(account: Account, policy: Policy) -> RuleResult:
    """R2: prior express consent, recorded, with a timestamp."""
    rule = policy.rule("R2")
    if not account.consent_on_file:
        return RuleResult(rule.id, rule.name, False, "no consent recorded on the account")
    if not account.consent_timestamp:
        return RuleResult(
            rule.id, rule.name, False, "consent flag is set but no consent timestamp is recorded"
        )
    if account.consent_at is None:
        return RuleResult(
            rule.id,
            rule.name,
            False,
            f"consent timestamp is unparseable: {account.consent_timestamp!r}",
        )
    return RuleResult(
        rule.id, rule.name, True, f"consent recorded {account.consent_timestamp}"
    )


def rule_not_suppressed(
    account: Account, suppression: SuppressionCheck | None, policy: Policy
) -> RuleResult:
    """R3: the number is not on the opt-out or do-not-call suppression list."""
    rule = policy.rule("R3")
    suppressed = _is_suppressed(account.phone_e164, suppression)
    return RuleResult(
        rule.id,
        rule.name,
        not suppressed,
        f"{account.masked_phone} is "
        f"{'on' if suppressed else 'not on'} the suppression list",
    )


def rule_disclosure_ready(script_text: str, policy: Policy) -> RuleResult:
    """R4: the rendered script carries every required disclosure element."""
    rule = policy.rule("R4")
    missing = missing_disclosure_elements(script_text, policy)
    if missing:
        return RuleResult(
            rule.id, rule.name, False, "script is missing: " + ", ".join(missing)
        )
    element_ids = ", ".join(element.id for element in policy.disclosure_elements)
    return RuleResult(rule.id, rule.name, True, f"script contains: {element_ids}")


def evaluate(
    account: Account,
    *,
    now: datetime,
    script_text: str,
    suppression: SuppressionCheck | None = None,
    policy: Policy | None = None,
) -> Decision:
    """Run the pre-dial gate. ALLOW only if every rule passes."""
    active = policy or default_policy()
    results = (
        rule_call_window(account, now, active),
        rule_consent_on_file(account, active),
        rule_not_suppressed(account, suppression, active),
        rule_disclosure_ready(script_text, active),
    )

    failed = [result for result in results if not result.passed]
    block_reason = active.rule(failed[0].rule_id).block_reason if failed else None
    local = local_time_for(account, now)

    return Decision(
        account_id=account.account_id,
        allowed=not failed,
        block_reason=block_reason,
        rules=results,
        evaluated_at=now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc),
        policy_id=active.policy_id,
        policy_version=active.policy_version,
        local_time=local.strftime("%Y-%m-%d %H:%M %Z") if local else None,
    )
