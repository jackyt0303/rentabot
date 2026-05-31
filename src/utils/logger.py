"""
Debug logger for RentaBot.

Always writes to two destinations:
  1. File   — `rentabot.log` by default (change via LOG_FILE env var)
  2. Stdout — captured by systemd journal on the VM (visible via journalctl)
              and printed to the terminal when running locally

To raise the log level to INFO only: set LOG_LEVEL=INFO in .env.

On the VM:
    journalctl -u rentabot -f          # live structured logs
    tail -f ~/rentabot/rentabot.log    # same logs in the file
"""

import logging
import os
import sys


def _setup() -> logging.Logger:
    logger = logging.getLogger("rentabot")
    if logger.handlers:
        return logger  # already configured (e.g. in tests)

    level = getattr(logging, os.getenv("LOG_LEVEL", "DEBUG").upper(), logging.DEBUG)
    logger.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s; %(module)s; %(funcName)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler 1: file — persistent log history survives reboots
    log_file = os.getenv("LOG_FILE", "rentabot.log")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Handler 2: stdout — captured by systemd journal on the VM,
    # printed to terminal when running locally via main.py or bot.py
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


log = _setup()
