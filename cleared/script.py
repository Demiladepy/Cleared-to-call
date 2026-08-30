"""Render the call script and check it carries the mandatory disclosure.

Rule 4 is a property of the script, so the script has to be built before the
gate runs. `render_script` produces the exact words the agent must open with;
`check_disclosure` is the predicate the gate evaluates over them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .policy import Policy
from .schema import Account

DISCLOSURE_TEMPLATE = (
    "Hello, this is an automated assistant calling on behalf of {creditor} "
    "about your account ending {account_tail}. "
    "This is an attempt to collect a debt, and any information obtained will be "
    "used for that purpose. "
    "If you would like us to stop calling, say stop calling and I will end this "
    "call and remove your number."
)

BODY_TEMPLATE = (
    "Our records show a balance of {amount} {currency} that is past due. "
    "I am only here to arrange a payment date. "
    "Would you like to set a date to pay, or would you rather discuss this with "
    "a person?"
)


@dataclass(frozen=True)
class DisclosureCheck:
    element_id: str
    description: str
    present: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "description": self.description,
            "present": self.present,
        }


@dataclass(frozen=True)
class CallScript:
    account_id: str
    disclosure: str
    body: str

    @property
    def text(self) -> str:
        return f"{self.disclosure} {self.body}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "disclosure": self.disclosure,
            "body": self.body,
            "text": self.text,
        }


def format_amount(amount: Decimal) -> str:
    return f"{amount:.2f}"


def render_script(account: Account, policy: Policy | None = None) -> CallScript:
    """Build the script for one account. Never called for a blocked account's dial."""
    disclosure = DISCLOSURE_TEMPLATE.format(
        creditor=account.creditor_name,
        account_tail=account.account_id[-4:],
    )
    body = BODY_TEMPLATE.format(
        amount=format_amount(account.amount_due),
        currency=account.currency,
    )
    return CallScript(account_id=account.account_id, disclosure=disclosure, body=body)


def check_disclosure(script_text: str, policy: Policy) -> tuple[DisclosureCheck, ...]:
    """Check the rendered script against every required disclosure element."""
    return tuple(
        DisclosureCheck(
            element_id=element.id,
            description=element.description,
            present=element.present_in(script_text),
        )
        for element in policy.disclosure_elements
    )


def missing_disclosure_elements(script_text: str, policy: Policy) -> tuple[str, ...]:
    return tuple(
        check.element_id
        for check in check_disclosure(script_text, policy)
        if not check.present
    )


CALL_GOAL_TEMPLATE = """Place one outbound account-servicing call to the recipient.

Open the call by saying this disclosure, in full, before anything else:

"{disclosure}"

Then say:

"{body}"

Rules for this call:
- Do not skip, shorten, or paraphrase the opening disclosure.
- If the recipient says anything that means stop calling, do not call again, \
take me off your list, or opt me out: immediately acknowledge with "Understood. \
I will remove your number and you will not receive further calls from us. \
Goodbye." then end the call. Do not attempt to keep them on the line, do not \
ask why, and do not offer alternatives.
- Handle payment logistics only. Do not give financial, legal, or credit advice, \
do not negotiate the balance, and do not threaten any consequence.
- If the recipient disputes the debt, acknowledge the dispute, state that it \
will be recorded, and end the call.
- Keep the call under three minutes.

End the call by reporting exactly one outcome from this list: promise_to_pay, \
dispute, refusal, no_answer, opt_out. If the outcome is promise_to_pay, also \
report the date the recipient committed to, in YYYY-MM-DD format."""

USER_INPUT_TEMPLATE = (
    "Call {masked_phone} in English, region US, about past-due account "
    "{account_id}. Read the required disclosure first, arrange a payment date "
    "only, and end the call immediately if the recipient asks to stop being "
    "called."
)


def render_call_goal(script: CallScript) -> str:
    """The instruction handed to the CALL-E agent for a cleared account."""
    return CALL_GOAL_TEMPLATE.format(disclosure=script.disclosure, body=script.body)


def render_user_input(account: Account) -> str:
    return USER_INPUT_TEMPLATE.format(
        masked_phone=account.masked_phone,
        account_id=account.account_id,
    )
