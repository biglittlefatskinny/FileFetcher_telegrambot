"""Structured logging setup for Open Sneak Bot.

Set OPEN_SNEAK_JSON_LOGS=1 to emit newline-delimited JSON suitable for
log aggregators (Loki, Datadog, CloudWatch, etc.).
"""
from __future__ import annotations

import json
import logging
from typing import Any


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            obj["stack"] = self.formatStack(record.stack_info)
        return json.dumps(obj, ensure_ascii=False)


def setup(level: str = "INFO", json_logs: bool = False) -> None:
    handler = logging.StreamHandler()
    if json_logs:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)

    # Silence chatty third-party loggers
    for name in ("httpx", "httpcore", "telegram", "playwright"):
        logging.getLogger(name).setLevel(logging.WARNING)
