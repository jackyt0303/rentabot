"""
MyPB (Public Bank) CSV Parser.
Parses CSV exports with metadata preamble and multi-column wrapped descriptions.
Header: Transaction Date, Cheque No/Ref No ,Description , Debit Amount, Credit Amount
"""

import csv
import io
import re
from datetime import datetime
from src.importers import NormalizedTx


def _parse_mypb_date(date_str: str) -> str:
    """Parse date like '30-07-2026' into ISO format '2026-07-30'."""
    date_str = date_str.strip()
    dt = datetime.strptime(date_str, "%d-%m-%Y")
    return dt.strftime("%Y-%m-%d")


def _clean_amount(amt_str: str) -> float:
    """Clean amount string like '1,100' or '52.52' to float."""
    amt_str = amt_str.strip().replace(",", "")
    return float(amt_str) if amt_str else 0.0


def _extract_mypb_payee(narration: str) -> str:
    """Extract payee/recipient name from MyPB concatenated narration string."""
    # Common transaction type prefixes to strip
    prefixes = [
        "DUITNOW TRSF CR",
        "DUITNOW TRSF DR",
        "TSFR FUND DR-ATM/EFT",
        "TSFR FUND CR-ATM/EFT",
        "CR CARD PYMT-ATM/EFT",
        "AUTOMATED LOAN PYMT",
        "DR-ECP",
        "DUITNOW QR CR",
    ]
    text = narration
    for p in prefixes:
        text = text.replace(p, " ")

    text = re.sub(r"'\d+'", " ", text)  # remove ref numbers like '000064650'
    text = re.sub(r"XXXXXX\d+", " ", text)  # remove masked account numbers like XXXXXX3623
    text = re.sub(r"\bQR\d+\b", " ", text)
    text = re.sub(r"\bTO\s+\d+\b", " ", text)
    text = re.sub(r"\bBRH\s+\d+\b", " ", text)
    text = re.sub(r"\bNOTE\s+\d+\b", " ", text)

    tokens = [t for t in text.split() if len(t) > 1]
    if not tokens:
        return narration[:30].strip()

    # Join early alpha tokens as candidate name (e.g. TEY HUEY YI, TANG ZHE KHEE)
    name_parts = []
    for tok in tokens:
        if tok.isupper() and tok.isalpha():
            name_parts.append(tok)
            if len(name_parts) >= 4:
                break
        elif name_parts:
            break  # stop at first non-name word

    if name_parts:
        return " ".join(name_parts)

    return tokens[0]


def parse(content: bytes) -> list[NormalizedTx]:
    """Parse MyPB CSV bytes into a list of NormalizedTx objects."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))

    txns: list[NormalizedTx] = []
    header_found = False

    for row in reader:
        if not row:
            continue

        # Look for header line containing 'Transaction Date'
        first_cell = row[0].strip().lower()
        if not header_found:
            if "transaction date" in first_cell:
                header_found = True
            continue

        if not first_cell:
            continue

        try:
            iso_date = _parse_mypb_date(first_cell)
        except ValueError:
            continue  # Not a date row

        # Columns: 0=Date, 1=Ref, 2=Desc, 3=Debit, 4=Credit, 5+=overflow desc
        debit_raw = row[3].strip() if len(row) > 3 else ""
        credit_raw = row[4].strip() if len(row) > 4 else ""

        if credit_raw:
            amt = _clean_amount(credit_raw)
            direction = "in"
        elif debit_raw:
            amt = _clean_amount(debit_raw)
            direction = "out"
        else:
            continue  # No amount

        # Build full narration by concatenating all text columns (excluding debit/credit cols if index 3,4)
        desc_parts = []
        if len(row) > 2 and row[2].strip():
            desc_parts.append(row[2].strip())
        if len(row) > 5:
            desc_parts.extend([cell.strip() for cell in row[5:] if cell.strip()])

        narration = " ".join(desc_parts).strip()
        payee = _extract_mypb_payee(narration)

        txns.append(
            NormalizedTx(
                date=iso_date,
                payee=payee,
                narration=narration,
                amount=amt,
                direction=direction,
                source="mypb",
            )
        )

    return txns
