"""
Unit tests for Multimodal DuitNow Receipt Vision Pipeline.
Strictly pure mocked unit tests (zero external network calls, zero real credentials).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic_ai.models.test import TestModel

from src.importers import NormalizedTx, ClassifiedTx
from src.importers.receipt_parser import (
    DuitNowReceipt,
    ClassifiedReceipt,
    detect_image_media_type,
    extract_receipt_from_image,
    classify_receipt,
    parse_and_classify_receipt,
    format_receipt_summary,
    ReceiptParsingError,
)
from src.sheets.client import SheetsClient

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test Fixtures & Sample Receipts
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_duitnow_income_receipt():
    return DuitNowReceipt(
        reference_id="20260725143201001",
        transaction_date="2026-07-25",
        transaction_time="14:32:01",
        amount=770.00,
        recipient_name="LANDLORD VENTURES",
        recipient_bank="CIMB BANK",
        sender_name="TANG ZHE KHEE",
        recipient_reference="C1811 R3 Aug26 Rental",
        payment_details="August rental payment",
        status="Successful",
        payment_type="duitnow_transfer",
    )


@pytest.fixture
def sample_tnb_expense_receipt():
    return DuitNowReceipt(
        reference_id="TNB202607139988",
        transaction_date="2026-07-13",
        transaction_time="11:15:00",
        amount=132.10,
        recipient_name="TENAGA NASIONAL BERHAD",
        recipient_bank="MAYBANK",
        sender_name="LANDLORD VENTURES",
        recipient_reference="C1811 Jul26 electricity",
        payment_details="TNB electricity bill",
        status="Successful",
        payment_type="duitnow_transfer",
    )


@pytest.fixture
def mock_sheets_for_receipts():
    sheets = MagicMock(spec=SheetsClient)
    sheets.get_payees.return_value = [
        {"payee_name": "TANG ZHE KHEE", "property_code": "C1811", "room_id": "R3", "type": "tenant"},
        {"payee_name": "ALICE TAN", "property_code": "C1613A", "room_id": "R1", "type": "tenant"},
        {"payee_name": "BOB SMITH", "property_code": "SV2", "room_id": "", "type": "tenant"},
    ]
    sheets.get_personal_payees.return_value = [
        {"payee_name": "LEMBAGA HASIL DALAM NEGERI MAL", "description": "Tax Payment"},
        {"payee_name": "MOM PERSONAL", "description": "Family transfer"},
    ]
    sheets.get_all_transactions.return_value = [
        {
            "date": "2026-07-24",
            "month_tag": "Jul26",
            "property_code": "C1811",
            "room_id": "R3",
            "category": "rental",
            "description": "DuitNow Ref: 20260725143201001",
            "amount": "770.0",
            "type": "income",
        }
    ]
    return sheets


# ---------------------------------------------------------------------------
# 1. Media Type Detection Tests
# ---------------------------------------------------------------------------

def test_detect_image_media_type():
    # Magic bytes
    assert detect_image_media_type(b"\x89PNG\r\n\x1a\n\x00\x00") == "image/png"
    assert detect_image_media_type(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "image/jpeg"
    assert detect_image_media_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert detect_image_media_type(b"%PDF-1.5\n") == "application/pdf"
    
    # Filename extensions
    assert detect_image_media_type(b"dummy", "receipt.png") == "image/png"
    assert detect_image_media_type(b"dummy", "receipt.jpg") == "image/jpeg"
    assert detect_image_media_type(b"dummy", "receipt.jpeg") == "image/jpeg"
    assert detect_image_media_type(b"dummy", "statement.pdf") == "application/pdf"
    assert detect_image_media_type(b"dummy", "receipt.heic") == "image/heic"
    
    # Fallback
    assert detect_image_media_type(b"raw bytes without header") == "image/jpeg"


# ---------------------------------------------------------------------------
# 2. Vision Extraction (Mocked Model) Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_receipt_from_image_mocked(sample_duitnow_income_receipt):
    mock_agent = MagicMock()
    mock_result = MagicMock()
    mock_result.output = sample_duitnow_income_receipt
    mock_agent.run = AsyncMock(return_value=mock_result)

    dummy_image = b"\x89PNG\r\n\x1a\nfake_png_data"
    receipt = await extract_receipt_from_image(dummy_image, media_type="image/png", agent=mock_agent)

    assert receipt.reference_id == "20260725143201001"
    assert receipt.amount == 770.00
    assert receipt.sender_name == "TANG ZHE KHEE"
    assert receipt.recipient_reference == "C1811 R3 Aug26 Rental"
    assert receipt.status == "Successful"


@pytest.mark.asyncio
async def test_extract_receipt_error_handling():
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=RuntimeError("Vision model error"))

    with pytest.raises(ReceiptParsingError, match="Failed to extract receipt"):
        await extract_receipt_from_image(b"invalid", agent=mock_agent)


# ---------------------------------------------------------------------------
# 3. Classification Tests
# ---------------------------------------------------------------------------

def test_classify_receipt_tenant_payee_registry_match(sample_duitnow_income_receipt, mock_sheets_for_receipts):
    # Modify receipt so reference does not contain property info, testing registry resolution
    sample_duitnow_income_receipt.recipient_reference = "Rent payment"
    sample_duitnow_income_receipt.payment_details = ""

    # Clear existing transactions to avoid duplicate trigger
    mock_sheets_for_receipts.get_all_transactions.return_value = []

    classified = classify_receipt(sample_duitnow_income_receipt, mock_sheets_for_receipts)

    assert classified.property_code == "C1811"
    assert classified.room_id == "R3"
    assert classified.tenant_name == "TANG ZHE KHEE"
    assert classified.tx_type == "income"
    assert classified.category == "rental"
    assert classified.month_tag == "Jul26"  # fallback to transaction date 2026-07-25
    assert classified.confidence >= 90
    assert "Matched Payees registry" in classified.reasoning


def test_classify_receipt_explicit_reference(mock_sheets_for_receipts):
    receipt = DuitNowReceipt(
        reference_id="REF999001",
        transaction_date="2026-06-30",
        amount=800.0,
        recipient_name="LANDLORD",
        sender_name="UNKNOWN SENDER",
        recipient_reference="SA1903A R2 Jul26 Rental",
        status="Successful",
    )
    mock_sheets_for_receipts.get_all_transactions.return_value = []

    classified = classify_receipt(receipt, mock_sheets_for_receipts)

    assert classified.property_code == "SA1903A"
    assert classified.room_id == "R2"
    assert classified.month_tag == "Jul26"
    assert classified.tx_type == "income"
    assert classified.confidence >= 85


def test_classify_receipt_whole_unit_property(mock_sheets_for_receipts):
    receipt = DuitNowReceipt(
        reference_id="REF_SV2_01",
        transaction_date="2026-08-01",
        amount=2500.0,
        recipient_name="LANDLORD",
        sender_name="BOB SMITH",
        recipient_reference="SV2 Aug26 Rent",
        status="Successful",
    )
    mock_sheets_for_receipts.get_all_transactions.return_value = []

    classified = classify_receipt(receipt, mock_sheets_for_receipts)

    assert classified.property_code == "SV2"
    assert classified.room_id == ""  # Whole unit has empty room_id
    assert classified.month_tag == "Aug26"
    assert classified.tx_type == "income"


def test_classify_receipt_utility_expense(sample_tnb_expense_receipt, mock_sheets_for_receipts):
    mock_sheets_for_receipts.get_all_transactions.return_value = []

    classified = classify_receipt(sample_tnb_expense_receipt, mock_sheets_for_receipts)

    assert classified.property_code == "C1811"
    assert classified.tx_type == "expense"
    assert classified.category == "utility"
    assert classified.month_tag == "Jul26"
    assert classified.confidence >= 85
    assert "TNB" in classified.reasoning or "utility" in classified.reasoning


def test_classify_receipt_personal_payee(mock_sheets_for_receipts):
    receipt = DuitNowReceipt(
        reference_id="TAX123456",
        transaction_date="2026-07-01",
        amount=102.00,
        recipient_name="LEMBAGA HASIL DALAM NEGERI MAL",
        sender_name="LANDLORD",
        recipient_reference="Income Tax 2025",
        status="Successful",
    )

    classified = classify_receipt(receipt, mock_sheets_for_receipts)

    assert classified.tx_type == "personal"
    assert classified.confidence == 100
    assert "PersonalPayees" in classified.reasoning


def test_classify_receipt_duplicate_detection(sample_duitnow_income_receipt, mock_sheets_for_receipts):
    # mock_sheets_for_receipts already has an existing transaction matching amount 770.0 and Ref ID on 2026-07-24
    classified = classify_receipt(sample_duitnow_income_receipt, mock_sheets_for_receipts)

    assert classified.tx_type == "duplicate"
    assert "Duplicate" in classified.reasoning


def test_classify_receipt_unknown_property(mock_sheets_for_receipts):
    receipt = DuitNowReceipt(
        reference_id="UNKNOWN123",
        transaction_date="2026-07-25",
        amount=50.00,
        recipient_name="RANDOM CAFE",
        sender_name="LANDLORD",
        recipient_reference="Coffee and snack",
        status="Successful",
    )
    mock_sheets_for_receipts.get_all_transactions.return_value = []

    classified = classify_receipt(receipt, mock_sheets_for_receipts)

    assert classified.tx_type == "unknown"
    assert classified.property_code == ""
    assert classified.confidence <= 40


# ---------------------------------------------------------------------------
# 4. Conversion & Formatting Tests
# ---------------------------------------------------------------------------

def test_to_normalized_and_classified_tx(sample_duitnow_income_receipt, mock_sheets_for_receipts):
    mock_sheets_for_receipts.get_all_transactions.return_value = []
    classified = classify_receipt(sample_duitnow_income_receipt, mock_sheets_for_receipts)

    norm = classified.to_normalized_tx()
    assert isinstance(norm, NormalizedTx)
    assert norm.date == "2026-07-25"
    assert norm.amount == 770.0
    assert norm.direction == "in"
    assert norm.source == "duitnow"
    assert "20260725143201001" in norm.narration

    ctx = classified.to_classified_tx()
    assert isinstance(ctx, ClassifiedTx)
    assert ctx.property_code == "C1811"
    assert ctx.room_id == "R3"
    assert ctx.month_tag == "Aug26"
    assert ctx.tx_type == "income"
    assert ctx.amount == 770.0


def test_format_receipt_summary(sample_duitnow_income_receipt, mock_sheets_for_receipts):
    mock_sheets_for_receipts.get_all_transactions.return_value = []
    classified = classify_receipt(sample_duitnow_income_receipt, mock_sheets_for_receipts)

    summary = format_receipt_summary(classified)
    assert "DuitNow Receipt" in summary
    assert "C1811" in summary
    assert "R3" in summary
    assert "RM 770.00" in summary
    assert "Aug26" in summary
    assert "20260725143201001" in summary


# ---------------------------------------------------------------------------
# 5. Full Pipeline Function
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parse_and_classify_receipt_full(sample_duitnow_income_receipt, mock_sheets_for_receipts):
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value=MagicMock(output=sample_duitnow_income_receipt))
    mock_sheets_for_receipts.get_all_transactions.return_value = []

    classified = await parse_and_classify_receipt(
        image_bytes=b"fake_image_bytes",
        filename="cimb_receipt.jpg",
        sheets=mock_sheets_for_receipts,
        agent=mock_agent,
    )

    assert classified.property_code == "C1811"
    assert classified.room_id == "R3"
    assert classified.amount == 770.0
    assert classified.tx_type == "income"


# ---------------------------------------------------------------------------
# 6. Telegram Bot Integration Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bot_handle_photo_receipt(monkeypatch, sample_duitnow_income_receipt, mock_sheets_for_receipts):
    from bot import handle_photo, _import_sessions, ImportSession

    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "12345")
    user_id = 12345
    _import_sessions.clear()

    # Mock Update
    mock_update = MagicMock()
    mock_update.effective_user.id = user_id
    mock_photo = MagicMock(file_id="photo_file_id_001")
    mock_update.message.photo = [mock_photo]
    mock_update.message.reply_text = AsyncMock()

    # Mock Context & Bot
    mock_context = MagicMock()
    mock_context.bot_data = {"sheets": mock_sheets_for_receipts}
    mock_file = MagicMock()
    mock_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"dummy_receipt_bytes"))
    mock_context.bot.get_file = AsyncMock(return_value=mock_file)

    mock_sheets_for_receipts.get_all_transactions.return_value = []

    with patch("bot.parse_and_classify_receipt", new_callable=AsyncMock) as mock_parse:
        mock_classified = classify_receipt(sample_duitnow_income_receipt, mock_sheets_for_receipts)
        mock_parse.return_value = mock_classified

        await handle_photo(mock_update, mock_context)

        # Verify reply calls
        assert mock_update.message.reply_text.call_count >= 2
        last_reply = mock_update.message.reply_text.call_args[0][0]
        assert "DuitNow Receipt" in last_reply
        assert "C1811" in last_reply

        # Verify session state
        session = _import_sessions.get(user_id)
        assert session is not None
        assert session.pending_receipt is not None
        assert session.pending_receipt.property_code == "C1811"


@pytest.mark.asyncio
async def test_bot_handle_photo_unauthorized(monkeypatch):
    from bot import handle_photo, _import_sessions

    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "12345")
    _import_sessions.clear()

    mock_update = MagicMock()
    mock_update.effective_user.id = 99999  # unauthorized
    mock_update.message.reply_text = AsyncMock()
    mock_context = MagicMock()

    await handle_photo(mock_update, mock_context)

    mock_update.message.reply_text.assert_not_called()
    assert 99999 not in _import_sessions


@pytest.mark.asyncio
async def test_bot_confirm_pending_receipt_income(monkeypatch, sample_duitnow_income_receipt, mock_sheets_for_receipts):
    from bot import handle_message, _import_sessions, ImportSession

    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "12345")
    user_id = 12345
    _import_sessions.clear()

    mock_sheets_for_receipts.get_all_transactions.return_value = []
    classified = classify_receipt(sample_duitnow_income_receipt, mock_sheets_for_receipts)

    session = ImportSession(pending_receipt=classified)
    _import_sessions[user_id] = session

    mock_update = MagicMock()
    mock_update.effective_user.id = user_id
    mock_update.message.text = "confirm"
    mock_update.message.reply_text = AsyncMock()

    mock_context = MagicMock()
    mock_context.bot_data = {"sheets": mock_sheets_for_receipts}

    with patch("bot.record_income", new_callable=AsyncMock) as mock_rec_income:
        mock_rec_income.return_value = "Recorded rent RM770 for C1811 R3"

        await handle_message(mock_update, mock_context)

        mock_rec_income.assert_called_once()
        assert session.pending_receipt is None
        reply = mock_update.message.reply_text.call_args[0][0]
        assert "Recorded receipt to Sheets" in reply


@pytest.mark.asyncio
async def test_bot_confirm_pending_receipt_expense(monkeypatch, sample_tnb_expense_receipt, mock_sheets_for_receipts):
    from bot import handle_message, _import_sessions, ImportSession

    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "12345")
    user_id = 12345
    _import_sessions.clear()

    mock_sheets_for_receipts.get_all_transactions.return_value = []
    classified = classify_receipt(sample_tnb_expense_receipt, mock_sheets_for_receipts)

    session = ImportSession(pending_receipt=classified)
    _import_sessions[user_id] = session

    mock_update = MagicMock()
    mock_update.effective_user.id = user_id
    mock_update.message.text = "yes"
    mock_update.message.reply_text = AsyncMock()

    mock_context = MagicMock()
    mock_context.bot_data = {"sheets": mock_sheets_for_receipts}

    with patch("bot.record_expense", new_callable=AsyncMock) as mock_rec_expense:
        mock_rec_expense.return_value = "Recorded utility RM132.10 for C1811"

        await handle_message(mock_update, mock_context)

        mock_rec_expense.assert_called_once()
        assert session.pending_receipt is None


@pytest.mark.asyncio
async def test_bot_discard_pending_receipt(monkeypatch, sample_duitnow_income_receipt, mock_sheets_for_receipts):
    from bot import handle_message, _import_sessions, ImportSession

    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "12345")
    user_id = 12345
    _import_sessions.clear()

    mock_sheets_for_receipts.get_all_transactions.return_value = []
    classified = classify_receipt(sample_duitnow_income_receipt, mock_sheets_for_receipts)

    session = ImportSession(pending_receipt=classified)
    _import_sessions[user_id] = session

    mock_update = MagicMock()
    mock_update.effective_user.id = user_id
    mock_update.message.text = "skip"
    mock_update.message.reply_text = AsyncMock()

    mock_context = MagicMock()
    mock_context.bot_data = {"sheets": mock_sheets_for_receipts}

    await handle_message(mock_update, mock_context)

    assert session.pending_receipt is None
    reply = mock_update.message.reply_text.call_args[0][0]
    assert "Receipt discarded" in reply


@pytest.mark.asyncio
async def test_bot_edit_pending_receipt(monkeypatch, sample_duitnow_income_receipt, mock_sheets_for_receipts):
    from bot import handle_message, _import_sessions, ImportSession

    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "12345")
    user_id = 12345
    _import_sessions.clear()

    mock_sheets_for_receipts.get_all_transactions.return_value = []
    classified = classify_receipt(sample_duitnow_income_receipt, mock_sheets_for_receipts)

    session = ImportSession(pending_receipt=classified)
    _import_sessions[user_id] = session

    mock_update = MagicMock()
    mock_update.effective_user.id = user_id
    mock_update.message.text = "C1613A R1 Sep26"
    mock_update.message.reply_text = AsyncMock()

    mock_context = MagicMock()
    mock_context.bot_data = {"sheets": mock_sheets_for_receipts}

    await handle_message(mock_update, mock_context)

    assert session.pending_receipt is not None
    assert session.pending_receipt.property_code == "C1613A"
    assert session.pending_receipt.room_id == "R1"
    assert session.pending_receipt.month_tag == "Sep26"
    reply = mock_update.message.reply_text.call_args[0][0]
    assert "Updated receipt" in reply


# ---------------------------------------------------------------------------
# 7. CLI parse_receipt_file Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_main_parse_receipt_file(tmp_path, sample_duitnow_income_receipt, mock_sheets_for_receipts, capsys):
    from main import parse_receipt_file

    fake_receipt_file = tmp_path / "receipt.jpg"
    fake_receipt_file.write_bytes(b"dummy_image_data")

    with patch("main.parse_and_classify_receipt", new_callable=AsyncMock) as mock_parse:
        mock_classified = classify_receipt(sample_duitnow_income_receipt, mock_sheets_for_receipts)
        mock_parse.return_value = mock_classified

        await parse_receipt_file(str(fake_receipt_file), sheets=mock_sheets_for_receipts)

        captured = capsys.readouterr().out
        assert "DuitNow Receipt" in captured
        assert "C1811" in captured

    # Test file not found
    await parse_receipt_file(str(tmp_path / "nonexistent.jpg"), sheets=mock_sheets_for_receipts)
    captured_err = capsys.readouterr().out
    assert "Receipt file not found" in captured_err


