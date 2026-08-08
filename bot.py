"""
RentaBot — Telegram entry point.

Usage:
    python bot.py

Reuses src/agents/rentabot.py, src/agents/deps.py, and src/sheets/client.py
unchanged. The CLI (main.py) continues to work independently.

Security:
    Only responds to the Telegram user ID in TELEGRAM_ALLOWED_USER_ID (.env).
    All other senders are silently rejected.

History:
    In-memory per user_id. Same MAX_HISTORY / _safe_trim logic as the CLI.
    History is cleared on unknown errors; preserved on rate-limit errors.
"""

import os
import re
import sys
from dataclasses import dataclass, field

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.agents.deps import AgentDeps
from src.agents.rentabot import agent, record_expense, record_income
from src.importers import ClassifiedTx, NormalizedTx, WhatsAppSignal
from src.importers.cimb_parser import parse as parse_cimb
from src.importers.classifier import classify_batch
from src.importers.mypb_parser import parse as parse_mypb
from src.importers.whatsapp_parser import parse as parse_wa
from src.sheets.client import SheetsClient
from src.utils.logger import log

# ---------------------------------------------------------------------------
# Per-user conversation history & import session state
# ---------------------------------------------------------------------------

_histories: dict[int, list] = {}
MAX_HISTORY = 10  # ~3-5 exchanges — match CLI setting


@dataclass
class ImportSession:
    files: list[tuple[str, bytes]] = field(default_factory=list)
    pending: dict[str, list[ClassifiedTx]] = field(default_factory=dict)
    excluded_count: int = 0
    duplicate_count: int = 0
    awaiting_edit_prop: str | None = None
    awaiting_edit_idx: int | None = None
    awaiting_payee: ClassifiedTx | None = None
    unprocessed_unknowns: list[ClassifiedTx] = field(default_factory=list)


_import_sessions: dict[int, ImportSession] = {}


def _safe_trim(history: list) -> list:
    """Trim to MAX_HISTORY at a clean user-message boundary."""
    if len(history) <= MAX_HISTORY:
        return history
    candidate = history[-MAX_HISTORY:]
    for i, msg in enumerate(candidate):
        parts = getattr(msg, "parts", [])
        if parts and type(parts[0]).__name__ == "UserPromptPart":
            trimmed = candidate[i:]
            log.debug(f"TRIM  history {len(history)} → {len(trimmed)} (boundary at index {i})")
            return trimmed
    log.warning("TRIM  no safe boundary found, clearing history")
    return []


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    log.info(f"CMD   /start  user_id={user.id} username={user.username!r}")
    await update.message.reply_text(
        "RentaBot ready.\n\n"
        "Single operations:\n"
        "  May26 C1613a R2 paid 700\n"
        "  Jun26 C1613a TNB 207.8\n"
        "  Who hasn't paid May26?\n"
        "  Balance for C1613a\n\n"
        "Bulk import:\n"
        "  Upload CIMB CSV, MyPB CSV, or WhatsApp .txt file, then type 'process'.\n\n"
        "Send /clear to reset history and import buffer."
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed_id = int(os.environ["TELEGRAM_ALLOWED_USER_ID"])
    user_id = update.effective_user.id
    log.info(f"CMD   /clear  user_id={user_id}")
    if user_id != allowed_id:
        log.warning(f"CMD   /clear rejected — unauthorized user_id={user_id}")
        return
    _histories.pop(user_id, None)
    _import_sessions.pop(user_id, None)
    log.info(f"CMD   /clear  history and import session wiped for user_id={user_id}")
    await update.message.reply_text("History and import buffer cleared.")


# ---------------------------------------------------------------------------
# Document / File upload handler
# ---------------------------------------------------------------------------

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed_id = int(os.environ["TELEGRAM_ALLOWED_USER_ID"])
    user_id = update.effective_user.id
    if user_id != allowed_id:
        log.warning(f"Rejected document from unauthorized user_id={user_id}")
        return

    doc = update.message.document
    if not doc:
        return

    filename = doc.file_name or "uploaded_file"
    ext = filename.lower().split(".")[-1]
    if ext not in ("csv", "txt"):
        await update.message.reply_text(f"Unsupported file type '{filename}'. Please send CSV or .txt files.")
        return

    file_obj = await context.bot.get_file(doc.file_id)
    content = await file_obj.download_as_bytearray()

    session = _import_sessions.setdefault(user_id, ImportSession())
    session.files.append((filename, bytes(content)))

    log.info(f"FILE  recv user_id={user_id} filename={filename!r} total={len(session.files)}")
    await update.message.reply_text(
        f"Received '{filename}'. ({len(session.files)} file(s) buffered).\n"
        f"Send more files or type 'process' to begin classification."
    )


# ---------------------------------------------------------------------------
# Import Pipeline Helpers
# ---------------------------------------------------------------------------

async def run_import_pipeline(user_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _import_sessions.get(user_id)
    if not session or not session.files:
        await update.message.reply_text("No files buffered for import. Please upload CSV or .txt files first.")
        return

    await update.message.reply_text(f"Processing {len(session.files)} file(s)... Please wait.")

    sheets: SheetsClient = context.bot_data["sheets"]
    deps = AgentDeps(sheets=sheets)

    parsed_txns: list[NormalizedTx] = []
    wa_signals: list[WhatsAppSignal] = []

    for fn, content in session.files:
        fn_lower = fn.lower()
        if "cimb" in fn_lower:
            parsed_txns.extend(parse_cimb(content))
        elif "mypb" in fn_lower or "public" in fn_lower or "statement" in fn_lower:
            parsed_txns.extend(parse_mypb(content))
        elif fn_lower.endswith(".csv"):
            # Fallback CSV check header
            try:
                parsed_txns.extend(parse_cimb(content))
            except Exception:
                parsed_txns.extend(parse_mypb(content))
        elif fn_lower.endswith(".txt"):
            wa_signals.extend(parse_wa(content))

    session.files.clear()

    if not parsed_txns:
        await update.message.reply_text("No transactions found in the uploaded CSV files.")
        return

    classified = await classify_batch(parsed_txns, wa_signals, sheets, agent)

    session.pending.clear()
    session.excluded_count = sum(1 for c in classified if c.tx_type == "personal")
    session.duplicate_count = sum(1 for c in classified if c.tx_type == "duplicate")

    unknowns = [c for c in classified if c.tx_type == "unknown"]
    session.unprocessed_unknowns = unknowns

    for c in classified:
        if c.tx_type in ("income", "expense") and c.property_code:
            prop = c.property_code.upper()
            session.pending.setdefault(prop, []).append(c)

    if session.unprocessed_unknowns:
        await ask_next_unknown(user_id, update, context)
    else:
        await send_batch_previews(user_id, update, context)


async def ask_next_unknown(user_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _import_sessions.get(user_id)
    if not session or not session.unprocessed_unknowns:
        await send_batch_previews(user_id, update, context)
        return

    unknown = session.unprocessed_unknowns.pop(0)
    session.awaiting_payee = unknown

    msg = (
        f"❓ Unknown transaction found:\n"
        f"  Date: {unknown.date}\n"
        f"  Payee: {unknown.payee}\n"
        f"  Narration: {unknown.narration}\n"
        f"  Amount: RM {unknown.amount:,.2f} ({'Income' if unknown.direction == 'in' else 'Expense'})\n\n"
        f"Reply with property & room (e.g., 'C1613A R2' or 'SA1903A'), 'personal', or 'skip':"
    )
    await update.message.reply_text(msg)


async def format_and_send_property_preview(prop_code: str, txns: list[ClassifiedTx], update: Update) -> None:
    lines = [f"📋 Property Batch — {prop_code.upper()} ({len(txns)} transactions):", ""]

    income_txns = [t for t in txns if t.tx_type == "income"]
    expense_txns = [t for t in txns if t.tx_type == "expense"]

    if income_txns:
        lines.append("Income:")
        for idx, t in enumerate(income_txns, start=1):
            room = f" {t.room_id}" if t.room_id else ""
            lines.append(f"  {idx}. {t.payee:<20} {room:<4}  RM {t.amount:>8,.2f}  [{t.month_tag}]")
            if t.reasoning:
                lines.append(f"     └ {t.reasoning}")

    if expense_txns:
        if income_txns:
            lines.append("")
        lines.append("Expenses:")
        for idx, t in enumerate(expense_txns, start=len(income_txns) + 1):
            cat = f"[{t.category}]" if t.category else ""
            lines.append(f"  {idx}. {t.narration[:22]:<22} {cat:<10} RM {t.amount:>8,.2f}  [{t.month_tag}]")
            if t.reasoning:
                lines.append(f"     └ {t.reasoning}")

    lines += [
        "",
        f"Reply: CONFIRM {prop_code.upper()}  |  EDIT <item_no>  |  SKIP {prop_code.upper()}"
    ]
    await update.message.reply_text("\n".join(lines))


async def send_batch_previews(user_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _import_sessions.get(user_id)
    if not session or not session.pending:
        summary = ["Import classification complete. No pending transactions to record."]
        if session:
            if session.excluded_count > 0:
                summary.append(f"⊘ Personal transactions excluded: {session.excluded_count}")
            if session.duplicate_count > 0:
                summary.append(f"⊘ Already-imported transactions skipped: {session.duplicate_count}")
        await update.message.reply_text("\n".join(summary))
        _import_sessions.pop(user_id, None)
        return

    for prop_code, txns in session.pending.items():
        await format_and_send_property_preview(prop_code, txns, update)

    total_pending = sum(len(v) for v in session.pending.values())
    summary = [
        f"Total pending: {total_pending} transactions across {len(session.pending)} property tab(s).",
    ]
    if session.excluded_count > 0:
        summary.append(f"⊘ Personal transactions excluded: {session.excluded_count}")
    if session.duplicate_count > 0:
        summary.append(f"⊘ Already-imported transactions skipped: {session.duplicate_count}")

    await update.message.reply_text("\n".join(summary))


# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed_id = int(os.environ["TELEGRAM_ALLOWED_USER_ID"])
    user_id = update.effective_user.id

    if user_id != allowed_id:
        log.warning(f"Rejected message from unauthorized user_id={user_id}")
        return

    text = (update.message.text or "").strip()
    if not text:
        log.debug(f"MSG   empty message ignored from user_id={user_id}")
        return

    log.info(f"MSG   recv  user_id={user_id}  text={text!r}")

    session = _import_sessions.get(user_id)

    # 1. Handle unknown payee response
    if session and session.awaiting_payee:
        unknown = session.awaiting_payee
        session.awaiting_payee = None
        sheets: SheetsClient = context.bot_data["sheets"]

        text_lower = text.lower()
        if "personal" in text_lower:
            sheets.upsert_personal_payee(unknown.payee, unknown.narration)
            session.excluded_count += 1
            await update.message.reply_text(f"Saved '{unknown.payee}' to PersonalPayees registry and excluded.")
        elif "skip" in text_lower:
            await update.message.reply_text(f"Skipped '{unknown.payee}'.")
        else:
            # Assume user gave property & room e.g. "C1613A R2"
            parts = text.upper().split()
            prop = parts[0] if parts else ""
            room = parts[1] if len(parts) > 1 else ""
            unknown.property_code = prop
            unknown.room_id = room
            unknown.tx_type = "income" if unknown.direction == "in" else "expense"
            unknown.category = "rental" if unknown.tx_type == "income" else "misc"
            sheets.upsert_payee(unknown.payee, prop, room, type_="tenant" if unknown.tx_type == "income" else "vendor")
            session.pending.setdefault(prop, []).append(unknown)
            await update.message.reply_text(f"Saved '{unknown.payee}' → {prop} {room} in Payees registry.")

        await ask_next_unknown(user_id, update, context)
        return

    # 2. Handle 'process' trigger for import session
    if session and session.files and "process" in text.lower():
        await run_import_pipeline(user_id, update, context)
        return

    # 3. Handle edit value reply
    if session and session.awaiting_edit_prop and session.awaiting_edit_idx is not None:
        prop = session.awaiting_edit_prop
        idx = session.awaiting_edit_idx
        session.awaiting_edit_prop = None
        session.awaiting_edit_idx = None

        txns = session.pending.get(prop, [])
        if 1 <= idx <= len(txns):
            target_tx = txns[idx - 1]
            # Parse corrections from text e.g. "2: Aug26 R3" or "Aug26 R3"
            clean_input = re.sub(r"^\d+:\s*", "", text).strip()
            tokens = clean_input.split()
            for tok in tokens:
                if tok.upper() in PROPERTIES:
                    target_tx.property_code = tok.upper()
                elif re.match(r"^[rR]\d+$", tok):
                    target_tx.room_id = tok.upper()
                elif re.match(r"^[A-Za-z]{3}\d{2}$", tok):
                    target_tx.month_tag = tok.capitalize()
                elif tok.replace(".", "", 1).isdigit():
                    target_tx.amount = float(tok)

            await update.message.reply_text(f"Updated item #{idx} for {prop}.")
            await format_and_send_property_preview(prop, txns, update)
        else:
            await update.message.reply_text(f"Invalid item index #{idx}.")
        return

    # 4. Handle CONFIRM / SKIP / EDIT commands for import session
    if session and session.pending:
        text_upper = text.upper()
        sheets: SheetsClient = context.bot_data["sheets"]
        deps = AgentDeps(sheets=sheets)

        if text_upper.startswith("CONFIRM"):
            parts = text_upper.split()
            prop_target = parts[1] if len(parts) > 1 else ""

            props_to_confirm = [prop_target] if prop_target in session.pending else list(session.pending.keys())

            for p in props_to_confirm:
                pending_txns = session.pending.pop(p, [])
                income_count = expense_count = 0
                for t in pending_txns:
                    if t.tx_type == "income":
                        await record_income(deps, t.property_code, t.room_id, t.amount, t.month_tag, t.narration, force=True)
                        income_count += 1
                    elif t.tx_type == "expense":
                        await record_expense(deps, t.property_code, t.amount, t.category or "misc", t.narration, t.month_tag, t.room_id)
                        expense_count += 1

                await update.message.reply_text(f"✓ Recorded {p}: {income_count} income, {expense_count} expense rows written to Sheets.")

            if not session.pending:
                _import_sessions.pop(user_id, None)
                await update.message.reply_text("All batches processed. Import complete! 🎉")
            return

        if text_upper.startswith("SKIP"):
            parts = text_upper.split()
            prop_target = parts[1] if len(parts) > 1 else ""
            if prop_target in session.pending:
                session.pending.pop(prop_target)
                await update.message.reply_text(f"Skipped {prop_target}.")
            if not session.pending:
                _import_sessions.pop(user_id, None)
                await update.message.reply_text("Import session ended.")
            return

        if text_upper.startswith("EDIT"):
            parts = text_upper.split()
            if len(parts) >= 2 and parts[1].isdigit():
                idx = int(parts[1])
                # Pick first property tab if not specified
                prop = list(session.pending.keys())[0]
                session.awaiting_edit_prop = prop
                session.awaiting_edit_idx = idx
                await update.message.reply_text(f"Editing item #{idx} for {prop}. Send correction (e.g. 'Aug26 R3' or '800'):")
                return

    # 5. Normal agent flow for text messages
    sheets: SheetsClient = context.bot_data["sheets"]
    deps = AgentDeps(sheets=sheets)

    chat_history = list(_histories.get(user_id, []))

    try:
        result = await agent.run(text, deps=deps, message_history=chat_history)
        new_msgs = result.new_messages()
        chat_history.extend(new_msgs)
        chat_history = _safe_trim(chat_history)
        _histories[user_id] = chat_history

        usage = result.usage
        log.info(
            f"USAGE input={usage.input_tokens} output={usage.output_tokens} "
            f"total={usage.total_tokens} | history={len(chat_history)} msgs"
        )
        await update.message.reply_text(result.output)

    except Exception as exc:
        err_str = str(exc)
        is_rate_limit = (
            "429" in err_str
            or "RESOURCE_EXHAUSTED" in err_str
            or "quota" in err_str.lower()
        )
        log.error(f"ERROR >> {exc}", exc_info=True)
        if is_rate_limit:
            await update.message.reply_text(
                "Daily quota reached. Try again later or switch models in config.py."
            )
        else:
            _histories.pop(user_id, None)
            await update.message.reply_text(
                f"Something went wrong. History cleared — please try again.\n{err_str.split(chr(10))[0]}"
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()

    required = [
        "GEMINI_API_KEY",
        "GOOGLE_SHEETS_ID",
        "SERVICE_ACCOUNT_PATH",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ALLOWED_USER_ID",
    ]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        log.error(f"STARTUP missing env vars: {', '.join(missing)}")
        print(f"ERROR  Missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    log.info("STARTUP connecting to Google Sheets...")
    sheets = SheetsClient(
        service_account_path=os.getenv("SERVICE_ACCOUNT_PATH"),
        spreadsheet_id=os.getenv("GOOGLE_SHEETS_ID"),
    )
    log.info(f"STARTUP Sheets client ready  spreadsheet_id={os.getenv('GOOGLE_SHEETS_ID')!r}")

    log.info("STARTUP building Telegram application...")
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    app.bot_data["sheets"] = sheets

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("STARTUP handlers registered: /start, /clear, file documents, text messages")

    log.info("=" * 60)
    log.info(f"STARTUP RentaBot Telegram bot running  allowed_user_id={os.getenv('TELEGRAM_ALLOWED_USER_ID')}  polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
