"""Structured JSON logging with context fields (notably `run_id`)
threaded through every module without an explicit parameter.

Any module logs with the stdlib `logging` module as usual. Whatever is
bound with `bind_context()` / `run_id_scope()` on the current async/thread
context is stamped onto every record emitted underneath it -- engine
calls, db access, HTTP handlers -- so a forecast run's `run_id` shows up
in every log line produced while that run executes, without engine/
(which must stay dependency-free and side-effect-free) knowing logging
exists.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "log_context", default=None
)

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message"}


@contextmanager
def bind_context(**fields: Any) -> Iterator[None]:
    """Merge `fields` into the logging context for the duration of the
    block, restoring the previous context on exit. Nestable."""
    token = _context.set({**(_context.get() or {}), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def run_id_scope(run_id: str) -> Any:
    """Bind `run_id` on the logging context for a forecast run's
    execution. Every log record emitted anywhere during the block
    carries `run_id` as a field."""
    return bind_context(run_id=run_id)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in (_context.get() or {}).items():
            setattr(record, key, value)
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Install JSON structured logging on the root logger.

    Idempotent -- safe to call from both the API's startup and the
    forecast job's entrypoint without accumulating duplicate handlers.
    """
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_ContextFilter())
    root.addHandler(handler)
