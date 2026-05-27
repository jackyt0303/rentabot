"""
Google Sheets CRUD wrapper using gspread.
Handles authentication, reading, and writing to the RentaBot spreadsheet.

Each property has its own tab (named by property code, e.g. "C1613A").
The Tenants and Config tabs remain shared.
"""

import gspread
from google.oauth2.service_account import Credentials

from src.config import (
    PROPERTIES,
    SHEET_TAB_TENANTS,
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

    _TRANSACTION_COLUMNS = [
        "date", "month_tag", "property_code", "room_id",
        "category", "description", "amount", "type",
    ]

    @property
    def tenants_sheet(self) -> gspread.Worksheet:
        return self._spreadsheet.worksheet(SHEET_TAB_TENANTS)

    @property
    def config_sheet(self) -> gspread.Worksheet:
        return self._spreadsheet.worksheet(SHEET_TAB_CONFIG)

    def _property_sheet(self, property_code: str) -> gspread.Worksheet:
        """Return the worksheet for a property, auto-creating with headers if missing."""
        tab_name = property_code.upper()
        try:
            return self._spreadsheet.worksheet(tab_name)
        except gspread.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=len(self._TRANSACTION_COLUMNS))
            ws.append_row(self._TRANSACTION_COLUMNS, value_input_option="USER_ENTERED")
            return ws

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_all_tenants(self) -> list[dict]:
        """Return all rows from the Tenants tab as a list of dicts."""
        return self.tenants_sheet.get_all_records()

    def get_all_transactions(self) -> list[dict]:
        """Fan-out read across all property tabs, merging into a single list."""
        all_txns: list[dict] = []
        for prop_code in PROPERTIES:
            try:
                ws = self._spreadsheet.worksheet(prop_code)
            except gspread.WorksheetNotFound:
                continue
            all_txns.extend(ws.get_all_records())
        return all_txns

    def get_transactions_by_property(self, property_code: str) -> list[dict]:
        """Read transactions directly from a single property tab."""
        try:
            ws = self._spreadsheet.worksheet(property_code.upper())
            return ws.get_all_records()
        except gspread.WorksheetNotFound:
            return []

    def get_transactions_by_month(self, month_tag: str, property_code: str | None = None) -> list[dict]:
        """Return transactions for a specific month_tag, optionally filtered by property."""
        if property_code:
            all_txns = self.get_transactions_by_property(property_code)
        else:
            all_txns = self.get_all_transactions()
        return [t for t in all_txns if t.get("month_tag", "").lower() == month_tag.lower()]

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

    def has_rental_entry(self, property_code: str, room_id: str, month_tag: str) -> bool:
        """Return True if a rental income row already exists for the given property/room/month."""
        txns = self.get_transactions_by_property(property_code)
        return any(
            t.get("category") == "rental"
            and t.get("type") == "income"
            and str(t.get("room_id", "")).upper() == room_id.upper()
            and str(t.get("month_tag", "")).lower() == month_tag.lower()
            for t in txns
        )

    def delete_last_row(self, property_code: str) -> dict | None:
        """Delete the last data row in a property's tab.

        Returns the deleted row as a dict, or None if the sheet has no data rows.
        """
        try:
            sheet = self._spreadsheet.worksheet(property_code.upper())
        except gspread.WorksheetNotFound:
            return None
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
        """Append a single transaction row to the property's dedicated tab.

        Auto-creates the tab with headers if it doesn't exist yet.

        Args:
            row: Dict with keys matching Transaction tab columns:
                 date, month_tag, property_code, room_id, category,
                 description, amount, type
        """
        prop = row.get("property_code", "").upper()
        if not prop:
            raise ValueError("property_code is required in transaction row")
        sheet = self._property_sheet(prop)
        header = sheet.row_values(1)
        if not header:
            sheet.append_row(self._TRANSACTION_COLUMNS, value_input_option="USER_ENTERED")
            header = self._TRANSACTION_COLUMNS
        values = [str(row.get(col, "")) for col in header]
        sheet.append_row(values, value_input_option="RAW")

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    def test_connection(self) -> dict:
        """Verify connectivity and return basic sheet info."""
        title = self._spreadsheet.title
        worksheets = [ws.title for ws in self._spreadsheet.worksheets()]
        return {"title": title, "tabs": worksheets}
