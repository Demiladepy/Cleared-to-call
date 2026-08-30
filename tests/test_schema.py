"""Input validation, masking, and the suppression list."""

from __future__ import annotations

from datetime import timezone

import pytest

from cleared.schema import (
    Account,
    InvalidAccountError,
    mask_phone,
    parse_iso8601,
    validate_account_dict,
)
from cleared.suppression import SuppressionList, normalize_phone

from .conftest import NOW

VALID_ROW = {
    "account_id": "A-1001",
    "display_name": "J. Doe",
    "phone_e164": "+15550101234",
    "timezone": "America/New_York",
    "amount_due": 220.00,
    "currency": "USD",
    "consent_on_file": True,
    "consent_timestamp": "2026-01-15T10:00:00Z",
}


def test_a_valid_row_has_no_problems():
    assert validate_account_dict(VALID_ROW) == []


def test_a_valid_row_becomes_an_account():
    account = Account.from_dict(VALID_ROW)
    assert account.account_id == "A-1001"
    assert float(account.amount_due) == 220.0
    assert account.consent_at.tzinfo == timezone.utc


@pytest.mark.parametrize("field", ["account_id", "phone_e164", "timezone", "currency"])
def test_missing_required_fields_are_reported(field):
    row = {**VALID_ROW}
    del row[field]
    assert any(field in problem for problem in validate_account_dict(row))


@pytest.mark.parametrize(
    "phone", ["5550101234", "+1 555 010 1234", "+0155501012345", "not-a-number", "+1555"]
)
def test_non_e164_numbers_are_rejected(phone):
    problems = validate_account_dict({**VALID_ROW, "phone_e164": phone})
    assert any("E.164" in problem for problem in problems)


def test_a_rejected_number_is_masked_in_the_error_message():
    problems = validate_account_dict({**VALID_ROW, "phone_e164": "5550101234"})
    assert "5550101234" not in " ".join(problems)


def test_a_non_iana_timezone_is_rejected():
    problems = validate_account_dict({**VALID_ROW, "timezone": "EST"})
    assert any("IANA" in problem for problem in problems)


def test_a_non_boolean_consent_flag_is_rejected():
    problems = validate_account_dict({**VALID_ROW, "consent_on_file": "yes"})
    assert any("true or false" in problem for problem in problems)


def test_a_malformed_consent_timestamp_is_rejected():
    problems = validate_account_dict({**VALID_ROW, "consent_timestamp": "2026-13-45"})
    assert any("consent_timestamp" in problem for problem in problems)


def test_a_timestamp_without_an_offset_is_rejected():
    problems = validate_account_dict({**VALID_ROW, "consent_timestamp": "2026-01-15T10:00:00"})
    assert any("UTC offset" in problem for problem in problems)


def test_from_dict_raises_on_an_unusable_row():
    with pytest.raises(InvalidAccountError) as error:
        Account.from_dict({**VALID_ROW, "phone_e164": "nope"})
    assert "A-1001" in str(error.value)


def test_a_non_object_row_is_rejected():
    assert validate_account_dict(["A-1001"]) == ["account must be a JSON object"]


@pytest.mark.parametrize(
    "raw, masked",
    [
        ("+15550101234", "+1******1234"),
        ("+6598765432", "+6*****5432"),
        ("+12345", "+*****"),
        ("", ""),
    ],
)
def test_masking_keeps_two_leading_and_four_trailing_characters(raw, masked):
    assert mask_phone(raw) == masked


def test_parse_iso8601_accepts_z_and_offsets():
    assert parse_iso8601("2026-01-15T10:00:00Z") == parse_iso8601("2026-01-15T05:00:00-05:00")


# Suppression list


def test_normalize_strips_formatting():
    assert normalize_phone("+1 (555) 010-1234") == "+15550101234"


def test_a_number_added_is_suppressed(tmp_path):
    suppression = SuppressionList(tmp_path / "s.jsonl")
    assert suppression.contains("+15550101234") is False
    suppression.add("+15550101234", reason="OPT_OUT", source="test", timestamp=NOW)
    assert suppression.contains("+15550101234") is True
    assert "+15550101234" in suppression


def test_suppression_matches_across_formatting(tmp_path):
    suppression = SuppressionList(tmp_path / "s.jsonl")
    suppression.add("+15550101234", reason="OPT_OUT", source="test", timestamp=NOW)
    assert suppression.contains("+1 (555) 010-1234") is True


def test_adding_the_same_number_twice_is_a_no_op(tmp_path):
    suppression = SuppressionList(tmp_path / "s.jsonl")
    suppression.add("+15550101234", reason="OPT_OUT", source="test", timestamp=NOW)
    assert suppression.add("+15550101234", reason="OPT_OUT", source="test", timestamp=NOW) is None
    assert len(suppression) == 1


def test_suppression_survives_a_reload(tmp_path):
    path = tmp_path / "s.jsonl"
    SuppressionList(path).add("+15550101234", reason="OPT_OUT", source="test", timestamp=NOW)
    assert SuppressionList(path).contains("+15550101234") is True


def test_suppression_entries_can_be_shown_masked(tmp_path):
    suppression = SuppressionList(tmp_path / "s.jsonl")
    suppression.add("+15550101234", reason="OPT_OUT", source="test", timestamp=NOW)
    assert suppression.entries()[0].to_masked_dict()["phone_e164"] == "+1******1234"


def test_the_seed_suppression_fixture_loads(fixtures_dir):
    suppression = SuppressionList(fixtures_dir / "suppression.jsonl")
    assert suppression.contains("+15550101237") is True
    assert len(suppression) == 2
