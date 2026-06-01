# RentaBot

RentaBot is an AI-assisted rental operations bot.

The production interface is Telegram (`bot.py`), deployed on a Google Cloud e2-micro VM. The CLI (`main.py`) remains in the project as a local developer/testing interface.

This repository showcases the project architecture, tool design, and test strategy without exposing private operational details.

## Highlights

- Telegram-first workflow for day-to-day rental operations
- Production runtime on Google Cloud e2-micro (cost-efficient always-on host)
- CLI retained for local developer testing and debugging
- Google Sheets integration for lightweight record storage
- Gemini-powered intent understanding via `pydantic-ai`
- Duplicate-safe transaction handling with confirmation override
- Async-friendly Python architecture with test coverage across tools and integrations

## Architecture Overview

```text
Telegram User Input
    -> Agent Layer (intent parsing + tool selection)
    -> Domain Tools (income/expense/status/report/undo)
    -> Sheets Client (read/write abstraction)
    -> Google Sheets backend
```

Local developer flow uses the same agent/tool stack through the CLI entry point for rapid iteration and validation.

## Runtime Modes

### 1) Production Bot (Primary)

- Entry point: `bot.py`
- Interface: Telegram input/output
- Deployment target: Google Cloud VM (e2-micro)
- Intended users: daily operational usage

### 2) Local CLI (Developer Support)

- Entry point: `main.py`
- Interface: terminal CLI
- Intended users: developer testing, troubleshooting, and local validation
- Not the primary production interface

## Repository Structure

```text
.
|- bot.py
|- main.py
|- src/
|  |- agents/
|  |  |- deps.py
|  |  |- rentabot.py
|  |- sheets/
|  |  |- client.py
|  |- utils/
|  |  |- logger.py
|  |- config.py
|- tests/
|  |- test_sheets.py
|  |- test_tools.py
|  |- test_llm.py
|- requirements.txt
|- README_PRIVATE.md
```

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies from `requirements.txt`.
3. Add your Google technical user credential file (service account JSON) to `credentials/`.
    - Example: `credentials/your-service-account.json`
    - Set `SERVICE_ACCOUNT_PATH` in `.env` to that file path.
4. Choose runtime:
    - Production-style local run (Telegram): `python bot.py`
    - Developer local interface (CLI): `python main.py`

## Testing

- Unit tests: `pytest -m unit`
- LLM routing tests: `pytest -m llm`
- Full test suite: `pytest`

## Security and Privacy

- Private operational data and deployment specifics are intentionally excluded from this public README.
- The previous detailed documentation is retained locally as `README_PRIVATE.md` and excluded from Git tracking.

## Notes

If you are evaluating this project for collaboration or hiring, the implementation demonstrates:

- practical LLM tool-routing design,
- clean separation between agent logic and storage client,
- testable async code patterns,
- and production-minded guardrails for financial record updates.
