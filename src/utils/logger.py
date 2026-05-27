"""
Debug logger for RentaBot.

Writes to `rentabot.log` by default — never to the CLI.
To redirect to a different file: set the LOG_FILE env var.
To raise the log level to INFO only: set LOG_LEVEL=INFO.

Usage:
    from src.utils.logger import log
    log.debug("something happened")
    log.info("tool called: record_income")

To switch to a centralized log service later:
    Replace the FileHandler with any logging.Handler subclass.
"""

import logging
import os


def _setup() -> logging.Logger:
    logger = logging.getLogger("rentabot")
    if logger.handlers:
        return logger  # already configured (e.g. in tests)

    level = getattr(logging, os.getenv("LOG_LEVEL", "DEBUG").upper(), logging.DEBUG)
    logger.setLevel(level)

    log_file = os.getenv("LOG_FILE", "rentabot.log")
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            fmt="[%(asctime)s] - [%(module)s.%(funcName)s] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    return logger


log = _setup()
