"""
Shared fixtures for the RentaBot test suite.

Must be loaded before any src.agents.rentabot import so GEMINI_API_KEY
is available when the agent module initialises at import time.
"""

# ---------------------------------------------------------------------------
# Ensure GEMINI_API_KEY is available before rentabot.py is imported.
# load_dotenv() reads the real key from .env if present; setdefault provides
# a dummy fallback so unit tests work without any credentials file.
# ---------------------------------------------------------------------------
import os
from dotenv import load_dotenv

load_dotenv()  # loads .env if it exists (sets real key for llm tests)
os.environ.setdefault("GEMINI_API_KEY", "test-dummy-key-for-unit-tests")

import pytest
from unittest.mock import MagicMock

from src.agents.deps import AgentDeps
from src.sheets.client import SheetsClient


# ---------------------------------------------------------------------------
# Static sample data — reused across test files
# ---------------------------------------------------------------------------

SAMPLE_TENANTS = [
    # Active tenants across two properties
    {
        "tenant_name": "Alice",
        "property_code": "C1613A",
        "room_id": "R1",
        "status": "active",
        "monthly_rent": "800",
    },
    {
        "tenant_name": "Bob",
        "property_code": "C1613A",
        "room_id": "R2",
        "status": "active",
        "monthly_rent": "700",
    },
    {
        "tenant_name": "Charlie",
        "property_code": "SA1903A",
        "room_id": "R1",
        "status": "active",
        "monthly_rent": "900",
    },
    # Inactive — must NOT appear in active-only results
    {
        "tenant_name": "Dave",
        "property_code": "C1613A",
        "room_id": "R3",
        "status": "inactive",
        "monthly_rent": "750",
    },
]

SAMPLE_TRANSACTIONS = [
    # May 2025 — C1613A
    {
        "date": "2025-05-01",
        "month_tag": "2025-05",
        "property_code": "C1613A",
        "room_id": "R1",
        "category": "rental",
        "description": "R1 Rental",
        "amount": "800",
        "type": "income",
    },
    {
        "date": "2025-05-02",
        "month_tag": "2025-05",
        "property_code": "C1613A",
        "room_id": "",
        "category": "utility",
        "description": "TNB",
        "amount": "120",
        "type": "expense",
    },
    # May 2025 — SA1903A
    {
        "date": "2025-05-03",
        "month_tag": "2025-05",
        "property_code": "SA1903A",
        "room_id": "R1",
        "category": "rental",
        "description": "R1 Rental",
        "amount": "900",
        "type": "income",
    },
    # April 2025 — C1613A (different month)
    {
        "date": "2025-04-01",
        "month_tag": "2025-04",
        "property_code": "C1613A",
        "room_id": "R1",
        "category": "rental",
        "description": "R1 Rental",
        "amount": "800",
        "type": "income",
    },
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_tenants():
    return [t.copy() for t in SAMPLE_TENANTS]


@pytest.fixture
def sample_transactions():
    return [t.copy() for t in SAMPLE_TRANSACTIONS]


@pytest.fixture
def mock_sheets(sample_tenants, sample_transactions):
    """MagicMock(spec=SheetsClient) pre-wired with sensible defaults.

    Individual tests can override specific return values as needed.
    """
    mock = MagicMock(spec=SheetsClient)

    c1613a_txns = [t for t in sample_transactions if t["property_code"] == "C1613A"]
    may_txns = [t for t in sample_transactions if t["month_tag"] == "2025-05"]
    active_tenants = [t for t in sample_tenants if t["status"] == "active"]

    mock.get_all_tenants.return_value = sample_tenants
    mock.get_active_tenants.return_value = active_tenants
    mock.get_all_transactions.return_value = sample_transactions
    mock.get_transactions_by_property.return_value = c1613a_txns
    mock.get_transactions_by_month.return_value = may_txns
    mock.has_rental_entry.return_value = False
    mock.delete_last_row.return_value = sample_transactions[0].copy()
    mock.append_transaction.return_value = None
    return mock


@pytest.fixture
def agent_deps(mock_sheets):
    return AgentDeps(sheets=mock_sheets, current_date="2025-05-01")


@pytest.fixture
def mock_ctx(agent_deps):
    """Minimal RunContext stand-in — exposes only .deps."""
    ctx = MagicMock()
    ctx.deps = agent_deps
    return ctx
