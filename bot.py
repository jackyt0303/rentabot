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
import sys

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
from src.agents.rentabot import agent
from src.sheets.client import SheetsClient
from src.utils.logger import log

# ---------------------------------------------------------------------------
# Per-user conversation history  (user_id → list of pydantic-ai messages)
# ---------------------------------------------------------------------------

_histories: dict[int, list] = {}
MAX_HISTORY = 10  # ~3-5 exchanges — match CLI setting


def _safe_trim(history: list) -> list:
    """Trim to MAX_HISTORY at a clean user-message boundary.

    Gemini rejects a history that ends with a ToolCallPart not immediately
    followed by its ToolReturnPart.  We scan forward from the candidate trim
    point to find the first clean user-message start so we never split a
    tool-call / tool-return pair.
    """
    if len(history) <= MAX_HISTORY:
        return history
    candidate = history[-MAX_HISTORY:]
    for i, msg in enumerate(candidate):
        parts = getattr(msg, "parts", [])
        if parts and type(parts[0]).__name__ == "UserPromptPart":
            trimmed = candidate[i:]
            log.debug(f"TRIM  history {len(history)} → {len(trimmed)} (boundary at index {i})")
            return trimmed
    # No safe boundary found — drop all rather than send a broken sequence
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
        "Examples:\n"
        "  May26 C1613a R2 paid 700\n"
        "  Jun26 C1613a TNB 207.8\n"
        "  Who hasn't paid May26?\n"
        "  Balance for C1613a\n\n"
        "Send /clear to reset conversation history."
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    allowed_id = int(os.environ["TELEGRAM_ALLOWED_USER_ID"])
    user_id = update.effective_user.id
    log.info(f"CMD   /clear  user_id={user_id}")
    if user_id != allowed_id:
        log.warning(f"CMD   /clear rejected — unauthorized user_id={user_id}")
        return
    prev_len = len(_histories.get(user_id, []))
    _histories.pop(user_id, None)
    log.info(f"CMD   /clear  history wiped ({prev_len} msgs) for user_id={user_id}")
    await update.message.reply_text("History cleared.")


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

    # AgentDeps is created fresh per message so _transaction_recorded resets
    sheets: SheetsClient = context.bot_data["sheets"]
    deps = AgentDeps(sheets=sheets)
    log.debug(f"DEPS  created  date={deps.current_date}")

    chat_history = list(_histories.get(user_id, []))  # shallow copy before mutation
    log.debug(f"HIST  before call  user_id={user_id}  msgs={len(chat_history)}")

    try:
        log.debug(f"AGENT run start  user_id={user_id}")
        result = await agent.run(text, deps=deps, message_history=chat_history)
        log.debug(f"AGENT run complete  user_id={user_id}")

        # Trace every message part the LLM exchanged (tool calls, tool returns, text)
        for msg in result.all_messages():
            msg_type = type(msg).__name__
            log.debug(f"MSG   {msg_type}: {msg}")

        new_msgs = result.new_messages()
        log.debug(f"HIST  new messages this turn: {len(new_msgs)}")
        chat_history.extend(new_msgs)
        chat_history = _safe_trim(chat_history)
        _histories[user_id] = chat_history
        log.debug(f"HIST  after trim  user_id={user_id}  msgs={len(chat_history)}")

        usage = result.usage
        log.info(
            f"USAGE input={usage.input_tokens} output={usage.output_tokens} "
            f"total={usage.total_tokens} | history={len(chat_history)} msgs"
        )
        log.info(f"MSG   send  user_id={user_id}  reply={result.output[:120]!r}")

        await update.message.reply_text(result.output)
        log.debug(f"MSG   reply delivered  user_id={user_id}")

    except Exception as exc:
        err_str = str(exc)
        is_rate_limit = (
            "429" in err_str
            or "RESOURCE_EXHAUSTED" in err_str
            or "quota" in err_str.lower()
        )
        log.error(f"ERROR >> {exc}", exc_info=True)
        if is_rate_limit:
            # History is still valid — preserve it
            log.warning("RATE LIMIT hit — history preserved")
            await update.message.reply_text(
                "Daily quota reached. Try again later or switch models in config.py."
            )
        else:
            # Unknown error — clear history to prevent cascading 400s
            _histories.pop(user_id, None)
            log.warning(f"HISTORY cleared after error for user_id={user_id}")
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
    log.info(f"STARTUP env vars OK: {', '.join(required)}")

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("STARTUP handlers registered: /start, /clear, text messages")

    log.info("=" * 60)
    log.info(f"STARTUP RentaBot Telegram bot running  allowed_user_id={os.getenv('TELEGRAM_ALLOWED_USER_ID')}  polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
