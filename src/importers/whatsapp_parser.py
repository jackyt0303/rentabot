"""
WhatsApp Group Chat Export Parser.
Parses .txt chat exports in format: [DD/MM/YYYY, HH:MM:SS AM/PM] Sender: message
"""

import re
from datetime import datetime
from src.importers import WhatsAppSignal

MSG_HEADER_REGEX = re.compile(
    r"^\[(\d{1,2}/\d{1,2}/\d{4}),\s*(\d{1,2}:\d{2}:\d{2}\s*[\u202f\s]*(?:AM|PM|am|pm)?)\]\s*([^:]+):\s*(.*)$"
)

RM_REGEX = re.compile(r"RM\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)

SKIP_PHRASES = {
    "image omitted",
    "video omitted",
    "audio omitted",
    "document omitted",
    "sticker omitted",
    "this message was deleted",
    "messages and calls are end-to-end encrypted",
}


def _parse_wa_date(date_str: str) -> str:
    """Parse date like '24/12/2023' or '01/01/2024' into ISO format '2024-01-01'."""
    dt = datetime.strptime(date_str, "%d/%m/%Y")
    return dt.strftime("%Y-%m-%d")


def parse(content: bytes) -> list[WhatsAppSignal]:
    """Parse WhatsApp export .txt bytes into a list of WhatsAppSignal objects."""
    text = content.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()

    signals: list[WhatsAppSignal] = []
    current_signal: WhatsAppSignal | None = None

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue

        match = MSG_HEADER_REGEX.match(line_clean)
        if match:
            if current_signal:
                signals.append(current_signal)
                current_signal = None

            date_str, time_str, sender, msg_text = match.groups()
            msg_text_stripped = msg_text.strip()

            # Skip system/omitted messages
            if any(phrase in msg_text_stripped.lower() for phrase in SKIP_PHRASES):
                continue
            if msg_text_stripped.startswith("<attached:"):
                continue

            try:
                iso_date = _parse_wa_date(date_str)
            except ValueError:
                continue

            rm_matches = RM_REGEX.findall(msg_text_stripped)
            amount = None
            if rm_matches:
                try:
                    amount = float(rm_matches[0].replace(",", ""))
                except ValueError:
                    amount = None

            current_signal = WhatsAppSignal(
                date=iso_date,
                sender=sender.strip(),
                text=msg_text_stripped,
                amount=amount,
            )
        else:
            # Multi-line message continuation
            if current_signal:
                if any(phrase in line_clean.lower() for phrase in SKIP_PHRASES):
                    continue
                current_signal.text += "\n" + line_clean
                if current_signal.amount is None:
                    rm_matches = RM_REGEX.findall(line_clean)
                    if rm_matches:
                        try:
                            current_signal.amount = float(rm_matches[0].replace(",", ""))
                        except ValueError:
                            pass

    if current_signal:
        signals.append(current_signal)

    return signals
