"""
CIMB Clicks CSV Parser.
Parses CSV exports with header: Date,Transaction Details,Money In,Money Out,Balance
"""

import csv
import io
from datetime import datetime
from src.importers import NormalizedTx


def _parse_cimb_date(date_str: str) -> str:
    """Parse date like '31-Jul-2026' into ISO format '2026-07-31'."""
    date_str = date_str.strip().strip('"')
    dt = datetime.strptime(date_str, "%d-%b-%Y")
    return dt.strftime("%Y-%m-%d")


def _clean_amount(amt_str: str) -> float:
    """Clean amount string like 'MYR 1,100.00' to float 1100.0."""
    amt_str = amt_str.strip().replace("MYR", "").replace(",", "").strip()
    return float(amt_str) if amt_str else 0.0


def _extract_cimb_payee(narration: str) -> str:
    """Extract payee name from pipe-delimited CIMB transaction details."""
    parts = [p.strip() for p in narration.split("|") if p.strip()]
    if not parts:
        return narration.strip()
    # Typical DuitNow structure: ["DUITNOW TO ACCOUNT", "ref_num", "PAYEE NAME", "remark..."]
    # Look for the first part that looks like a person/merchant name (not all digits, not standard prefixes)
    skip_keywords = {"DUITNOW TO ACCOUNT", "I-PAYMENT", "CREDIT INTEREST", "DUITNOW TRANSFER"}
    for part in parts:
        if part not in skip_keywords and not part.isdigit() and len(part) > 2:
            return part
    return parts[0]


def parse(content: bytes) -> list[NormalizedTx]:
    """Parse CIMB CSV bytes into a list of NormalizedTx objects."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))

    txns: list[NormalizedTx] = []
    for row in reader:
        if not row or len(row) < 4:
            continue

        # Skip header
        if row[0].strip().lower() in ("date", "transaction date"):
            continue

        date_raw = row[0].strip()
        try:
            iso_date = _parse_cimb_date(date_raw)
        except ValueError:
            continue  # Not a valid date row

        details = row[1].strip()
        money_in_raw = row[2].strip() if len(row) > 2 else ""
        money_out_raw = row[3].strip() if len(row) > 3 else ""

        if money_in_raw:
            amt = _clean_amount(money_in_raw)
            direction = "in"
        elif money_out_raw:
            amt = _clean_amount(money_out_raw)
            direction = "out"
        else:
            continue  # Row has no amount

        payee = _extract_cimb_payee(details)

        txns.append(
            NormalizedTx(
                date=iso_date,
                payee=payee,
                narration=details,
                amount=amt,
                direction=direction,
                source="cimb",
            )
        )

    return txns
