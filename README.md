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
└── tests/
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

## Running Tests

```bash
pytest
```
