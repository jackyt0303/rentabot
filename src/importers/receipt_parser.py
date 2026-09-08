"""
Multimodal DuitNow Receipt Vision Pipeline for RentaBot.

Extracts structured transaction details from Malaysian banking receipts (Maybank MAE,
CIMB Clicks/OCTO, Public Bank/MyPB, Touch 'n Go eWallet, RHB, etc.) and utility bills (TNB,
Air Selangor, Indah Water, Unifi). Classifies entities against RentaBot's property and payee
registries, checks for duplicates, and produces ClassifiedReceipt objects.
"""

import os
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from src.config import EXPENSE_CATEGORIES, GEMINI_MODEL, PROPERTIES, PROPERTY_ALIASES
from src.importers import ClassifiedTx, NormalizedTx
from src.sheets.client import SheetsClient
from src.utils.logger import log


class ReceiptParsingError(Exception):
    """Raised when vision receipt extraction fails."""
    pass


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DuitNowReceipt(BaseModel):
    reference_id: str = Field(default="", description="DuitNow transaction reference number or ID")
    transaction_date: str = Field(default="", description="Transaction date in ISO format YYYY-MM-DD")
    transaction_time: str | None = Field(default=None, description="Transaction time in HH:MM:SS or HH:MM format if available")
    amount: float = Field(default=0.0, description="Transferred amount in MYR, e.g. 750.00")
    recipient_name: str = Field(default="", description="Beneficiary / recipient account or business name")
    recipient_bank: str | None = Field(default=None, description="Receiving bank or provider name (e.g. Maybank, CIMB, Public Bank, TNG)")
    sender_name: str | None = Field(default=None, description="Transfer sender / payer name if available")
    recipient_reference: str | None = Field(default=None, description="Recipient reference / notes entered by sender")
    payment_details: str | None = Field(default=None, description="Additional payment details / description / remarks")
    status: str = Field(default="Successful", description="Payment status, e.g. Successful, Accepted, Completed")
    payment_type: str = Field(default="duitnow_transfer", description="Payment type: duitnow_transfer, duitnow_qr, jompay, or bank_transfer")
    raw_text: str | None = Field(default=None, description="Unparsed text or OCR dump if available")


class ClassifiedReceipt(BaseModel):
    receipt: DuitNowReceipt
    property_code: str = Field(default="", description="Normalized property code (e.g. C1613A, SA1903A)")
    room_id: str = Field(default="", description="Normalized room id (e.g. R1, R2, Studio, or empty for whole units)")
    month_tag: str = Field(default="", description="Billing period tag (e.g. Jul26, Aug26)")
    tx_type: str = Field(default="income", description="'income', 'expense', 'personal', 'duplicate', or 'unknown'")
    category: str = Field(default="rental", description="Category: 'rental', 'utility', 'maintenance', 'wifi', etc.")
    tenant_name: str = Field(default="", description="Matched tenant name from registry")
    confidence: int = Field(default=0, description="Confidence score 0-100")
    reasoning: str = Field(default="", description="Explanation of classification decisions")

    @property
    def amount(self) -> float:
        return self.receipt.amount

    @property
    def date(self) -> str:
        return self.receipt.transaction_date

    @property
    def reference_id(self) -> str:
        return self.receipt.reference_id

    def to_normalized_tx(self) -> NormalizedTx:
        payee = self.tenant_name or self.receipt.sender_name or self.receipt.recipient_name or "Unknown"
        narration_parts = [
            f"DuitNow Ref: {self.receipt.reference_id}" if self.receipt.reference_id else "",
            self.receipt.recipient_reference or "",
            self.receipt.payment_details or "",
        ]
        narration = " | ".join(p for p in narration_parts if p) or f"DuitNow transfer {self.receipt.amount}"
        direction = "in" if self.tx_type == "income" else "out"
        return NormalizedTx(
            date=self.receipt.transaction_date,
            payee=payee,
            narration=narration,
            amount=self.receipt.amount,
            direction=direction,
            source="duitnow",
        )

    def to_classified_tx(self) -> ClassifiedTx:
        norm = self.to_normalized_tx()
        return ClassifiedTx(
            date=norm.date,
            payee=norm.payee,
            narration=norm.narration,
            amount=norm.amount,
            direction=norm.direction,
            source=norm.source,
            property_code=self.property_code,
            room_id=self.room_id,
            month_tag=self.month_tag,
            category=self.category,
            tx_type=self.tx_type,
            confidence=self.confidence,
            reasoning=self.reasoning,
        )


# ---------------------------------------------------------------------------
# Media Type Detection
# ---------------------------------------------------------------------------

def detect_image_media_type(data: bytes, filename: str | None = None) -> str:
    """Detect MIME media type from file magic bytes or filename extension."""
    if len(data) >= 8:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
            return "image/webp"
        if data.startswith(b"%PDF"):
            return "application/pdf"
        if len(data) >= 12 and data[4:8] == b"ftyp":
            return "image/heic"

    if filename:
        fn_lower = filename.lower()
        if fn_lower.endswith(".png"):
            return "image/png"
        if fn_lower.endswith((".jpg", ".jpeg")):
            return "image/jpeg"
        if fn_lower.endswith(".webp"):
            return "image/webp"
        if fn_lower.endswith(".pdf"):
            return "application/pdf"
        if fn_lower.endswith(".heic"):
            return "image/heic"

    return "image/jpeg"


# ---------------------------------------------------------------------------
# Vision Agent Builder
# ---------------------------------------------------------------------------

def build_receipt_vision_agent(model: Any = None, api_key: str | None = None) -> Agent:
    """Build the pydantic-ai vision agent configured for DuitNow receipt extraction."""
    if model is None:
        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        model = GoogleModel(GEMINI_MODEL, provider=GoogleProvider(api_key=key))

    system_prompt = (
        "You are an expert Malaysian banking receipt parser. "
        "Your task is to analyze images or documents of payment receipts, specifically DuitNow Transfers, "
        "DuitNow QR receipts, JomPAY receipts, bank transfers, or utility bill receipts (Maybank MAE/M2U, "
        "CIMB Clicks/OCTO, Public Bank/MyPB, Touch 'n Go eWallet, RHB, Hong Leong, AmBank, TNB, Air Selangor, etc.).\n\n"
        "Carefully extract the following fields:\n"
        "- reference_id: The transaction reference number, Ref ID, or Transaction ID.\n"
        "- transaction_date: Date of transfer in ISO format YYYY-MM-DD. Convert dates like '25 Jul 2026' or '25/07/2026' to '2026-07-25'.\n"
        "- transaction_time: Time of transfer in HH:MM:SS or HH:MM if visible.\n"
        "- amount: Total transferred amount in MYR as a float (e.g., 750.00). Strip 'RM' or 'MYR' currency prefixes.\n"
        "- recipient_name: Payee, beneficiary, or transfer-to account name.\n"
        "- recipient_bank: Name of the recipient bank or e-wallet if present.\n"
        "- sender_name: Payer, transfer-from, or sender name if present.\n"
        "- recipient_reference: Recipient reference or description entered by the sender (e.g. 'C1613A R2 Rental').\n"
        "- payment_details: Additional payment details or remarks.\n"
        "- status: 'Successful', 'Accepted', 'Completed', 'Failed', or 'Pending'.\n"
        "- payment_type: 'duitnow_transfer', 'duitnow_qr', 'jompay', or 'bank_transfer'.\n"
        "- raw_text: Any other key text observed on the receipt."
    )

    return Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=DuitNowReceipt,
    )


# ---------------------------------------------------------------------------
# Vision Extraction
# ---------------------------------------------------------------------------

async def extract_receipt_from_image(
    image_bytes: bytes,
    media_type: str | None = None,
    agent: Any = None,
) -> DuitNowReceipt:
    """Extract structured DuitNowReceipt from image bytes using Gemini vision."""
    if media_type is None:
        media_type = detect_image_media_type(image_bytes)

    if agent is None:
        agent = build_receipt_vision_agent()

    user_prompt = [
        BinaryContent(data=image_bytes, media_type=media_type),
        "Extract all transaction details from this Malaysian payment receipt.",
    ]

    try:
        result = await agent.run(user_prompt)
        return result.output
    except Exception as e:
        log.error(f"RECEIPT_VISION error extracting receipt: {e}", exc_info=True)
        raise ReceiptParsingError(f"Failed to extract receipt: {e}") from e


# ---------------------------------------------------------------------------
# Entity Extraction & Date Helpers
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "jan": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr",
    "may": "May", "jun": "Jun", "jul": "Jul", "aug": "Aug",
    "sep": "Sep", "oct": "Oct", "nov": "Nov", "dec": "Dec",
}

_MONTH_PATTERN = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*['\s]*(\d{2}|\d{4})?\b",
    re.IGNORECASE,
)

_ROOM_PATTERN = re.compile(
    r"\b(r[1-4]|studio|master|middle|small|room\s*[1-4])\b",
    re.IGNORECASE,
)


def _extract_month_tag(text: str, fallback_iso_date: str) -> tuple[str, bool]:
    """Extract month tag like 'Jul26' from text. Returns (tag, is_explicit)."""
    match = _MONTH_PATTERN.search(text)
    if match:
        m_str = match.group(1).lower()
        yr_str = match.group(2)
        month_prefix = _MONTH_MAP.get(m_str, "Jan")
        if yr_str:
            yr = yr_str[-2:]
        elif len(fallback_iso_date) >= 4:
            yr = fallback_iso_date[:4][-2:]
        else:
            yr = datetime.now().strftime("%y")
        return f"{month_prefix}{yr}", True

    if fallback_iso_date:
        try:
            dt = datetime.strptime(fallback_iso_date[:10], "%Y-%m-%d")
            return dt.strftime("%b%y"), False
        except ValueError:
            pass

    return datetime.now().strftime("%b%y"), False


def _normalize_room_id(raw_room: str) -> str:
    """Normalize room string (e.g. 'room 2', 'r2', 'studio') to canonical format."""
    cleaned = raw_room.strip().upper()
    if "STUDIO" in cleaned:
        return "Studio"
    match = re.search(r"R(?:OOM)?\s*([1-4])", cleaned)
    if match:
        return f"R{match.group(1)}"
    if "MASTER" in cleaned:
        return "R1"
    if "MIDDLE" in cleaned:
        return "R2"
    if "SMALL" in cleaned:
        return "R3"
    return cleaned


def _resolve_property_code(text: str) -> str:
    """Match property aliases in text to canonical property code."""
    text_lower = text.lower()
    for alias, code in PROPERTY_ALIASES.items():
        pattern = r"\b" + re.escape(alias) + r"\b"
        if re.search(pattern, text_lower):
            return code

    clean_text = re.sub(r"[-_\s]", "", text_lower)
    for alias, code in PROPERTY_ALIASES.items():
        if alias in clean_text:
            return code

    for code in PROPERTIES:
        if code.lower() in clean_text:
            return code

    return ""


# ---------------------------------------------------------------------------
# Receipt Classification Engine
# ---------------------------------------------------------------------------

def classify_receipt(
    receipt: DuitNowReceipt,
    sheets: SheetsClient,
) -> ClassifiedReceipt:
    """Classify a DuitNowReceipt into a ClassifiedReceipt using Sheets registries and business rules."""
    classified = ClassifiedReceipt(receipt=receipt)
    reasoning_steps: list[str] = []

    ref_text = receipt.recipient_reference or ""
    details_text = receipt.payment_details or ""
    combined_notes = f"{ref_text} {details_text} {receipt.raw_text or ''}".strip()
    sender = (receipt.sender_name or "").strip()
    recipient = (receipt.recipient_name or "").strip()

    # Step 1: Check PersonalPayees registry
    personal_payees = sheets.get_personal_payees()
    personal_names = {
        str(p.get("payee_name", "")).strip().lower(): p.get("description", "")
        for p in personal_payees if p.get("payee_name")
    }

    recipient_lower = recipient.lower()
    sender_lower = sender.lower()

    for p_name, desc in personal_names.items():
        if p_name and (p_name in recipient_lower or p_name in sender_lower):
            classified.tx_type = "personal"
            classified.confidence = 100
            classified.reasoning = f"Matched PersonalPayees registry ({desc or p_name})"
            m_tag, _ = _extract_month_tag(combined_notes, receipt.transaction_date)
            classified.month_tag = m_tag
            return classified

    # Step 2: Check for Expense keywords (Utilities, Maintenance, Taxes, WiFi)
    is_expense = False
    expense_category = "rental"
    for kw, cat in EXPENSE_CATEGORIES.items():
        if kw in combined_notes.lower() or kw in recipient_lower:
            if kw not in ("rental", "rent", "paid"):
                is_expense = True
                expense_category = cat
                reasoning_steps.append(f"Expense detected keyword '{kw}' -> category '{cat}'")
                break

    utility_names = {
        "tenaga nasional": "utility",
        "tnb": "utility",
        "air selangor": "utility",
        "syabas": "utility",
        "indah water": "utility",
        "iwk": "utility",
        "telekom malaysia": "wifi",
        "unifi": "wifi",
        "time dotcom": "wifi",
        "maxis": "wifi",
        "badan pengurusan": "maintenance",
        "management corporation": "maintenance",
    }
    for provider, cat in utility_names.items():
        if provider in recipient_lower or provider in combined_notes.lower():
            is_expense = True
            expense_category = cat
            reasoning_steps.append(f"Provider '{provider}' -> {cat}")
            break

    # Step 3: Check Payees registry (tenants & regular payees)
    payees_registry = sheets.get_payees()
    payee_map = {
        str(r.get("payee_name", "")).strip().lower(): r
        for r in payees_registry if r.get("payee_name")
    }

    matched_reg = None
    for p_name, reg in payee_map.items():
        if p_name and (p_name in sender_lower or p_name in combined_notes.lower()):
            matched_reg = reg
            classified.tenant_name = reg.get("payee_name", "")
            classified.property_code = reg.get("property_code", "").upper()
            classified.room_id = reg.get("room_id", "").upper()
            reasoning_steps.append(f"Matched Payees registry for tenant '{classified.tenant_name}'")
            break

    # Also check active tenants registry if tenant not yet matched
    if not classified.tenant_name:
        active_tenants = sheets.get_active_tenants()
        for t in active_tenants:
            t_name = str(t.get("tenant_name", "")).strip().lower()
            if t_name and (t_name in sender_lower or t_name in combined_notes.lower()):
                classified.tenant_name = t.get("tenant_name", "")
                if not classified.property_code:
                    classified.property_code = str(t.get("property_code", "")).upper()
                if not classified.room_id:
                    classified.room_id = str(t.get("room_id", "")).upper()
                reasoning_steps.append(f"Matched active tenant '{classified.tenant_name}'")
                break

    # Step 4: Resolve Property Code from Reference/Notes if not set
    if not classified.property_code:
        prop_found = _resolve_property_code(combined_notes)
        if prop_found:
            classified.property_code = prop_found
            reasoning_steps.append(f"Extracted property code '{prop_found}' from reference")

    # Step 5: Resolve Room ID if multi-room property
    prop_info = PROPERTIES.get(classified.property_code, {})
    if prop_info.get("type") == "whole-unit":
        classified.room_id = ""
        reasoning_steps.append("Whole-unit tenancy (room_id is empty)")
    elif not classified.room_id:
        room_match = _ROOM_PATTERN.search(combined_notes)
        if room_match:
            classified.room_id = _normalize_room_id(room_match.group(1))
            reasoning_steps.append(f"Extracted room '{classified.room_id}' from reference")

    # Step 6: Month Tag Resolution
    m_tag, is_explicit_month = _extract_month_tag(combined_notes, receipt.transaction_date)
    classified.month_tag = m_tag
    if is_explicit_month:
        reasoning_steps.append(f"Explicit billing period '{m_tag}'")
    else:
        reasoning_steps.append(f"Inferred month tag '{m_tag}' from transaction date")

    # Step 7: Determine Transaction Type & Category
    if is_expense:
        classified.tx_type = "expense"
        classified.category = expense_category
    elif classified.property_code:
        classified.tx_type = "income"
        classified.category = "rental"
    else:
        classified.tx_type = "unknown"
        classified.category = "misc"

    # Step 8: Calculate Base Confidence
    if classified.tx_type == "unknown":
        confidence = 35
        reasoning_steps.append("Unrecognized property code")
    elif is_expense and classified.property_code and is_explicit_month:
        confidence = 90
    elif is_expense and classified.property_code:
        confidence = 85
    elif matched_reg and classified.room_id and is_explicit_month:
        confidence = 95
    elif matched_reg and (classified.room_id or prop_info.get("type") == "whole-unit"):
        confidence = 90
    elif classified.property_code and (classified.room_id or prop_info.get("type") == "whole-unit") and is_explicit_month:
        confidence = 90
    elif classified.property_code and (classified.room_id or prop_info.get("type") == "whole-unit"):
        confidence = 85
    elif classified.property_code:
        confidence = 70
    else:
        confidence = 50

    # Step 9: Duplicate Detection
    existing_txns = sheets.get_all_transactions()
    try:
        tx_dt = datetime.strptime(receipt.transaction_date[:10], "%Y-%m-%d")
    except ValueError:
        tx_dt = datetime.now()

    for ext in existing_txns:
        ext_amt = float(ext.get("amount", 0) or 0)
        ext_prop = str(ext.get("property_code", "")).upper()
        ext_desc = str(ext.get("description", ""))
        ext_date_str = str(ext.get("date", "")).strip()

        if receipt.reference_id and receipt.reference_id in ext_desc:
            classified.tx_type = "duplicate"
            reasoning_steps.append(f"Duplicate reference ID '{receipt.reference_id}' found in existing records")
            break

        if ext_prop == classified.property_code and abs(ext_amt - receipt.amount) < 0.01:
            try:
                ext_dt = datetime.strptime(ext_date_str[:10], "%Y-%m-%d")
                if abs((ext_dt - tx_dt).days) <= 2:
                    classified.tx_type = "duplicate"
                    reasoning_steps.append(
                        f"Duplicate transaction found on {ext_date_str} for RM {ext_amt:,.2f}"
                    )
                    break
            except ValueError:
                pass

    classified.confidence = confidence
    classified.reasoning = "; ".join(reasoning_steps) or "Classified from receipt data"
    return classified


# ---------------------------------------------------------------------------
# High-Level Pipeline & Summary Formatting
# ---------------------------------------------------------------------------

async def parse_and_classify_receipt(
    image_bytes: bytes,
    filename: str | None = None,
    sheets: SheetsClient = None,
    agent: Any = None,
) -> ClassifiedReceipt:
    """End-to-end receipt parsing: runs vision extraction and entity classification."""
    media_type = detect_image_media_type(image_bytes, filename)
    receipt = await extract_receipt_from_image(image_bytes, media_type=media_type, agent=agent)
    if sheets is not None:
        return classify_receipt(receipt, sheets)
    return ClassifiedReceipt(receipt=receipt, reasoning="Parsed receipt without sheets client")


def format_receipt_summary(c: ClassifiedReceipt) -> str:
    """Format ClassifiedReceipt into a user-friendly Telegram/CLI summary string."""
    r = c.receipt
    lines = [
        "🧾 *DuitNow Receipt Detected*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏢 *Property:*   `{c.property_code or 'Unknown'}`",
    ]
    if c.room_id:
        lines.append(f"🚪 *Room:*       `{c.room_id}`")
    lines.extend([
        f"📅 *Month Tag:*  `{c.month_tag}` (Date: {r.transaction_date})",
        f"💰 *Amount:*     `RM {r.amount:,.2f}`",
    ])
    if c.tenant_name or r.sender_name:
        payer = c.tenant_name or r.sender_name
        lines.append(f"👤 *Payer:*      {payer}")
    if r.recipient_name:
        lines.append(f"🏦 *Recipient:*  {r.recipient_name}")
    if r.reference_id:
        lines.append(f"🏷️ *Ref ID:*     `{r.reference_id}`")

    tx_display = c.tx_type.capitalize()
    if c.category:
        tx_display += f" ({c.category})"
    lines.extend([
        f"📊 *Type:*       {tx_display}",
        f"🎯 *Confidence:* {c.confidence}%",
        f"ℹ️ *Reasoning:*  _{c.reasoning}_",
        "━━━━━━━━━━━━━━━━━━━━",
    ])
    return "\n".join(lines)
