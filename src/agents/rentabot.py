"""
RentaBot — single pydantic-ai agent with all 5 tools.

MVP design decisions:
- 1 agent (no orchestrator/worker split) — faster to build and debug
- LLM handles entity extraction and expense categorisation from system prompt context
- No confirmation flow — wrong writes are trivially reversible in Sheets
- month_tag = billing period, always stated explicitly by the user
"""

import os
from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from src.agents.deps import AgentDeps
from src.config import EXPENSE_CATEGORIES, GEMINI_MODEL, PROPERTIES

load_dotenv()


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def _build_static_prompt() -> str:
    """Build the static part of the system prompt from config dicts."""
    props_lines = []
    for code, info in PROPERTIES.items():
        if info["room_ids"]:
            rooms = ", ".join(info["room_ids"])
            props_lines.append(f"  {code}: rooms {rooms}")
        else:
            props_lines.append(f"  {code}: whole unit (room_id is always empty string)")

    unique_cats = sorted(set(EXPENSE_CATEGORIES.values()))
    cats_text = ", ".join(unique_cats)

    return f"""You are RentaBot, a rental property management assistant for a Malaysian landlord.

## Managed Properties
{chr(10).join(props_lines)}

## Expense Categories
Valid values: {cats_text}
Keyword hints: TNB/electricity → utility | maintenance/sinking fund → maintenance | wifi/internet → wifi | loan → loan | cukai/quit rent → tax | cleaning → cleaning | commission/agent fee → fees

## Rules
1. month_tag is the BILLING PERIOD stated explicitly by the user (e.g., "May26", "Jun26").
2. Always normalize property codes to uppercase (c1613a → C1613A).
3. Always normalize room IDs to uppercase (r2 → R2).
4. Whole-unit properties (SV2, IRIS, ISKANDARSHOP) have room_id = "" (empty string).
5. Recording a rent payment → call record_income (category is always "rental").
6. Recording a bill or cost → call record_expense with the most appropriate category.
7. Amounts are MYR. Never modify the stated amount.
8. If month_tag is not stated, ask the user to clarify before calling any write tool.
9. Duplicate guard: if record_income returns a message starting with "⚠️", relay the warning to the user and wait for confirmation. If the user replies 'yes' or 'confirm', call record_income again with force=True.
10. Undo: if user says 'undo', 'delete last', or similar → call delete_last_transaction.
11. When displaying tool output (reports, balances, rent status), present the text exactly as returned — do not reformat into markdown bullets or headers.

## Input examples
"May26 C1613a R2 paid 700"       → record_income("C1613A", "R2", 700.0, "May26")
"Jun26 C1613a TNB 207.8"         → record_expense("C1613A", 207.8, "utility", "TNB electricity", "Jun26")
"Who hasn't paid May26?"          → get_rent_status("May26")
"Balance for C1613a"              → get_balance("C1613A")
"May26 balance for C1613a"        → get_balance("C1613A", "May26")
"What's my overall balance?"      → get_balance("")
"Overall balance for May26"       → get_balance("", "May26")
"May26 report C1613a"             → get_monthly_report("May26", "C1613A")
"undo" / "delete last"            → delete_last_transaction()
"""


# E: Cache the static prompt — built once at import time, not on every call
_STATIC_PROMPT = _build_static_prompt()


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

agent = Agent(
    GoogleModel(GEMINI_MODEL, provider=GoogleProvider(api_key=os.environ["GEMINI_API_KEY"])),
    deps_type=AgentDeps,
    output_type=str,
)


@agent.system_prompt
async def system_prompt(ctx: RunContext[AgentDeps]) -> str:
    return _STATIC_PROMPT + f"\nToday's date: {ctx.deps.current_date}"


# ---------------------------------------------------------------------------
# Finance tools
# ---------------------------------------------------------------------------

@agent.tool
async def record_income(
    ctx: RunContext[AgentDeps],
    property_code: str,
    room_id: str,
    amount: float,
    month_tag: str,
    description: str = "",
    force: bool = False,
) -> str:
    """Record a rent payment (income) from a tenant.

    If a rental entry already exists for the same property/room/month, returns a
    warning string starting with '⚠️'. Call again with force=True after user confirms.
    """
    prop = property_code.upper()
    room = room_id.upper() if room_id else ""

    # A: Duplicate guard
    if not force:
        try:
            if ctx.deps.sheets.has_rental_entry(prop, room, month_tag):
                loc = f"{prop} {room}".strip()
                return (
                    f"⚠️ A rental payment for {loc} in {month_tag} is already recorded. "
                    f"Reply 'yes' to record again anyway."
                )
        except Exception:
            pass  # if check fails, allow the write

    row = {
        "date": ctx.deps.current_date,
        "month_tag": month_tag,
        "property_code": prop,
        "room_id": room,
        "category": "rental",
        "description": description or f"{room} Rental".strip(),
        "amount": amount,
        "type": "income",
    }
    try:
        ctx.deps.sheets.append_transaction(row)
        loc = f"{prop} {room}".strip()
        return f"Recorded RM {amount:,.2f} rental income — {loc} for {month_tag}"
    except Exception as e:
        return f"Failed to record income: {e}"


@agent.tool
async def record_expense(
    ctx: RunContext[AgentDeps],
    property_code: str,
    amount: float,
    category: str,
    description: str,
    month_tag: str,
    room_id: str = "",
) -> str:
    """Record a property expense (utility, maintenance, loan, etc.)."""
    row = {
        "date": ctx.deps.current_date,
        "month_tag": month_tag,
        "property_code": property_code.upper(),
        "room_id": room_id.upper() if room_id else "",
        "category": category,
        "description": description,
        "amount": amount,
        "type": "expense",
    }
    try:
        ctx.deps.sheets.append_transaction(row)
        return f"Recorded RM {amount:,.2f} {category} expense — {property_code.upper()} for {month_tag}"
    except Exception as e:
        return f"Failed to record expense: {e}"


# ---------------------------------------------------------------------------
# Report tools
# ---------------------------------------------------------------------------

@agent.tool
async def get_rent_status(
    ctx: RunContext[AgentDeps],
    month_tag: str,
    property_code: str = "",
) -> str:
    """Check which active tenants have/haven't paid rent for a billing month."""
    tenants = ctx.deps.sheets.get_active_tenants(property_code or None)
    transactions = ctx.deps.sheets.get_transactions_by_month(month_tag, property_code or None)

    paid_keys = {
        f"{t['property_code'].upper()}:{t.get('room_id', '').upper()}"
        for t in transactions
        if t.get("category") == "rental" and t.get("type") == "income"
    }

    paid, unpaid = [], []
    for t in tenants:
        key = f"{t['property_code'].upper()}:{t.get('room_id', '').upper()}"
        name = t.get("tenant_name", "Unknown")
        loc = f"{t['property_code']} {t.get('room_id', '')}".strip()
        rent = float(t.get("monthly_rent", 0))
        if key in paid_keys:
            paid.append(f"  {name} ({loc}) — RM {rent:,.2f}")
        else:
            unpaid.append(f"  {name} ({loc}) — RM {rent:,.2f} UNPAID")

    if not tenants:
        return (
            f"Rent status for {month_tag}: No active tenants found. "
            "Populate the Tenants tab in the spreadsheet first."
        )

    lines = [f"Rent status for {month_tag}:"]
    if unpaid:
        lines += ["", "Unpaid:"] + unpaid
    if paid:
        lines += ["", "Paid:"] + paid
    return "\n".join(lines)


@agent.tool
async def get_balance(
    ctx: RunContext[AgentDeps],
    property_code: str = "",
    month_tag: str = "",
) -> str:
    """Get net balance (income minus expenses) for a property or all properties.

    Pass month_tag to filter to a single billing month.
    Omit property_code to get a per-property breakdown across all properties.
    """
    month_label = f" — {month_tag}" if month_tag else " (all-time)"

    if property_code:
        prop = property_code.upper()
        if month_tag:
            txns = ctx.deps.sheets.get_transactions_by_month(month_tag, prop)
        else:
            txns = ctx.deps.sheets.get_transactions_by_property(prop)
        income = sum(float(t.get("amount", 0)) for t in txns if t.get("type") == "income")
        expenses = sum(float(t.get("amount", 0)) for t in txns if t.get("type") == "expense")
        net = income - expenses
        return (
            f"Balance — {prop}{month_label}:\n"
            f"  Income:   RM {income:,.2f}\n"
            f"  Expenses: RM {expenses:,.2f}\n"
            f"  Net:      RM {net:,.2f}"
        )

    # All properties — per-property breakdown + grand total
    all_txns = ctx.deps.sheets.get_all_transactions()
    if month_tag:
        all_txns = [t for t in all_txns if t.get("month_tag", "").lower() == month_tag.lower()]

    grand_income = grand_expense = 0.0
    lines = [f"Balance — all properties{month_label}:", ""]

    for prop_code in PROPERTIES:
        txns = [t for t in all_txns if t.get("property_code", "").upper() == prop_code]
        inc = sum(float(t.get("amount", 0)) for t in txns if t.get("type") == "income")
        exp = sum(float(t.get("amount", 0)) for t in txns if t.get("type") == "expense")
        if inc == 0 and exp == 0:
            continue  # skip properties with no activity yet
        net = inc - exp
        grand_income += inc
        grand_expense += exp
        flag = " ▼" if net < 0 else ""
        lines.append(f"  {prop_code:<14}  RM {net:>10,.2f}{flag}")

    grand_net = grand_income - grand_expense
    lines += [
        "",
        f"  {'TOTAL':<14}  RM {grand_net:>10,.2f}",
        "",
        f"  Income total:    RM {grand_income:,.2f}",
        f"  Expenses total:  RM {grand_expense:,.2f}",
    ]
    return "\n".join(lines)


@agent.tool
async def get_monthly_report(
    ctx: RunContext[AgentDeps],
    month_tag: str,
    property_code: str = "",
) -> str:
    """Generate a monthly summary: income line items, expense breakdown, unpaid tenants."""
    transactions = ctx.deps.sheets.get_transactions_by_month(month_tag, property_code or None)
    tenants = ctx.deps.sheets.get_active_tenants(property_code or None)

    income_txns = [t for t in transactions if t.get("type") == "income"]
    expense_txns = [t for t in transactions if t.get("type") == "expense"]
    total_income = sum(float(t.get("amount", 0)) for t in income_txns)
    total_expense = sum(float(t.get("amount", 0)) for t in expense_txns)
    net = total_income - total_expense

    scope = f" — {property_code.upper()}" if property_code else ""
    lines = [f"Monthly Report — {month_tag}{scope}", ""]

    # Income line items
    if income_txns:
        lines.append("Income:")
        for t in income_txns:
            desc = t.get("description") or f"{t.get('room_id', '')} Rental".strip()
            lines.append(f"  {desc:<24}  RM {float(t.get('amount', 0)):>8,.2f}")
        lines.append(f"  {'TOTAL':<24}  RM {total_income:>8,.2f}")
    else:
        lines.append("  Income:   RM 0.00")

    lines.append("")

    # Expense breakdown by category
    if expense_txns:
        lines.append("Expenses:")
        by_cat: dict[str, float] = {}
        for t in expense_txns:
            cat = t.get("category", "misc")
            by_cat[cat] = by_cat.get(cat, 0) + float(t.get("amount", 0))
        for cat, amt in sorted(by_cat.items()):
            lines.append(f"  {cat:<24}  RM {amt:>8,.2f}")
        lines.append(f"  {'TOTAL':<24}  RM {total_expense:>8,.2f}")
    else:
        lines.append("  Expenses: RM 0.00")

    lines += ["", f"  Net:  RM {net:,.2f}", ""]

    # Unpaid tenants
    paid_keys = {
        f"{t['property_code'].upper()}:{t.get('room_id', '').upper()}"
        for t in income_txns
        if t.get("category") == "rental"
    }
    unpaid_tenants = [
        t for t in tenants
        if f"{t['property_code'].upper()}:{t.get('room_id', '').upper()}" not in paid_keys
    ]
    if unpaid_tenants:
        lines.append(f"Unpaid rent ({len(unpaid_tenants)}):")
        for t in unpaid_tenants:
            name = t.get("tenant_name", "Unknown")
            loc = f"{t['property_code']} {t.get('room_id', '')}".strip()
            rent = float(t.get("monthly_rent", 0))
            lines.append(f"  {name} ({loc}) — RM {rent:,.2f}")
    elif tenants:
        lines.append("All tenants paid. ✓")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Undo tool
# ---------------------------------------------------------------------------

@agent.tool
async def delete_last_transaction(ctx: RunContext[AgentDeps]) -> str:
    """Delete the most recently written row in the Transactions sheet (undo last entry)."""
    try:
        deleted = ctx.deps.sheets.delete_last_row()
        if deleted is None:
            return "Nothing to delete — Transactions sheet is empty."
        desc = deleted.get("description", "")
        amt = deleted.get("amount", "")
        prop = deleted.get("property_code", "")
        month = deleted.get("month_tag", "")
        return f"Deleted: {desc} RM {amt} — {prop} {month}".strip()
    except Exception as e:
        return f"Failed to delete last entry: {e}"
