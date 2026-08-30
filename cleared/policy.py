"""Loader for the declarative compliance policy.

The policy is data, not code. `policy.json` holds the five rules, the call
window, the required disclosure elements and the revocation phrases. Nothing in
this package hard-codes a legal threshold; changing the policy file changes the
gate.

Each rule carries a `temporal_form` string. That is the property the rule is
meant to enforce, written the way a temporal-logic specification would state it.
It is documentation for reviewers and auditors: this package evaluates the
runtime checks, it does not model-check the formula.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_POLICY_PATH = Path(__file__).resolve().parent / "policy.json"


class PolicyError(ValueError):
    """Raised when the policy file is missing required structure."""


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    stage: str
    block_reason: str
    requirement: str
    authority: str
    temporal_form: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "stage": self.stage,
            "block_reason": self.block_reason,
            "requirement": self.requirement,
            "authority": self.authority,
            "temporal_form": self.temporal_form,
        }


@dataclass(frozen=True)
class DisclosureElement:
    id: str
    description: str
    patterns: tuple[re.Pattern[str], ...]

    def present_in(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self.patterns)


@dataclass(frozen=True)
class Policy:
    policy_id: str
    policy_version: str
    jurisdiction: str
    authorities: tuple[str, ...]
    window_start: time
    window_end: time
    timezone_source: str
    disclosure_elements: tuple[DisclosureElement, ...]
    rules: tuple[Rule, ...]
    revocation_phrases: tuple[str, ...]

    def rule(self, rule_id: str) -> Rule:
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        raise PolicyError(f"policy {self.policy_id} has no rule {rule_id}")

    @property
    def pre_dial_rules(self) -> tuple[Rule, ...]:
        return tuple(rule for rule in self.rules if rule.stage == "pre_dial")

    @property
    def in_call_rules(self) -> tuple[Rule, ...]:
        return tuple(rule for rule in self.rules if rule.stage == "in_call")

    @property
    def window_label(self) -> str:
        return f"{self.window_start.strftime('%H:%M')}-{self.window_end.strftime('%H:%M')}"


def _parse_hhmm(value: str, field_name: str) -> time:
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError) as error:
        raise PolicyError(f"{field_name} is not HH:MM: {value!r}") from error


def parse_policy(raw: dict[str, Any]) -> Policy:
    for key in ("policy_id", "policy_version", "call_window", "rules", "disclosure_elements"):
        if key not in raw:
            raise PolicyError(f"policy is missing required key: {key}")

    window = raw["call_window"]
    rules = tuple(
        Rule(
            id=item["id"],
            name=item["name"],
            stage=item["stage"],
            block_reason=item["block_reason"],
            requirement=item["requirement"],
            authority=item.get("authority", ""),
            temporal_form=item.get("temporal_form", ""),
        )
        for item in raw["rules"]
    )
    if not rules:
        raise PolicyError("policy defines no rules")

    elements = tuple(
        DisclosureElement(
            id=item["id"],
            description=item["description"],
            patterns=tuple(
                re.compile(pattern, re.IGNORECASE) for pattern in item["patterns"]
            ),
        )
        for item in raw["disclosure_elements"]
    )
    if not elements:
        raise PolicyError("policy defines no disclosure elements")

    return Policy(
        policy_id=raw["policy_id"],
        policy_version=raw["policy_version"],
        jurisdiction=raw.get("jurisdiction", "US-FEDERAL"),
        authorities=tuple(raw.get("authorities", ())),
        window_start=_parse_hhmm(window.get("start_local", ""), "call_window.start_local"),
        window_end=_parse_hhmm(window.get("end_local", ""), "call_window.end_local"),
        timezone_source=window.get("timezone_source", "account.timezone"),
        disclosure_elements=elements,
        rules=rules,
        revocation_phrases=tuple(raw.get("revocation_phrases", ())),
    )


def load_policy(path: str | Path | None = None) -> Policy:
    """Load a policy from disk. Defaults to the packaged US federal policy."""
    resolved = Path(path) if path else DEFAULT_POLICY_PATH
    if not resolved.is_file():
        raise PolicyError(f"policy file not found: {resolved}")
    return parse_policy(json.loads(resolved.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def default_policy() -> Policy:
    return load_policy()
