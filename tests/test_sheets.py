"""
Unit tests for SheetsClient — all Google API calls are mocked at the boundary.

Strategy: patch `Credentials` and `gspread.authorize` so SheetsClient.__init__
never touches the network. After construction, swap `client._spreadsheet` with
a controllable MagicMock for each test.

Run:  pytest -m unit tests/test_sheets.py

COVERAGE MAP
============
SheetsClient method            Scenarios
──────────────────────────────────────────────────────────────────────────────
has_rental_entry               match found
                               no match (different room)
                               case-insensitive room_id / month_tag
                               WorksheetNotFound → False
get_active_tenants             returns only status='active' rows
                               filters by property_code when given
                               any exception → empty list
get_transactions_by_month      no property filter (all properties)
                               with property_code filter
                               unknown month → empty list
append_transaction             values written in _TRANSACTION_COLUMNS order
                               auto-creates tab + writes headers when missing
delete_last_row                returns dict of deleted row
                               header-only sheet → None
                               WorksheetNotFound → None
get_all_transactions           fans out across all PROPERTIES keys
                               silently skips tabs that don't exist
──────────────────────────────────────────────────────────────────────────────
Not covered here (tested via tool tests):
  get_all_tenants              thin wrapper over tenants_sheet.get_all_records()
  get_transactions_by_property thin wrapper over worksheet.get_all_records()
  test_connection              thin wrapper, no logic to test
"""

import pytest
import gspread
from unittest.mock import MagicMock, patch, call

from src.sheets.client import SheetsClient


# ---------------------------------------------------------------------------
# Shared fixture — a SheetsClient whose _spreadsheet is a fresh MagicMock
# ---------------------------------------------------------------------------

@pytest.fixture
def client_and_sheet():
    """Returns (SheetsClient, mock_spreadsheet).

    Both Credentials and gspread.authorize are patched so construction succeeds
    without any real credentials.  _spreadsheet is the MagicMock the tests
    configure per-scenario.
    """
    mock_spreadsheet = MagicMock()
    with (
        patch("src.sheets.client.Credentials"),
        patch("src.sheets.client.gspread.authorize") as mock_auth,
    ):
        mock_auth.return_value.open_by_key.return_value = mock_spreadsheet
        client = SheetsClient("dummy/path.json", "dummy-sheet-id")
    # Patches are removed but client._spreadsheet already points to mock_spreadsheet
    return client, mock_spreadsheet


# ---------------------------------------------------------------------------
# has_rental_entry
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_has_rental_entry_returns_true_when_matching_row_exists(client_and_sheet):
    client, mock_spreadsheet = client_and_sheet
    ws = MagicMock()
    ws.get_all_records.return_value = [
        {
            "category": "rental",
            "type": "income",
            "room_id": "R1",
            "month_tag": "2025-05",
        }
    ]
    mock_spreadsheet.worksheet.return_value = ws

    assert client.has_rental_entry("C1613A", "R1", "2025-05") is True


@pytest.mark.unit
def test_has_rental_entry_returns_false_when_no_match(client_and_sheet):
    client, mock_spreadsheet = client_and_sheet
    ws = MagicMock()
    # Different room_id — should NOT match
    ws.get_all_records.return_value = [
        {
            "category": "rental",
            "type": "income",
            "room_id": "R2",
            "month_tag": "2025-05",
        }
    ]
    mock_spreadsheet.worksheet.return_value = ws

    assert client.has_rental_entry("C1613A", "R1", "2025-05") is False


@pytest.mark.unit
def test_has_rental_entry_case_insensitive_room_and_month(client_and_sheet):
    """room_id and month_tag comparisons must be case-insensitive."""
    client, mock_spreadsheet = client_and_sheet
    ws = MagicMock()
    ws.get_all_records.return_value = [
        {
            "category": "rental",
            "type": "income",
            "room_id": "r1",          # lowercase in sheet
            "month_tag": "2025-05",
        }
    ]
    mock_spreadsheet.worksheet.return_value = ws

    assert client.has_rental_entry("C1613A", "R1", "2025-05") is True


@pytest.mark.unit
def test_has_rental_entry_returns_false_when_sheet_not_found(client_and_sheet):
    client, mock_spreadsheet = client_and_sheet
    mock_spreadsheet.worksheet.side_effect = gspread.WorksheetNotFound

    assert client.has_rental_entry("C1613A", "R1", "2025-05") is False


# ---------------------------------------------------------------------------
# get_active_tenants
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_get_active_tenants_returns_only_active(client_and_sheet, sample_tenants):
    client, mock_spreadsheet = client_and_sheet
    tenants_ws = MagicMock()
    tenants_ws.get_all_records.return_value = sample_tenants
    mock_spreadsheet.worksheet.return_value = tenants_ws

    result = client.get_active_tenants()

    assert all(t["status"] == "active" for t in result)
    assert not any(t["tenant_name"] == "Dave" for t in result)  # inactive


@pytest.mark.unit
def test_get_active_tenants_filters_by_property(client_and_sheet, sample_tenants):
    client, mock_spreadsheet = client_and_sheet
    tenants_ws = MagicMock()
    tenants_ws.get_all_records.return_value = sample_tenants
    mock_spreadsheet.worksheet.return_value = tenants_ws

    result = client.get_active_tenants(property_code="SA1903A")

    assert all(t["property_code"] == "SA1903A" for t in result)
    assert len(result) == 1  # only Charlie


@pytest.mark.unit
def test_get_active_tenants_returns_empty_list_on_exception(client_and_sheet):
    client, mock_spreadsheet = client_and_sheet
    mock_spreadsheet.worksheet.side_effect = Exception("network error")

    result = client.get_active_tenants()

    assert result == []


# ---------------------------------------------------------------------------
# get_transactions_by_month
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_get_transactions_by_month_no_property_filter(client_and_sheet, sample_transactions):
    """Returns all transactions matching month_tag across all properties."""
    client, mock_spreadsheet = client_and_sheet

    from src.config import PROPERTIES

    def ws_side_effect(tab_name):
        ws = MagicMock()
        ws.get_all_records.return_value = [
            t for t in sample_transactions if t["property_code"] == tab_name
        ]
        return ws

    mock_spreadsheet.worksheet.side_effect = ws_side_effect

    result = client.get_transactions_by_month("2025-05")

    assert all(t["month_tag"] == "2025-05" for t in result)
    assert len(result) == 3  # 2 C1613A + 1 SA1903A in May


@pytest.mark.unit
def test_get_transactions_by_month_with_property_filter(client_and_sheet, sample_transactions):
    """When property_code is given, only that property's transactions are returned."""
    client, mock_spreadsheet = client_and_sheet
    ws = MagicMock()
    ws.get_all_records.return_value = [
        t for t in sample_transactions if t["property_code"] == "C1613A"
    ]
    mock_spreadsheet.worksheet.return_value = ws

    result = client.get_transactions_by_month("2025-05", property_code="C1613A")

    assert all(t["property_code"] == "C1613A" for t in result)
    assert all(t["month_tag"] == "2025-05" for t in result)


@pytest.mark.unit
def test_get_transactions_by_month_returns_empty_for_unknown_month(
    client_and_sheet, sample_transactions
):
    client, mock_spreadsheet = client_and_sheet
    ws = MagicMock()
    ws.get_all_records.return_value = sample_transactions
    mock_spreadsheet.worksheet.return_value = ws

    result = client.get_transactions_by_month("2099-01", property_code="C1613A")

    assert result == []


# ---------------------------------------------------------------------------
# append_transaction
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_append_transaction_uses_correct_column_order(client_and_sheet):
    """Values written to the sheet must follow _TRANSACTION_COLUMNS order."""
    client, mock_spreadsheet = client_and_sheet
    ws = MagicMock()
    ws.row_values.return_value = SheetsClient._TRANSACTION_COLUMNS  # existing headers
    mock_spreadsheet.worksheet.return_value = ws

    row = {
        "date": "2025-05-01",
        "month_tag": "2025-05",
        "property_code": "C1613A",
        "room_id": "R1",
        "category": "rental",
        "description": "R1 Rental",
        "amount": "800",
        "type": "income",
    }
    client.append_transaction(row)

    ws.append_row.assert_called_once()
    written_values = ws.append_row.call_args[0][0]
    # Values must match column order
    expected = [str(row.get(col, "")) for col in SheetsClient._TRANSACTION_COLUMNS]
    assert written_values == expected


@pytest.mark.unit
def test_append_transaction_auto_creates_tab_when_missing(client_and_sheet):
    """When the property tab does not exist, add_worksheet is called and headers are written."""
    client, mock_spreadsheet = client_and_sheet
    mock_spreadsheet.worksheet.side_effect = gspread.WorksheetNotFound

    new_ws = MagicMock()
    new_ws.row_values.return_value = SheetsClient._TRANSACTION_COLUMNS
    mock_spreadsheet.add_worksheet.return_value = new_ws

    row = {
        "date": "2025-05-01",
        "month_tag": "2025-05",
        "property_code": "NEWPROP",
        "room_id": "",
        "category": "rental",
        "description": "Rental",
        "amount": "1000",
        "type": "income",
    }
    client.append_transaction(row)

    mock_spreadsheet.add_worksheet.assert_called_once()
    # Header row must have been written during tab creation
    new_ws.append_row.assert_any_call(
        SheetsClient._TRANSACTION_COLUMNS, value_input_option="USER_ENTERED"
    )


# ---------------------------------------------------------------------------
# delete_last_row
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_delete_last_row_returns_dict_of_deleted_row(client_and_sheet):
    client, mock_spreadsheet = client_and_sheet
    ws = MagicMock()
    header = SheetsClient._TRANSACTION_COLUMNS
    data_row = ["2025-05-01", "2025-05", "C1613A", "R1", "rental", "R1 Rental", "800", "income"]
    ws.get_all_values.return_value = [header, data_row]
    mock_spreadsheet.worksheet.return_value = ws

    result = client.delete_last_row("C1613A")

    assert isinstance(result, dict)
    assert result["category"] == "rental"
    assert result["amount"] == "800"
    ws.delete_rows.assert_called_once_with(2)  # 1-based: row 2 is the only data row


@pytest.mark.unit
def test_delete_last_row_returns_none_when_sheet_is_header_only(client_and_sheet):
    client, mock_spreadsheet = client_and_sheet
    ws = MagicMock()
    ws.get_all_values.return_value = [SheetsClient._TRANSACTION_COLUMNS]  # header only
    mock_spreadsheet.worksheet.return_value = ws

    result = client.delete_last_row("C1613A")

    assert result is None
    ws.delete_rows.assert_not_called()


@pytest.mark.unit
def test_delete_last_row_returns_none_when_tab_not_found(client_and_sheet):
    client, mock_spreadsheet = client_and_sheet
    mock_spreadsheet.worksheet.side_effect = gspread.WorksheetNotFound

    result = client.delete_last_row("NOPROP")

    assert result is None


# ---------------------------------------------------------------------------
# get_all_transactions
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_get_all_transactions_fans_out_across_properties(client_and_sheet, sample_transactions):
    """Fan-out reads each property tab and merges results into one list."""
    client, mock_spreadsheet = client_and_sheet

    def ws_side_effect(tab_name):
        ws = MagicMock()
        ws.get_all_records.return_value = [
            t for t in sample_transactions if t["property_code"] == tab_name
        ]
        return ws

    mock_spreadsheet.worksheet.side_effect = ws_side_effect

    result = client.get_all_transactions()

    # All transactions should be merged regardless of which property tab they came from
    assert len(result) == len(sample_transactions)


@pytest.mark.unit
def test_get_all_transactions_skips_missing_tabs(client_and_sheet, sample_transactions):
    """WorksheetNotFound for a property tab should be silently skipped."""
    client, mock_spreadsheet = client_and_sheet

    def ws_side_effect(tab_name):
        if tab_name == "C1613A":
            ws = MagicMock()
            ws.get_all_records.return_value = [
                t for t in sample_transactions if t["property_code"] == "C1613A"
            ]
            return ws
        raise gspread.WorksheetNotFound

    mock_spreadsheet.worksheet.side_effect = ws_side_effect

    result = client.get_all_transactions()

    assert all(t["property_code"] == "C1613A" for t in result)
