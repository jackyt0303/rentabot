"""
Google Sheets CRUD wrapper using gspread.
Handles authentication, reading, and writing to the RentaBot spreadsheet.
"""

import gspread
from google.oauth2.service_account import Credentials

from src.config import (
    SHEET_TAB_TENANTS,
    SHEET_TAB_TRANSACTIONS,
    SHEET_TAB_CONFIG,
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class SheetsClient:
    """Wrapper around gspread for RentaBot spreadsheet operations."""

    def __init__(self, service_account_path: str, spreadsheet_id: str):
        creds = Credentials.from_service_account_file(
            service_account_path, scopes=SCOPES
        )
        self._gc = gspread.authorize(creds)
        self._spreadsheet = self._gc.open_by_key(spreadsheet_id)

    # ------------------------------------------------------------------
    # Tab accessors
    # ------------------------------------------------------------------

    @property
    def tenants_sheet(self) -> gspread.Worksheet:
        return self._spreadsheet.worksheet(SHEET_TAB_TENANTS)

    @property
    def transactions_sheet(self) -> gspread.Worksheet:
        return self._spreadsheet.worksheet(SHEET_TAB_TRANSACTIONS)

    @property
    def config_sheet(self) -> gspread.Worksheet:
        return self._spreadsheet.worksheet(SHEET_TAB_CONFIG)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_all_tenants(self) -> list[dict]:
        """Return all rows from the Tenants tab as a list of dicts."""
        return self.tenants_sheet.get_all_records()

    def get_all_transactions(self) -> list[dict]:
        """Return all rows from the Transactions tab as a list of dicts."""
        return self.transactions_sheet.get_all_records()

    def get_transactions_by_property(self, property_code: str) -> list[dict]:
        """Return transactions filtered by property_code."""
        all_txns = self.get_all_transactions()
        return [t for t in all_txns if t.get("property_code", "").upper() == property_code.upper()]

    def get_transactions_by_month(self, month_tag: str, property_code: str | None = None) -> list[dict]:
        """Return transactions for a specific month_tag, optionally filtered by property."""
        all_txns = self.get_all_transactions()
        filtered = [t for t in all_txns if t.get("month_tag", "").lower() == month_tag.lower()]
        if property_code:
            filtered = [t for t in filtered if t.get("property_code", "").upper() == property_code.upper()]
        return filtered

    def get_active_tenants(self, property_code: str | None = None) -> list[dict]:
        """Return tenants with status='active', optionally filtered by property.

        Returns an empty list if the Tenants worksheet doesn't exist yet.
        """
        try:
            tenants = self.get_all_tenants()
        except Exception:
            return []
        active = [t for t in tenants if t.get("status", "").strip().lower() == "active"]
        if property_code:
            active = [t for t in active if t.get("property_code", "").upper() == property_code.upper()]
        return active

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    _TRANSACTION_COLUMNS = [
        "date", "month_tag", "property_code", "room_id",
        "category", "description", "amount", "type",
    ]

    def has_rental_entry(self, property_code: str, room_id: str, month_tag: str) -> bool:
        """Return True if a rental income row already exists for the given property/room/month."""
        txns = self.get_transactions_by_month(month_tag, property_code)
        return any(
            t.get("category") == "rental"
            and t.get("type") == "income"
            and t.get("room_id", "").upper() == room_id.upper()
            for t in txns
        )

    def delete_last_row(self) -> dict | None:
        """Delete the last data row in the Transactions tab.

        Returns the deleted row as a dict, or None if the sheet has no data rows.
        """
        sheet = self.transactions_sheet
        all_values = sheet.get_all_values()
        if len(all_values) <= 1:  # empty or header only
            return None
        header = all_values[0]
        last_row_values = all_values[-1]
        last_row_index = len(all_values)  # 1-based row number in Sheets
        row_dict = dict(zip(header, last_row_values))
        sheet.delete_rows(last_row_index)
        return row_dict

    def append_transaction(self, row: dict) -> None:
        """Append a single transaction row to the Transactions tab.

        If the tab has no headers yet (newly created empty sheet), writes
        the canonical header row first so subsequent reads work correctly.

        Args:
            row: Dict with keys matching Transaction tab columns:
                 date, month_tag, property_code, room_id, category,
                 description, amount, type
        """
        sheet = self.transactions_sheet
        header = sheet.row_values(1)
        if not header:
            sheet.append_row(self._TRANSACTION_COLUMNS, value_input_option="USER_ENTERED")
            header = self._TRANSACTION_COLUMNS
        values = [str(row.get(col, "")) for col in header]
        sheet.append_row(values, value_input_option="USER_ENTERED")

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    def test_connection(self) -> dict:
        """Verify connectivity and return basic sheet info."""
        title = self._spreadsheet.title
        worksheets = [ws.title for ws in self._spreadsheet.worksheets()]
        return {"title": title, "tabs": worksheets}
