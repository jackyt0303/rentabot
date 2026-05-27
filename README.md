# RentaBot — AI Rental Property Manager

A conversational CLI tool that lets a Malaysian landlord manage multiple rental properties using plain-language commands. RentaBot uses **Google Gemini** (via `pydantic-ai`) as its reasoning engine and **Google Sheets** as the data store — no dedicated database required.

---

## Features

- Record rent payments and flag duplicates with a confirmation flow
- Record property expenses (utilities, maintenance, loan, WiFi, tax, etc.)
- Query rent-payment status for a billing month
- View income/expense balances per property or across the whole portfolio
- Generate a monthly income/expense report for any property
- Undo the last transaction
- Persistent conversation history within a session (auto-trimmed)
- Duplicate-safe writes with `force=True` override

---

## Managed Properties

| Code | Address | Type |
|---|---|---|
| `C1613A` | C-16-13A | Multi-room (R1, R2, R3, Studio) |
| `SA1903A` | SA-19-03A | Multi-room (R1–R4) |
| `C03A09` | C-03A-09 | Multi-room (R1–R4) |
| `C1811` | C-18-11 | Multi-room (R1–R4) |
| `SV2` | SV2 | Whole-unit |
| `IRIS` | Iris | Whole-unit |
| `ISKANDARSHOP` | Iskandar Shop | Whole-unit |

---

## Tech Stack

| Component | Library |
|---|---|
| LLM agent | `pydantic-ai[google]` |
| Language model | Google Gemini (configured in `src/config.py`) |
| Spreadsheet backend | `gspread` + Google Sheets API |
| Authentication | `google-auth` service account |
| Environment config | `python-dotenv` |

---

## Prerequisites

- Python 3.11+
- A Google Cloud service account with **Sheets + Drive** API access
- The service account JSON key file placed in `credentials/`
- The service account email shared as **Editor** on your Google Sheets spreadsheet
- A Gemini API key from [Google AI Studio](https://aistudio.google.com)

---

## Setup

### 1. Clone and create a virtual environment

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `example.env` to `.env` and fill in your values:

```bash
cp example.env .env
```

```env
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_SHEETS_ID=your_google_spreadsheet_id
SERVICE_ACCOUNT_PATH=credentials/your-service-account.json
```

| Variable | Description |
|---|---|
| `GEMINI_API_KEY` | API key from Google AI Studio |
| `GOOGLE_SHEETS_ID` | The ID portion of your Google Sheet URL |
| `SERVICE_ACCOUNT_PATH` | Relative path to your service account JSON key |

### 4. Google Sheets structure

Your spreadsheet must have three tabs named exactly:

| Tab | Purpose |
|---|---|
| `Transactions` | All income and expense rows written by RentaBot |
| `Tenants` | Active tenant list (property, room, monthly rent) |
| `Config` | Optional configuration rows |

---

## Running RentaBot

### Test API connectivity

Verifies that environment variables are set, the Google Sheets connection works, and the Gemini API responds.

```bash
python main.py --test-connection
```

### Start the interactive CLI

```bash
python main.py
```

Type a message at the `You:` prompt. Type `exit`, `quit`, or `q` to end the session.

---

## Example Commands

```
# Record a rent payment
May26 C1613a R2 paid 700

# Record a utility bill
Jun26 C1613a TNB 207.80

# Check who hasn't paid for a month
Who hasn't paid May26?

# Balance for a single property
Balance for C1613a

# Balance for a property in a specific month
May26 balance for C1613a

# Overall portfolio balance
What's my overall balance?

# Monthly report for a property
May26 report C1613a

# Undo the last transaction
undo
```

Property codes and room IDs are **case-insensitive** — `c1613a`, `C1613A`, and `1613a` are all accepted.

---

## Project Structure

```
.
├── main.py                  # CLI entry point
├── requirements.txt
├── example.env
├── credentials/             # Service account JSON key (git-ignored)
├── src/
│   ├── config.py            # Property registry, expense categories, model name
│   ├── agents/
│   │   ├── deps.py          # AgentDeps dataclass (sheets client, current date)
│   │   └── rentabot.py      # pydantic-ai agent + all 5 tools
│   ├── sheets/
│   │   └── client.py        # Google Sheets CRUD wrapper (gspread)
│   └── utils/
│       ├── colors.py        # Terminal colour helpers
│       └── logger.py        # Logging setup
├── tests/
│   ├── conftest.py          # Shared fixtures (mock_sheets, agent_deps, sample data)
│   ├── test_sheets.py       # SheetsClient unit tests (gspread mocked)
│   ├── test_tools.py        # Agent tool unit tests (no LLM)
│   └── test_llm.py          # LLM routing tests (real Gemini API)
└── pytest.ini               # asyncio_mode=auto, unit/llm marks
```

---

## Expense Categories

| Keyword examples | Category |
|---|---|
| TNB, electricity | `utility` |
| maintenance, sinking fund | `maintenance` |
| wifi, internet | `wifi` |
| loan | `loan` |
| cukai, quit rent | `tax` |
| cleaning | `cleaning` |
| commission, agent fee | `fees` |
| rent payment | `rental` |

---

## Logging

Runtime logs are written to the console (and optionally a log file, depending on `src/utils/logger.py`). The log level is `DEBUG` for tool/LLM calls and `INFO` for user messages and token usage.

---

## Testing

### Quick reference

```bash
# Code-only unit tests — no credentials or network required (~4 s)
pytest -m unit

# LLM routing tests — requires real GEMINI_API_KEY in .env
pytest -m llm

# Everything
pytest
```

### Test file map

| File | Mark | What it tests |
|---|---|---|
| `tests/test_sheets.py` | `unit` | `SheetsClient` — all read/write methods with gspread mocked at the boundary |
| `tests/test_tools.py` | `unit` | All 6 agent tool functions called directly with a mock `RunContext` (no LLM) |
| `tests/test_llm.py` | `llm` | Real Gemini agent — asserts correct tool routing and parameter extraction for 5 prompt patterns |
| `tests/conftest.py` | — | Shared fixtures: `sample_tenants`, `sample_transactions`, `mock_sheets`, `agent_deps`, `mock_ctx` |

### Coverage at a glance

**SheetsClient** (`test_sheets.py`)

| Method | Scenarios covered |
|---|---|
| `has_rental_entry` | Match found · No match · Case-insensitive · Tab missing |
| `get_active_tenants` | Active-only filter · Property filter · Exception → `[]` |
| `get_transactions_by_month` | No property filter · With property · Unknown month |
| `append_transaction` | Correct column order · Auto-creates tab + headers |
| `delete_last_row` | Returns dict · Header-only sheet → `None` · Tab missing → `None` |
| `get_all_transactions` | Fans out across all properties · Skips missing tabs |

**Agent tools** (`test_tools.py`)

| Tool | Scenarios covered |
|---|---|
| `record_income` | Happy path · Duplicate/`force=False` (blocked) · Duplicate/`force=True` (allowed) · Lowercase normalisation · Bare-digit room prefix |
| `record_expense` | Happy path (correct `type`, category, property) |
| `get_rent_status` | All paid · Mixed · Property filter forwarded · No active tenants |
| `get_balance` | Single property · Single + `month_tag` · All properties breakdown · Empty data |
| `get_monthly_report` | Single property · All tenants paid · Empty data |
| `delete_last_transaction` | Success with description · Nothing to delete · `property_code` uppercased |

**LLM routing** (`test_llm.py`)

| Prompt pattern | Expected tool |
|---|---|
| `record RM800 rent for room R1 at C1613A for May26` | `record_income` |
| `Who hasn't paid rent at C1613A for May26?` | `get_rent_status` |
| `What's the balance for SA1903A?` | `get_balance` |
| `TNB bill RM120 at C1613A for May26` | `record_expense` (category=utility) |
| `undo last transaction at C1613A` | `delete_last_transaction` |

### Adding tests for a new tool

1. **Code-only test** — add an `async def test_<tool>_*` section in `tests/test_tools.py` following the `mock_ctx` pattern. Import the function at the top.
2. **LLM routing test** — add a `@pytest.mark.llm` test in `tests/test_llm.py` using `_find_tool_call()`.
3. **Update both COVERAGE MAP docstrings** at the top of those files.
4. **Update the table above** in this README.

### Adding tests for a new SheetsClient method

1. Add a test section in `tests/test_sheets.py` using the `client_and_sheet` fixture.
2. Configure `mock_spreadsheet.worksheet(...)` return values for your scenario.
3. Update the COVERAGE MAP docstring and the README table above.
