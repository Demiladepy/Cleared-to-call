"""Shared fixtures. Every test pins the clock: no test may depend on wall time."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from cleared.policy import load_policy
from cleared.schema import Account

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# 2026-08-28 13:30 UTC is 09:30 in New York, 08:30 in Chicago, 06:30 in Los Angeles.
NOW = datetime(2026, 8, 28, 13, 30, tzinfo=timezone.utc)


@pytest.fixture
def policy():
    return load_policy()


@pytest.fixture
def account():
    return Account(
        account_id="A-1001",
        display_name="J. Doe",
        phone_e164="+15550101234",
        timezone="America/New_York",
        amount_due=Decimal("220.00"),
        currency="USD",
        consent_on_file=True,
        consent_timestamp="2026-01-15T10:00:00Z",
    )


@pytest.fixture
def fixtures_dir():
    return FIXTURES
