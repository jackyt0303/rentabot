"""
Importers package for RentaBot — parsers and dataclasses for bank CSVs and WhatsApp exports.
"""

from dataclasses import dataclass, field


@dataclass
class NormalizedTx:
    date: str           # ISO date string (YYYY-MM-DD)
    payee: str          # extracted sender/recipient name
    narration: str      # full transaction description
    amount: float       # positive transaction amount
    direction: str      # "in" (credit/income) or "out" (debit/expense)
    source: str         # "cimb" | "mypb"


@dataclass
class WhatsAppSignal:
    date: str                   # ISO date string (YYYY-MM-DD)
    sender: str                 # message sender name
    text: str                   # full message text
    amount: float | None = None # extracted RM amount if present


@dataclass
class ClassifiedTx(NormalizedTx):
    property_code: str = ""
    room_id: str = ""
    month_tag: str = ""
    category: str = ""
    tx_type: str = ""           # "income" | "expense" | "personal" | "unknown" | "duplicate"
    confidence: int = 0
    reasoning: str = ""
