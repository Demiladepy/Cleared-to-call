"""The audit log: append-only, hash-chained, and detectably broken when edited."""

from __future__ import annotations

import json
from datetime import timedelta

from cleared.audit import (
    GENESIS_HASH,
    AuditLog,
    build_entry,
    compute_audit_ref,
    verify_entries,
    verify_file,
)

from .conftest import NOW


def append_three(log: AuditLog):
    return [
        log.append(
            account_id=f"A-100{index}",
            phone_masked="+1******123" + str(index),
            decision="block" if index else "allow",
            block_reason="NO_CONSENT" if index else None,
            rules_evaluated={"R1": "pass", "R2": "fail" if index else "pass", "R3": "pass", "R4": "pass"},
            outcome="not_called" if index else "promise_to_pay",
            policy_id="us-federal-collections",
            policy_version="1.0.0",
            timestamp=NOW + timedelta(seconds=index),
        )
        for index in range(3)
    ]


def test_first_entry_chains_from_genesis(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    entry = append_three(log)[0]
    assert entry["prev_hash"] == GENESIS_HASH
    assert entry["audit_ref"].startswith("aud_")
    assert len(entry["hash"]) == 64


def test_each_entry_chains_to_the_previous_hash(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    entries = append_three(log)
    assert entries[1]["prev_hash"] == entries[0]["hash"]
    assert entries[2]["prev_hash"] == entries[1]["hash"]


def test_a_clean_chain_verifies(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    append_three(log)
    result = log.verify()
    assert result.ok is True
    assert result.entries_checked == 3


def test_verification_survives_a_reload_from_disk(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_three(AuditLog(path))
    assert verify_file(path).ok is True


def test_editing_a_field_breaks_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_three(AuditLog(path))
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["block_reason"] = None
    tampered["decision"] = "allow"
    lines[1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = verify_file(path)
    assert result.ok is False
    assert result.first_bad_index == 1
    assert "audit_ref" in result.reason


def test_recomputing_the_hash_after_editing_still_breaks_the_chain(tmp_path):
    """A tamperer who fixes the entry's own hashes still breaks the next link."""
    path = tmp_path / "audit.jsonl"
    append_three(AuditLog(path))
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    from cleared.audit import BODY_FIELDS, compute_hash

    entries[1]["outcome"] = "promise_to_pay"
    body = {key: entries[1][key] for key in BODY_FIELDS}
    entries[1]["audit_ref"] = compute_audit_ref(body)
    entries[1]["hash"] = compute_hash(body, entries[1]["audit_ref"])
    path.write_text(
        "\n".join(json.dumps(entry, sort_keys=True, separators=(",", ":")) for entry in entries)
        + "\n",
        encoding="utf-8",
    )

    result = verify_file(path)
    assert result.ok is False
    assert result.first_bad_index == 2
    assert "prev_hash" in result.reason


def test_deleting_an_entry_breaks_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_three(AuditLog(path))
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")

    result = verify_file(path)
    assert result.ok is False
    assert result.first_bad_index == 1


def test_reordering_entries_breaks_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_three(AuditLog(path))
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0], lines[2], lines[1]]) + "\n", encoding="utf-8")
    assert verify_file(path).ok is False


def test_appending_continues_an_existing_file(tmp_path):
    path = tmp_path / "audit.jsonl"
    first = append_three(AuditLog(path))
    reopened = AuditLog(path)
    fourth = reopened.append(
        account_id="A-2000",
        phone_masked="+1******9999",
        decision="allow",
        rules_evaluated={"R1": "pass", "R2": "pass", "R3": "pass", "R4": "pass"},
        outcome="dispute",
        policy_id="us-federal-collections",
        policy_version="1.0.0",
        timestamp=NOW + timedelta(minutes=5),
    )
    assert fourth["prev_hash"] == first[-1]["hash"]
    assert reopened.verify().ok is True


def test_an_empty_log_verifies(tmp_path):
    assert AuditLog(tmp_path / "audit.jsonl").verify().ok is True


def test_missing_fields_are_reported(tmp_path):
    entry = build_entry(
        account_id="A-1",
        phone_masked="+1******1111",
        decision="allow",
        rules_evaluated={},
        prev_hash=GENESIS_HASH,
        policy_id="p",
        policy_version="1",
        timestamp=NOW,
    )
    del entry["outcome"]
    result = verify_entries([entry])
    assert result.ok is False
    assert "outcome" in result.reason


def test_audit_ref_is_stable_for_the_same_body():
    body = {
        "timestamp": NOW.isoformat(),
        "account_id": "A-1001",
        "phone_masked": "+1******1234",
        "decision": "allow",
        "block_reason": None,
        "rules_evaluated": {"R1": "pass"},
        "outcome": "promise_to_pay",
        "policy_id": "us-federal-collections",
        "policy_version": "1.0.0",
        "dry_run": True,
        "prev_hash": GENESIS_HASH,
    }
    assert compute_audit_ref(body) == compute_audit_ref(dict(reversed(list(body.items()))))


def test_entries_never_contain_an_unmasked_number(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_three(AuditLog(path))
    text = path.read_text(encoding="utf-8")
    assert "+15550101234" not in text
    assert "*" in text
