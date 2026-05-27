"""
RentaBot configuration — property registry, expense categories, and model settings.
"""

# ============================================================
# PROPERTY REGISTRY
# ============================================================
# All managed properties. room_ids is None for whole-unit tenancies.

PROPERTIES = {
    "C1613A": {
        "address": "C-16-13A",  # Update with full address
        "type": "multi-room",
        "room_ids": ["R1", "R2", "R3", "Studio"],
    },
    "SA1903A": {
        "address": "SA-19-03A",
        "type": "multi-room",
        "room_ids": ["R1", "R2", "R3", "R4"],
    },
    "C03A09": {
        "address": "C-03A-09",
        "type": "multi-room",
        "room_ids": ["R1", "R2", "R3", "R4"],
    },
    "C1811": {
        "address": "C-18-11",
        "type": "multi-room",
        "room_ids": ["R1", "R2", "R3", "R4"],
    },
    "SV2": {
        "address": "SV2",
        "type": "whole-unit",
        "room_ids": None,
    },
    "IRIS": {
        "address": "Iris",
        "type": "whole-unit",
        "room_ids": None,
    },
    "ISKANDARSHOP": {
        "address": "Iskandar Shop",
        "type": "whole-unit",
        "room_ids": None,
    },
}

# ============================================================
# PROPERTY ALIASES — maps shorthand/typos to canonical codes
# ============================================================

PROPERTY_ALIASES = {
    # C1613A
    "c1613a": "C1613A",
    "1613a": "C1613A",
    "1613": "C1613A",
    # SA1903A
    "sa1903a": "SA1903A",
    "1903a": "SA1903A",
    "1903": "SA1903A",
    # C03A09
    "c03a09": "C03A09",
    "03a09": "C03A09",
    "3a09": "C03A09",
    # C1811
    "c1811": "C1811",
    "1811": "C1811",
    # SV2
    "sv2": "SV2",
    # Iris
    "iris": "IRIS",
    # IskandarShop
    "iskandarshop": "ISKANDARSHOP",
    "iskandar": "ISKANDARSHOP",
    "shop": "ISKANDARSHOP",
}

# ============================================================
# EXPENSE CATEGORIES — keyword → category mapping
# ============================================================

EXPENSE_CATEGORIES = {
    "rental": "rental",
    "rent": "rental",
    "paid": "rental",
    # Utilities
    "tnb": "utility",
    "electricity": "utility",
    "water": "utility",
    "indah water": "utility",
    "utility": "utility",
    "utilities": "utility",
    # Maintenance
    "maintenance": "maintenance",
    "sinking": "maintenance",
    "sinking fund": "maintenance",
    # Loan
    "loan": "loan",
    "loan repayment": "loan",
    # Wifi
    "wifi": "wifi",
    "internet": "wifi",
    # Cleaning
    "cleaning": "cleaning",
    "cleaner": "cleaning",
    # Tax
    "cukai": "tax",
    "taksiran": "tax",
    "quit rent": "tax",
    "tax": "tax",
    # Commission / Fees
    "commission": "fees",
    "commision": "fees",
    "agent fee": "fees",
    "ta fee": "fees",
    "tenancy agreement": "fees",
    # Misc
    "misc": "misc",
}

# Keywords that indicate income (vs expense)
INCOME_KEYWORDS = {"rental", "rent", "paid", "deposit", "booking", "advance", "refund"}

# ============================================================
# GEMINI MODEL CONFIG
# ============================================================

GEMINI_MODEL = "gemini-3.1-flash-lite"  # or "gemini-pro-latest" for more complex tasks (with latency tradeoff)

# ============================================================
# SHEET TAB NAMES
# ============================================================

SHEET_TAB_TENANTS = "Tenants"
SHEET_TAB_CONFIG = "Config"
