"""Cleared to Call - a consent-and-compliance gate for outbound collection calls.

The call either happens lawfully, or provably does not.

    from cleared import gate, render_script, load_policy

    policy = load_policy()
    script = render_script(account, policy)
    decision = gate.evaluate(account, now=now, script_text=script.text,
                             suppression=suppression, policy=policy)
    if decision.allowed:
        ...  # only now may anything dial
"""

from __future__ import annotations

from .audit import AuditLog, verify_entries, verify_file
from .gate import evaluate
from .policy import Policy, load_policy
from .revocation import detect_revocation, scan_transcript
from .schema import Account, CallReport, CallResult, Decision, RuleResult, mask_phone
from .script import check_disclosure, render_script
from .suppression import SuppressionList

__all__ = [
    "Account",
    "AuditLog",
    "CallReport",
    "CallResult",
    "Decision",
    "Policy",
    "RuleResult",
    "SuppressionList",
    "check_disclosure",
    "detect_revocation",
    "evaluate",
    "load_policy",
    "mask_phone",
    "render_script",
    "scan_transcript",
    "verify_entries",
    "verify_file",
]

__version__ = "0.1.0"
