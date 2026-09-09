"""
Unit tests for RentaBot importers: CIMB parser, MyPB parser, WhatsApp parser.
"""

import pytest
from pathlib import Path
from src.importers.cimb_parser import parse as parse_cimb
from src.importers.mypb_parser import parse as parse_mypb
from src.importers.whatsapp_parser import parse as parse_wa

FIXTURES_DIR = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.unit


def test_cimb_parser():
    cimb_file = FIXTURES_DIR / "CIMB Clicks Transaction History.csv"
    assert cimb_file.exists(), "CIMB sample fixture missing"
    content = cimb_file.read_bytes()
    txns = parse_cimb(content)

    assert len(txns) > 0
    # Check income row
    rent_tx = next((t for t in txns if "TANG ZHE KHEE" in t.narration), None)
    assert rent_tx is not None
    assert rent_tx.amount == 770.0
    assert rent_tx.direction == "in"
    assert rent_tx.date == "2026-07-25"
    assert rent_tx.source == "cimb"

    # Check expense row
    expense_tx = next((t for t in txns if t.direction == "out"), None)
    assert expense_tx is not None
    assert expense_tx.amount == 132.10
    assert expense_tx.date == "2026-07-13"


def test_mypb_parser():
    mypb_file = FIXTURES_DIR / "MyPB Statement.csv"
    assert mypb_file.exists(), "MyPB sample fixture missing"
    content = mypb_file.read_bytes()
    txns = parse_mypb(content)

    assert len(txns) > 0
    # Check credit row
    credit_tx = next((t for t in txns if t.amount == 1590.0), None)
    assert credit_tx is not None
    assert credit_tx.direction == "in"
    assert credit_tx.date == "2026-07-25"
    assert credit_tx.source == "mypb"

    # Check debit row
    debit_tx = next((t for t in txns if t.amount == 52.52), None)
    assert debit_tx is not None
    assert debit_tx.direction == "out"
    assert debit_tx.date == "2026-07-16"


def test_whatsapp_parser():
    wa_file = FIXTURES_DIR / "_chat(whatsapp_group_sa1903a_export).txt"
    assert wa_file.exists(), "WhatsApp sample fixture missing"
    content = wa_file.read_bytes()
    signals = parse_wa(content)

    assert len(signals) > 0
    # Check signal with RM amount
    rm_signal = next((s for s in signals if s.amount == 750.0), None)
    assert rm_signal is not None
    assert rm_signal.date is not None


@pytest.mark.asyncio
async def test_classifier(mocker):
    from unittest.mock import AsyncMock, MagicMock
    from src.importers import NormalizedTx
    from src.importers.classifier import classify_batch

    mock_sheets = MagicMock()
    mock_sheets.get_payees.return_value = [
        {"payee_name": "TANG ZHE KHEE", "property_code": "C1811", "room_id": "R3", "type": "tenant"}
    ]
    mock_sheets.get_personal_payees.return_value = [
        {"payee_name": "LEMBAGA HASIL DALAM NEGERI MAL", "description": "Tax"}
    ]
    mock_sheets.get_all_transactions.return_value = []

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value=MagicMock(
        output='{"property_code": "SA1903A", "room_id": "R1", "month_tag": "Jul26", "category": "utility", "confidence": 85, "reasoning": "Unifi bill"}'
    ))

    txns = [
        NormalizedTx(date="2026-07-25", payee="TANG ZHE KHEE", narration="C1811 R3 Aug26 Rental", amount=770.0, direction="in", source="cimb"),
        NormalizedTx(date="2026-07-01", payee="LEMBAGA HASIL DALAM NEGERI MAL", narration="tax payment", amount=102.0, direction="out", source="mypb"),
        NormalizedTx(date="2026-07-04", payee="UNKNOWN PAYEE", narration="sa193a jul26 unifi", amount=94.35, direction="out", source="mypb"),
    ]

    classified = await classify_batch(txns, [], mock_sheets, mock_agent)
    assert len(classified) == 3

    # Check Payee registry match
    assert classified[0].property_code == "C1811"
    assert classified[0].tx_type == "income"
    assert classified[0].month_tag == "Aug26"  # explicit in narration

    # Check PersonalPayees match
    assert classified[1].tx_type == "personal"

    # Check LLM classification fallback
    assert classified[2].property_code == "SA1903A"
    assert classified[2].category == "utility"

