"""
Classifier module for bulk import mode.
Classifies normalized bank transactions using Payees/PersonalPayees registries, LLM inference, WhatsApp signals, and duplicate detection.
"""

import json
import re
from datetime import datetime, timedelta
from typing import Any

from src.config import EXPENSE_CATEGORIES, PROPERTIES
from src.importers import ClassifiedTx, NormalizedTx, WhatsAppSignal
from src.sheets.client import SheetsClient


def _parse_date(date_str: str) -> datetime:
    """Parse ISO date string 'YYYY-MM-DD' into datetime object."""
    return datetime.strptime(date_str.strip(), "%Y-%m-%d")


def _find_wa_matches(tx: NormalizedTx, wa_signals: list[WhatsAppSignal]) -> list[WhatsAppSignal]:
    """Find WhatsApp signals within ±7 days of transaction date with matching amount."""
    tx_dt = _parse_date(tx.date)
    matches = []
    for sig in wa_signals:
        try:
            sig_dt = _parse_date(sig.date)
        except ValueError:
            continue
        if abs((sig_dt - tx_dt).days) <= 7:
            if sig.amount is not None and abs(sig.amount - tx.amount) < 0.01:
                matches.append(sig)
    return matches


def _extract_month_from_narration(narration: str, tx_date_iso: str) -> str:
    """Extract month_tag like 'Jul26', 'Aug26' from narration if explicitly present."""
    # Pattern for Jul26, Aug26, May26, July26, Aug 2026, July'26
    months_map = {
        "jan": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr",
        "may": "May", "jun": "Jun", "jul": "Jul", "aug": "Aug",
        "sep": "Sep", "oct": "Oct", "nov": "Nov", "dec": "Dec",
    }
    pattern = r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*['\s]*(\d{2}|\d{4})?\b"
    match = re.search(pattern, narration, re.IGNORECASE)
    if match:
        m_str = match.group(1).lower()
        yr_str = match.group(2)
        month_prefix = months_map.get(m_str, "Jan")
        if yr_str:
            yr = yr_str[-2:]
        else:
            yr = tx_date_iso[:4][-2:]
        return f"{month_prefix}{yr}"
    return ""


async def classify_batch(
    txns: list[NormalizedTx],
    wa_signals: list[WhatsAppSignal],
    sheets: SheetsClient,
    agent: Any,
) -> list[ClassifiedTx]:
    """Classify a list of normalized transactions into ClassifiedTx objects."""

    # 1. Fetch registries and existing transactions from Sheets
    payees_registry = sheets.get_payees()
    personal_payees = sheets.get_personal_payees()
    existing_txns = sheets.get_all_transactions()

    # Pre-build lowercase lookup maps
    payee_map = {
        str(r.get("payee_name", "")).strip().lower(): r
        for r in payees_registry if r.get("payee_name")
    }
    personal_map = {
        str(r.get("payee_name", "")).strip().lower(): r
        for r in personal_payees if r.get("payee_name")
    }

    classified_list: list[ClassifiedTx] = []

    for tx in txns:
        c = ClassifiedTx(
            date=tx.date,
            payee=tx.payee,
            narration=tx.narration,
            amount=tx.amount,
            direction=tx.direction,
            source=tx.source,
        )

        payee_clean = tx.payee.strip().lower()

        # Step 1: Check PersonalPayees registry
        if payee_clean in personal_map:
            c.tx_type = "personal"
            c.confidence = 100
            c.reasoning = f"Matched PersonalPayees registry ({personal_map[payee_clean].get('description', '')})"
            classified_list.append(c)
            continue

        # Step 2: Check Payees registry
        if payee_clean in payee_map:
            reg = payee_map[payee_clean]
            c.property_code = reg.get("property_code", "").upper()
            c.room_id = reg.get("room_id", "").upper()
            c.tx_type = "income" if tx.direction == "in" else "expense"
            c.category = "rental" if c.tx_type == "income" else (reg.get("notes") or "misc")
            c.confidence = 90
            c.reasoning = f"Matched Payees registry ({reg.get('payee_name')})"

        # Check for explicit property code in narration to override or set
        for prop_code in PROPERTIES:
            if prop_code.lower() in tx.narration.lower():
                c.property_code = prop_code
                break

        # Extract explicit month_tag from narration if present
        month_from_narration = _extract_month_from_narration(tx.narration, tx.date)
        if month_from_narration:
            c.month_tag = month_from_narration

        # Step 3: LLM classification if property_code or category is still missing
        if not c.property_code or not c.category or not c.month_tag:
            wa_matches = _find_wa_matches(tx, wa_signals)
            wa_text = "\n".join([f"[{s.date}] {s.sender}: {s.text}" for s in wa_matches]) if wa_matches else "None"

            prompt = (
                f"Classify this Malaysian property transaction.\n"
                f"Date: {tx.date}\n"
                f"Payee: {tx.payee}\n"
                f"Narration: {tx.narration}\n"
                f"Amount: MYR {tx.amount}\n"
                f"Direction: {'Credit (Income)' if tx.direction == 'in' else 'Debit (Expense)'}\n"
                f"WhatsApp Context:\n{wa_text}\n\n"
                f"Return ONLY a JSON object (no markdown formatting) with these exact keys:\n"
                f'{{"property_code": "...", "room_id": "...", "month_tag": "...", "category": "...", "confidence": 85, "reasoning": "..."}}\n'
                f"Valid property_code options: {list(PROPERTIES.keys())} or empty string if personal/unknown.\n"
                f"Valid expense categories: {sorted(set(EXPENSE_CATEGORIES.values()))}.\n"
                f"month_tag format: 'Jul26', 'Aug26', etc.\n"
            )

            try:
                # Run prompt through agent
                res = await agent.run(prompt)
                raw_out = res.output.strip()
                # Clean code blocks if present
                if raw_out.startswith("```"):
                    raw_out = re.sub(r"^```[a-z]*\n?", "", raw_out)
                    raw_out = re.sub(r"\n?```$", "", raw_out)

                data = json.loads(raw_out.strip())
                if not c.property_code and data.get("property_code"):
                    c.property_code = str(data["property_code"]).upper()
                if not c.room_id and data.get("room_id"):
                    c.room_id = str(data["room_id"]).upper()
                if not c.month_tag and data.get("month_tag"):
                    c.month_tag = str(data["month_tag"])
                if not c.category and data.get("category"):
                    c.category = str(data["category"]).lower()

                c.confidence = int(data.get("confidence", 70))
                c.reasoning = str(data.get("reasoning", "LLM classified"))
            except Exception as e:
                c.reasoning = f"Classification attempt: {e}"

        # Determine tx_type if still unset
        if not c.tx_type:
            if c.property_code:
                c.tx_type = "income" if tx.direction == "in" else "expense"
            else:
                c.tx_type = "unknown"

        # Step 4: Month tag fallback using date default if month_tag still empty
        if not c.month_tag:
            dt = _parse_date(tx.date)
            c.month_tag = dt.strftime("%b%y")  # e.g. Jul26

        # Step 5: Duplicate detection against existing Sheets transactions
        tx_dt = _parse_date(tx.date)
        for ext in existing_txns:
            ext_amt = float(ext.get("amount", 0))
            if abs(ext_amt - tx.amount) < 0.01:
                ext_prop = str(ext.get("property_code", "")).upper()
                if ext_prop == c.property_code:
                    ext_date_str = str(ext.get("date", "")).strip()
                    try:
                        ext_dt = _parse_date(ext_date_str)
                        if abs((ext_dt - tx_dt).days) <= 2:
                            c.tx_type = "duplicate"
                            c.reasoning = f"Duplicate of transaction on {ext_date_str}"
                            break
                    except ValueError:
                        pass

        classified_list.append(c)

    return classified_list
