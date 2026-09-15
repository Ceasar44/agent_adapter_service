"""Task-local correlation and structured application logs."""

import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

_context: ContextVar[dict[str, str]] = ContextVar("diagnostic_context", default={})
FIELDS = {"request_id", "thread_id", "run_id", "trace_id"}


def get_context() -> dict[str, str]:
    return dict(_context.get())


@contextmanager
def bind_context(**fields):
    if set(fields) - FIELDS:
        raise ValueError("Unsupported correlation field")
    safe = {
        key: value
        for key, value in fields.items()
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/+=-]{1,255}", value)
    }
    token = _context.set({**get_context(), **safe})
    try:
        yield
    finally:
        _context.reset(token)


class ContextFormatter(logging.Formatter):
    def __init__(self, json_output: bool = True):
        super().__init__()
        self.json_output = json_output

    def format(self, record: logging.LogRecord) -> str:
        # Arbitrary extras and exception text can contain input, SQL and credentials.
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **get_context(),
        }
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__
        if self.json_output:
            return json.dumps(payload, ensure_ascii=True)
        return " ".join(f"{key}={json.dumps(value)}" for key, value in payload.items())


def configure_logging(*, level: str = "INFO", json_output: bool = True, stream=None) -> None:
    """Idempotently configure our namespace without replacing host/root handlers.

    Messages must be static diagnostics; never pass user text, tokens or payloads.
    Third-party logging remains the host application's responsibility.
    """
    logger = logging.getLogger("agent_adapter_service")
    for handler in tuple(logger.handlers):
        if getattr(handler, "_adapter_handler", False):
            logger.removeHandler(handler)
            handler.close()
    handler = logging.StreamHandler(stream)
    handler._adapter_handler = True
    handler.setFormatter(ContextFormatter(json_output))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
