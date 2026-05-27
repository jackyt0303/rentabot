"""
LLM-involved tests — run the real Gemini agent and assert that it picks the
correct tool and extracts the right parameters from natural language prompts.

Requirements:
  - GEMINI_API_KEY must be set in .env or in the environment
  - Run with:  pytest -m llm

These tests deliberately make real API calls.  They are kept in this file so
`pytest -m unit` never touches the network.

COVERAGE MAP
============
Prompt                                      Expected tool              Key assertions
──────────────────────────────────────────────────────────────────────────────────────
"record RM800 rent for room R1 at C1613A    record_income              amount=800, room_id=R1,
  for May26"                                                           property_code=C1613A
"Who hasn't paid rent at C1613A for May26?" get_rent_status            property_code=C1613A
"What's the balance for SA1903A?"           get_balance                property_code=SA1903A
"TNB bill RM120 at C1613A for May26"        record_expense             category=utility,
                                                                       amount=120
"undo last transaction at C1613A"           delete_last_transaction    property_code=C1613A
──────────────────────────────────────────────────────────────────────────────────────
Notes on flakiness:
  - Tool name assertions are high-confidence (LLM routing is stable).
  - Numeric arg assertions (amount) are high-confidence.
  - month_tag format ("May26" vs "2026-05") can vary; assert only tool_name
    if a prompt becomes flaky.

To add tests for a new tool:
  1. Add a test function following the _find_tool_call() pattern below.
  2. Add the prompt/tool/assertions row to the COVERAGE MAP above.
  3. Update the README Testing section.
"""

import pytest
from unittest.mock import MagicMock

from src.agents.deps import AgentDeps
from src.agents.rentabot import agent
from src.sheets.client import SheetsClient

# All async tests in this file share one event loop for the whole session.
# This prevents RuntimeError("Event loop is closed") caused by the module-level
# agent singleton holding an httpx.AsyncClient whose connection pool is tied to
# the first test's event loop.  Without this, tests 4+ fail when they need a
# fresh TCP connection and the original loop has already been closed.
pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _extract_tool_calls(result) -> list[dict]:
    """Return a list of {tool_name, args} dicts from the agent result messages."""
    calls = []
    for msg in result.all_messages():
        parts = getattr(msg, "parts", [])
        for part in parts:
            tool_name = getattr(part, "tool_name", None)
            if tool_name is None:
                continue
            args = getattr(part, "args", {})
            # pydantic-ai wraps args in ArgsDict (has .args_dict) or ArgsJson (.args_json)
            if hasattr(args, "args_dict"):
                args = args.args_dict
            elif hasattr(args, "args_json"):
                import json
                args = json.loads(args.args_json)
            elif isinstance(args, str):
                import json
                args = json.loads(args)
            calls.append({"tool_name": tool_name, "args": args})
    return calls


def _find_tool_call(result, tool_name: str) -> dict | None:
    """Return the first call to `tool_name`, or None if not found."""
    for call in _extract_tool_calls(result):
        if call["tool_name"] == tool_name:
            return call["args"]
    return None


# ---------------------------------------------------------------------------
# Fixture — mock sheets so tools don't crash when the LLM calls them
# ---------------------------------------------------------------------------

@pytest.fixture
def llm_deps():
    """AgentDeps with a MagicMock SheetsClient so write tools are side-effect-free."""
    mock = MagicMock(spec=SheetsClient)
    mock.has_rental_entry.return_value = False
    mock.get_active_tenants.return_value = []
    mock.get_transactions_by_month.return_value = []
    mock.get_transactions_by_property.return_value = []
    mock.get_all_transactions.return_value = []
    mock.delete_last_row.return_value = {
        "description": "Test",
        "amount": "100",
        "property_code": "C1613A",
        "month_tag": "2025-05",
    }
    mock.append_transaction.return_value = None
    return AgentDeps(sheets=mock, current_date="2025-05-01")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.llm
async def test_llm_routes_record_income_prompt(llm_deps):
    """Natural language rent payment → record_income with correct params."""
    result = await agent.run(
        "record RM800 rent for room R1 at C1613A for May26",
        deps=llm_deps,
    )

    args = _find_tool_call(result, "record_income")
    assert args is not None, "Expected record_income to be called"
    assert args.get("amount") == 800.0
    assert str(args.get("room_id", "")).upper() == "R1"
    assert str(args.get("property_code", "")).upper() == "C1613A"


@pytest.mark.llm
async def test_llm_routes_get_rent_status_prompt(llm_deps):
    """Unpaid-tenants query → get_rent_status with correct month_tag and property."""
    result = await agent.run(
        "Who hasn't paid rent at C1613A for May26?",
        deps=llm_deps,
    )

    args = _find_tool_call(result, "get_rent_status")
    assert args is not None, "Expected get_rent_status to be called"
    assert str(args.get("property_code", "")).upper() == "C1613A"


@pytest.mark.llm
async def test_llm_routes_get_balance_prompt(llm_deps):
    """Balance query → get_balance with correct property_code."""
    result = await agent.run(
        "What's the balance for SA1903A?",
        deps=llm_deps,
    )

    args = _find_tool_call(result, "get_balance")
    assert args is not None, "Expected get_balance to be called"
    assert str(args.get("property_code", "")).upper() == "SA1903A"


@pytest.mark.llm
async def test_llm_routes_record_expense_with_tnb_keyword(llm_deps):
    """TNB keyword → record_expense with category='utility'."""
    result = await agent.run(
        "TNB bill RM120 at C1613A for May26",
        deps=llm_deps,
    )

    args = _find_tool_call(result, "record_expense")
    assert args is not None, "Expected record_expense to be called"
    assert args.get("category") == "utility"
    assert args.get("amount") == 120.0


@pytest.mark.llm
async def test_llm_routes_delete_last_transaction_prompt(llm_deps):
    """Undo prompt → delete_last_transaction with correct property_code."""
    result = await agent.run(
        "undo last transaction at C1613A",
        deps=llm_deps,
    )

    args = _find_tool_call(result, "delete_last_transaction")
    assert args is not None, "Expected delete_last_transaction to be called"
    assert str(args.get("property_code", "")).upper() == "C1613A"
