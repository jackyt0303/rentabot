"""
Unit tests for the 6 agent tool functions in rentabot.py.

Strategy: import the raw async functions directly (pydantic-ai's @agent.tool
returns the original function unchanged). Call each with a MagicMock RunContext
whose .deps is an AgentDeps backed by mock_sheets. No LLM involved.

Run:  pytest -m unit tests/test_tools.py

COVERAGE MAP
============
Tool                      Scenarios
──────────────────────────────────────────────────────────────────────────────
record_income             happy path — appends row, returns success message
                          duplicate + force=False — no write, ⚠️ warning
                          duplicate + force=True  — writes despite duplicate
                          lowercase inputs normalised to uppercase
                          bare digit room_id ("2") → prefixed ("R2")
record_expense            happy path — type="expense", correct category/prop
get_rent_status           all tenants paid — Paid section only
                          mixed paid/unpaid — both sections present
                          property_code forwarded to sheet calls
                          no active tenants — graceful message
get_balance               single property — net = income − expenses
                          single property + month_tag — uses get_transactions_by_month
                          all properties (no prop) — per-property breakdown + TOTAL
                          empty transaction list — no crash, TOTAL shows 0
get_monthly_report        single property — Income/Expenses/Net sections
                          all paid — "All tenants paid" confirmation
                          empty data — no crash
delete_last_transaction   delete_last_row returns dict — success with description
                          delete_last_row returns None — "Nothing to delete"
                          property_code uppercased before forwarding
──────────────────────────────────────────────────────────────────────────────
To add tests for a new tool:
  1. Add the tool to the import list at the top of this file.
  2. Add a section below following the same async/mock_ctx pattern.
  3. Add the tool to the COVERAGE MAP above.
  4. Add an LLM routing test in test_llm.py.
  5. Update the README Testing section.
"""

import pytest
from unittest.mock import MagicMock

from src.agents.rentabot import (
    delete_last_transaction,
    get_balance,
    get_monthly_report,
    get_rent_status,
    record_expense,
    record_income,
)


# ===========================================================================
# record_income
# ===========================================================================

@pytest.mark.unit
async def test_record_income_happy_path(mock_ctx):
    """New entry: appends row and returns success message."""
    mock_ctx.deps.sheets.has_rental_entry.return_value = False

    result = await record_income(
        mock_ctx,
        property_code="C1613A",
        room_id="R1",
        amount=800.0,
        month_tag="2025-05",
    )

    assert "Recorded" in result
    assert "800.00" in result
    mock_ctx.deps.sheets.append_transaction.assert_called_once()


@pytest.mark.unit
async def test_record_income_duplicate_guard_blocks_write(mock_ctx):
    """Duplicate entry with force=False: must NOT write and must return ⚠️ warning."""
    mock_ctx.deps.sheets.has_rental_entry.return_value = True

    result = await record_income(
        mock_ctx,
        property_code="C1613A",
        room_id="R1",
        amount=800.0,
        month_tag="2025-05",
        force=False,
    )

    assert result.startswith("⚠️")
    mock_ctx.deps.sheets.append_transaction.assert_not_called()


@pytest.mark.unit
async def test_record_income_force_true_overrides_duplicate_guard(mock_ctx):
    """Duplicate entry with force=True: must still write the row."""
    mock_ctx.deps.sheets.has_rental_entry.return_value = True

    result = await record_income(
        mock_ctx,
        property_code="C1613A",
        room_id="R1",
        amount=800.0,
        month_tag="2025-05",
        force=True,
    )

    assert "Recorded" in result
    mock_ctx.deps.sheets.append_transaction.assert_called_once()


@pytest.mark.unit
async def test_record_income_normalises_property_and_room_to_uppercase(mock_ctx):
    """Lowercase inputs must be uppercased before checking duplicates and writing."""
    mock_ctx.deps.sheets.has_rental_entry.return_value = False

    await record_income(
        mock_ctx,
        property_code="c1613a",
        room_id="r1",
        amount=800.0,
        month_tag="2025-05",
    )

    # has_rental_entry must receive uppercased values
    call_args = mock_ctx.deps.sheets.has_rental_entry.call_args
    assert call_args[0][0] == "C1613A"
    assert call_args[0][1] == "R1"

    # append_transaction row must also use uppercase
    written_row = mock_ctx.deps.sheets.append_transaction.call_args[0][0]
    assert written_row["property_code"] == "C1613A"
    assert written_row["room_id"] == "R1"


@pytest.mark.unit
async def test_record_income_room_prefix_canonicalisation(mock_ctx):
    """'2' should be expanded to 'R2' when R2 is a known room for the property."""
    mock_ctx.deps.sheets.has_rental_entry.return_value = False

    await record_income(
        mock_ctx,
        property_code="C1613A",
        room_id="2",         # user/LLM passed bare digit
        amount=700.0,
        month_tag="2025-05",
    )

    written_row = mock_ctx.deps.sheets.append_transaction.call_args[0][0]
    assert written_row["room_id"] == "R2"


# ===========================================================================
# record_expense
# ===========================================================================

@pytest.mark.unit
async def test_record_expense_happy_path(mock_ctx):
    """Appends an expense row with type='expense' and returns success message."""
    result = await record_expense(
        mock_ctx,
        property_code="C1613A",
        amount=120.0,
        category="utility",
        description="TNB electricity",
        month_tag="2025-05",
    )

    assert "Recorded" in result
    assert "120.00" in result
    assert "utility" in result

    written_row = mock_ctx.deps.sheets.append_transaction.call_args[0][0]
    assert written_row["type"] == "expense"
    assert written_row["category"] == "utility"
    assert written_row["property_code"] == "C1613A"


# ===========================================================================
# get_rent_status
# ===========================================================================

@pytest.mark.unit
async def test_get_rent_status_all_paid(mock_ctx):
    """When every active tenant has a matching income row, output shows Paid section only."""
    mock_ctx.deps.sheets.get_active_tenants.return_value = [
        {"tenant_name": "Alice", "property_code": "C1613A", "room_id": "R1", "monthly_rent": "800"},
        {"tenant_name": "Bob", "property_code": "C1613A", "room_id": "R2", "monthly_rent": "700"},
    ]
    mock_ctx.deps.sheets.get_transactions_by_month.return_value = [
        {"property_code": "C1613A", "room_id": "R1", "category": "rental", "type": "income"},
        {"property_code": "C1613A", "room_id": "R2", "category": "rental", "type": "income"},
    ]

    result = await get_rent_status(mock_ctx, month_tag="2025-05", property_code="C1613A")

    assert "Paid:" in result
    assert "Unpaid:" not in result


@pytest.mark.unit
async def test_get_rent_status_mixed_paid_unpaid(mock_ctx):
    """Output must contain both Paid and Unpaid sections when statuses differ."""
    mock_ctx.deps.sheets.get_active_tenants.return_value = [
        {"tenant_name": "Alice", "property_code": "C1613A", "room_id": "R1", "monthly_rent": "800"},
        {"tenant_name": "Bob", "property_code": "C1613A", "room_id": "R2", "monthly_rent": "700"},
    ]
    # Only R1 has paid
    mock_ctx.deps.sheets.get_transactions_by_month.return_value = [
        {"property_code": "C1613A", "room_id": "R1", "category": "rental", "type": "income"},
    ]

    result = await get_rent_status(mock_ctx, month_tag="2025-05", property_code="C1613A")

    assert "Paid:" in result
    assert "Unpaid:" in result
    assert "Alice" in result  # paid
    assert "Bob" in result    # unpaid


@pytest.mark.unit
async def test_get_rent_status_single_property_filter_passed_to_sheets(mock_ctx):
    """property_code is forwarded to both get_active_tenants and get_transactions_by_month."""
    mock_ctx.deps.sheets.get_active_tenants.return_value = []
    mock_ctx.deps.sheets.get_transactions_by_month.return_value = []

    await get_rent_status(mock_ctx, month_tag="2025-05", property_code="SA1903A")

    mock_ctx.deps.sheets.get_active_tenants.assert_called_once_with("SA1903A")
    mock_ctx.deps.sheets.get_transactions_by_month.assert_called_once_with("2025-05", "SA1903A")


@pytest.mark.unit
async def test_get_rent_status_no_active_tenants_returns_graceful_message(mock_ctx):
    """Empty tenant list should return a helpful message, not crash."""
    mock_ctx.deps.sheets.get_active_tenants.return_value = []
    mock_ctx.deps.sheets.get_transactions_by_month.return_value = []

    result = await get_rent_status(mock_ctx, month_tag="2025-05")

    assert "No active tenants" in result


# ===========================================================================
# get_balance
# ===========================================================================

@pytest.mark.unit
async def test_get_balance_single_property_calculates_net(mock_ctx):
    """Single property: net = income - expenses."""
    mock_ctx.deps.sheets.get_transactions_by_property.return_value = [
        {"type": "income", "amount": "800"},
        {"type": "expense", "amount": "120"},
    ]

    result = await get_balance(mock_ctx, property_code="C1613A")

    assert "C1613A" in result
    assert "800.00" in result
    assert "120.00" in result
    # Net = 680
    assert "680.00" in result


@pytest.mark.unit
async def test_get_balance_single_property_with_month_tag(mock_ctx):
    """When month_tag is provided for a single property, uses get_transactions_by_month."""
    mock_ctx.deps.sheets.get_transactions_by_month.return_value = [
        {"type": "income", "amount": "800"},
    ]

    result = await get_balance(mock_ctx, property_code="C1613A", month_tag="2025-05")

    mock_ctx.deps.sheets.get_transactions_by_month.assert_called_once_with("2025-05", "C1613A")
    assert "800.00" in result


@pytest.mark.unit
async def test_get_balance_all_properties_shows_per_property_breakdown(mock_ctx):
    """No property_code: must show per-property lines and grand totals."""
    mock_ctx.deps.sheets.get_all_transactions.return_value = [
        {"property_code": "C1613A", "type": "income", "amount": "800", "month_tag": "2025-05"},
        {"property_code": "C1613A", "type": "expense", "amount": "120", "month_tag": "2025-05"},
        {"property_code": "SA1903A", "type": "income", "amount": "900", "month_tag": "2025-05"},
    ]

    result = await get_balance(mock_ctx)

    assert "C1613A" in result
    assert "SA1903A" in result
    assert "TOTAL" in result


@pytest.mark.unit
async def test_get_balance_empty_transactions_returns_zero_net(mock_ctx):
    """No transactions: must not crash and should reflect 0 totals."""
    mock_ctx.deps.sheets.get_all_transactions.return_value = []

    result = await get_balance(mock_ctx)

    # With no data the per-property loop skips everything; only the footer remains
    assert "TOTAL" in result
    assert "0.00" in result


# ===========================================================================
# get_monthly_report
# ===========================================================================

@pytest.mark.unit
async def test_get_monthly_report_single_property_shows_income_and_expenses(mock_ctx):
    mock_ctx.deps.sheets.get_transactions_by_month.return_value = [
        {
            "type": "income",
            "amount": "800",
            "description": "R1 Rental",
            "category": "rental",
            "property_code": "C1613A",
            "room_id": "R1",
        },
        {
            "type": "expense",
            "amount": "120",
            "description": "TNB",
            "category": "utility",
            "property_code": "C1613A",
            "room_id": "",
        },
    ]
    mock_ctx.deps.sheets.get_active_tenants.return_value = []

    result = await get_monthly_report(mock_ctx, month_tag="2025-05", property_code="C1613A")

    assert "Income:" in result
    assert "Expenses:" in result
    assert "800.00" in result
    assert "120.00" in result
    assert "Net:" in result


@pytest.mark.unit
async def test_get_monthly_report_all_tenants_paid_shows_confirmation(mock_ctx):
    mock_ctx.deps.sheets.get_transactions_by_month.return_value = [
        {
            "type": "income",
            "amount": "800",
            "description": "R1 Rental",
            "category": "rental",
            "property_code": "C1613A",
            "room_id": "R1",
        },
    ]
    mock_ctx.deps.sheets.get_active_tenants.return_value = [
        {
            "tenant_name": "Alice",
            "property_code": "C1613A",
            "room_id": "R1",
            "monthly_rent": "800",
        }
    ]

    result = await get_monthly_report(mock_ctx, month_tag="2025-05", property_code="C1613A")

    assert "All tenants paid" in result


@pytest.mark.unit
async def test_get_monthly_report_empty_data_does_not_crash(mock_ctx):
    mock_ctx.deps.sheets.get_transactions_by_month.return_value = []
    mock_ctx.deps.sheets.get_active_tenants.return_value = []

    result = await get_monthly_report(mock_ctx, month_tag="2025-05")

    assert "Monthly Report" in result
    assert "0.00" in result


# ===========================================================================
# delete_last_transaction
# ===========================================================================

@pytest.mark.unit
async def test_delete_last_transaction_returns_description_of_deleted_row(mock_ctx):
    mock_ctx.deps.sheets.delete_last_row.return_value = {
        "description": "R1 Rental",
        "amount": "800",
        "property_code": "C1613A",
        "month_tag": "2025-05",
    }

    result = await delete_last_transaction(mock_ctx, property_code="C1613A")

    assert "Deleted" in result
    assert "R1 Rental" in result
    assert "800" in result
    mock_ctx.deps.sheets.delete_last_row.assert_called_once_with("C1613A")


@pytest.mark.unit
async def test_delete_last_transaction_nothing_to_delete_returns_message(mock_ctx):
    mock_ctx.deps.sheets.delete_last_row.return_value = None

    result = await delete_last_transaction(mock_ctx, property_code="C1613A")

    assert "Nothing to delete" in result
    assert "C1613A" in result


@pytest.mark.unit
async def test_delete_last_transaction_normalises_property_code(mock_ctx):
    """property_code is uppercased before forwarding to sheets."""
    mock_ctx.deps.sheets.delete_last_row.return_value = None

    await delete_last_transaction(mock_ctx, property_code="c1613a")

    mock_ctx.deps.sheets.delete_last_row.assert_called_once_with("C1613A")
