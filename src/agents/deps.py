"""
Shared dependencies injected into every agent via RunContext.
"""

from dataclasses import dataclass, field
from datetime import datetime

from src.sheets.client import SheetsClient


@dataclass
class AgentDeps:
    """Dependencies available to all agents through pydantic-ai's RunContext."""
    sheets: SheetsClient
    current_date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    _transaction_recorded: bool = False  # Idempotency flag — prevents duplicate writes
