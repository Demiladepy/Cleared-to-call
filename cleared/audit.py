"""Append-only, hash-chained audit log.

One line of JSON per decision, blocked or called. Each line carries the hash of
the line before it, so removing, reordering or editing any entry breaks the
chain from that point on. That is the whole mechanism: it makes tampering
detectable, it does not make it impossible.

Two hashes per entry:

- `audit_ref` is derived from the entry body (everything except the ref and the
  hash), which is what lets a result record point at its audit line;
- `hash` covers the body, the `audit_ref` and the `prev_hash`.

`verify_entries` recomputes both, in order, from the genesis hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

GENESIS_HASH = "0" * 64

BODY_FIELDS = (
    "timestamp",
    "account_id",
    "phone_masked",
    "decision",
    "block_reason",
    "rules_evaluated",
    "outcome",
    "policy_id",
    "policy_version",
    "dry_run",
    "provider_ref",
    "prev_hash",
)

# An entry's `decision` is one of these. `intent` and `dispatched` exist so a
# call that is in flight is on disk before it can be lost: see B3 in
# TEAMMATE-TASKS.md. A run that dies after `intent` must never be re-dialled.
# `unreconciled` is deliberately NOT terminal: it records that we refused to
# re-dial, without clearing the in-flight state that caused the refusal. A
# terminal entry here would make the next run believe the account was settled
# and dial the person again, which is the whole bug.
DECISIONS = ("allow", "block", "intent", "dispatched", "unreconciled")


def canonical_json(payload: dict[str, Any]) -> str:
    """Stable serialization: sorted keys, no incidental whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_audit_ref(body: dict[str, Any]) -> str:
    return "aud_" + _sha256(canonical_json(body))[:8]


def compute_hash(body: dict[str, Any], audit_ref: str) -> str:
    return _sha256(canonical_json({**body, "audit_ref": audit_ref}))


def build_entry(
    *,
    account_id: str,
    phone_masked: str,
    decision: str,
    rules_evaluated: dict[str, str],
    prev_hash: str,
    policy_id: str,
    policy_version: str,
    block_reason: str | None = None,
    outcome: str | None = None,
    dry_run: bool = False,
    provider_ref: str | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Build one complete, self-verifying audit entry."""
    moment = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
    body = {
        "timestamp": moment.isoformat(),
        "account_id": account_id,
        "phone_masked": phone_masked,
        "decision": decision,
        "block_reason": block_reason,
        "rules_evaluated": rules_evaluated,
        "outcome": outcome,
        "policy_id": policy_id,
        "policy_version": policy_version,
        "dry_run": dry_run,
        "provider_ref": provider_ref,
        "prev_hash": prev_hash,
    }
    audit_ref = compute_audit_ref(body)
    return {**body, "audit_ref": audit_ref, "hash": compute_hash(body, audit_ref)}


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    entries_checked: int
    first_bad_index: int | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "entries_checked": self.entries_checked,
            "first_bad_index": self.first_bad_index,
            "reason": self.reason,
        }


def verify_entries(entries: Sequence[dict[str, Any]]) -> VerificationResult:
    """Walk the chain from genesis and recompute every hash."""
    prev = GENESIS_HASH
    for index, entry in enumerate(entries):
        missing = [key for key in (*BODY_FIELDS, "audit_ref", "hash") if key not in entry]
        if missing:
            return VerificationResult(
                False, index, index, f"entry is missing fields: {', '.join(missing)}"
            )
        if entry["prev_hash"] != prev:
            return VerificationResult(
                False,
                index,
                index,
                f"prev_hash does not match the previous entry hash "
                f"(expected {prev[:12]}..., found {str(entry['prev_hash'])[:12]}...)",
            )
        body = {key: entry[key] for key in BODY_FIELDS}
        expected_ref = compute_audit_ref(body)
        if entry["audit_ref"] != expected_ref:
            return VerificationResult(
                False, index, index, f"audit_ref does not match entry body ({entry['audit_ref']})"
            )
        expected_hash = compute_hash(body, entry["audit_ref"])
        if entry["hash"] != expected_hash:
            return VerificationResult(
                False, index, index, f"hash does not match entry body ({entry['audit_ref']})"
            )
        prev = entry["hash"]
    return VerificationResult(True, len(entries))


def read_entries(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        target.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entries.append(json.loads(stripped))
        except json.JSONDecodeError as error:
            raise ValueError(f"{target}:{line_number} is not valid JSON: {error}") from error
    return entries


def verify_file(path: str | Path) -> VerificationResult:
    return verify_entries(read_entries(path))


class AuditLog:
    """Append-only JSONL audit log. Entries are never rewritten in place."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cached_last_hash: str | None = None

    def entries(self) -> list[dict[str, Any]]:
        return read_entries(self.path)

    def last_hash(self) -> str:
        if self._cached_last_hash is None:
            entries = self.entries()
            self._cached_last_hash = entries[-1]["hash"] if entries else GENESIS_HASH
        return self._cached_last_hash

    def append(
        self,
        *,
        account_id: str,
        phone_masked: str,
        decision: str,
        rules_evaluated: dict[str, str],
        policy_id: str,
        policy_version: str,
        block_reason: str | None = None,
        outcome: str | None = None,
        dry_run: bool = False,
        provider_ref: str | None = None,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        entry = build_entry(
            account_id=account_id,
            phone_masked=phone_masked,
            decision=decision,
            rules_evaluated=rules_evaluated,
            prev_hash=self.last_hash(),
            policy_id=policy_id,
            policy_version=policy_version,
            block_reason=block_reason,
            outcome=outcome,
            dry_run=dry_run,
            provider_ref=provider_ref,
            timestamp=timestamp,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(entry) + "\n")
        self._cached_last_hash = entry["hash"]
        return entry

    def verify(self) -> VerificationResult:
        return verify_entries(self.entries())
