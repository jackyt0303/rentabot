"""
RentaBot CLI entry point.
Usage:
    python main.py --test-connection    Test Sheets + Gemini API connectivity
    python main.py                      Interactive CLI
"""

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

from src.agents.deps import AgentDeps
from src.sheets.client import SheetsClient
from src.utils.colors import Colors, agent_print, print_header
from src.utils.logger import log


def test_connection():

    """Verify Google Sheets and Gemini API connectivity.
    command: python main.py --test-connection """
    load_dotenv()

    print_header("RentaBot — Connection Test")

    # 1. Check environment variables
    agent_print("SYSTEM", "Checking environment variables...", Colors.INFO)
    required_vars = ["GEMINI_API_KEY", "GOOGLE_SHEETS_ID", "SERVICE_ACCOUNT_PATH"]
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        agent_print("ERROR", f"Missing env vars: {', '.join(missing)}", Colors.ERROR)
        sys.exit(1)
    agent_print("SUCCESS", "All environment variables present", Colors.SUCCESS)

    # 2. Test Google Sheets connection
    agent_print("SYSTEM", "Testing Google Sheets connection...", Colors.INFO)
    try:
        sheets = SheetsClient(
            service_account_path=os.getenv("SERVICE_ACCOUNT_PATH"),
            spreadsheet_id=os.getenv("GOOGLE_SHEETS_ID"),
        )
        info = sheets.test_connection()
        agent_print("SUCCESS", f"Connected to: \"{info['title']}\"", Colors.SUCCESS)
        agent_print("SYSTEM", f"Tabs found: {', '.join(info['tabs'])}", Colors.INFO)
    except Exception as e:
        agent_print("ERROR", f"Sheets connection failed: {e}", Colors.ERROR)
        sys.exit(1)

    # 3. Test Gemini API
    agent_print("SYSTEM", "Testing Gemini API...", Colors.INFO)
    try:
        import httpx

        api_key = os.getenv("GEMINI_API_KEY")
        resp = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={api_key}",
            json={"contents": [{"parts": [{"text": "Reply with just: OK"}]}]},
            timeout=15,
        )
        if resp.status_code == 200:
            agent_print("SUCCESS", "Gemini API responding", Colors.SUCCESS)
        else:
            agent_print("ERROR", f"Gemini API error: {resp.status_code} — {resp.text[:200]}", Colors.ERROR)
            sys.exit(1)
    except Exception as e:
        agent_print("ERROR", f"Gemini API test failed: {e}", Colors.ERROR)
        sys.exit(1)

    print_header("All connections OK")


async def run_cli():
    """Interactive CLI loop — send messages to RentaBot."""
    load_dotenv()

    required_vars = ["GEMINI_API_KEY", "GOOGLE_SHEETS_ID", "SERVICE_ACCOUNT_PATH"]
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        agent_print("ERROR", f"Missing env vars: {', '.join(missing)}", Colors.ERROR)
        sys.exit(1)

    sheets = SheetsClient(
        service_account_path=os.getenv("SERVICE_ACCOUNT_PATH"),
        spreadsheet_id=os.getenv("GOOGLE_SHEETS_ID"),
    )
    deps = AgentDeps(sheets=sheets)

    from src.agents.rentabot import agent  # lazy import keeps startup fast

    print_header("RentaBot — Rental Property Manager")
    agent_print("SYSTEM", "Ready. Type your message or 'exit' to quit.", Colors.INFO)
    agent_print("SYSTEM", "Examples: 'May26 C1613a R2 paid 700'  |  'Balance for C1613a'", Colors.DIM)
    log.info("=" * 60)
    log.info("RentaBot session started")

    chat_history: list = []
    MAX_HISTORY = 10  # ~3-5 exchanges (each exchange = 2 messages minimum)

    def _safe_trim(history: list) -> list:
        """Trim to MAX_HISTORY but only at user-message boundaries.

        Gemini rejects a history that ends with a ToolCallPart not immediately
        followed by its ToolReturnPart. We scan forward from the trim point to
        find the first clean user-message start, so we never split a
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
        # No safe boundary found — drop all history rather than send a broken sequence
        log.warning("TRIM  no safe boundary found, clearing history")
        return []

    while True:
        try:
            user_input = input(f"\n{Colors.BOLD}You: {Colors.RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "bye", "q"):
            agent_print("SYSTEM", "Goodbye!", Colors.INFO)
            break

        try:
            log.info(f"USER  >> {user_input}")
            result = await agent.run(user_input, deps=deps, message_history=chat_history)

            # Accumulate history (new_messages excludes the system prompt so it
            # won't be duplicated when the next turn re-injects it via the decorator)
            chat_history.extend(result.new_messages())
            chat_history = _safe_trim(chat_history)

            # --- debug: tool calls and LLM metadata ---
            for msg in result.all_messages():
                msg_type = type(msg).__name__
                log.debug(f"MSG   {msg_type}: {msg}")
            usage = result.usage
            log.info(
                f"USAGE >> input={usage.input_tokens} output={usage.output_tokens} "
                f"total={usage.total_tokens} | history_msgs={len(chat_history)}"
            )
            log.info(f"BOT   >> {result.output}")
            # ------------------------------------------

            agent_print("RENTABOT", result.output, Colors.SUCCESS)
        except Exception as e:
            err_str = str(e)
            is_rate_limit = (
                "429" in err_str
                or "RESOURCE_EXHAUSTED" in err_str
                or "quota" in err_str.lower()
            )
            log.error(f"ERROR >> {e}", exc_info=True)
            if is_rate_limit:
                # History is still valid — rate limit is transient, preserve it
                log.warning("RATE LIMIT hit — history preserved")
                agent_print("ERROR", "Daily quota reached. Try again later or switch models in config.py.", Colors.ERROR)
            else:
                # Unknown error — clear history to prevent cascading 400s
                chat_history = []
                log.warning("HISTORY cleared after error")
                agent_print("ERROR", err_str.split("\n")[0], Colors.ERROR)


def main():
    parser = argparse.ArgumentParser(description="RentaBot — Rental Property Management")
    parser.add_argument("--test-connection", action="store_true", help="Test API connectivity")
    args = parser.parse_args()

    if args.test_connection:
        test_connection()
    else:
        asyncio.run(run_cli())


if __name__ == "__main__":
    main()
