"""Keep FastMCP validation diagnostics from echoing admin input values."""

import logging
from contextvars import ContextVar

from fastmcp.exceptions import ToolError, ValidationError
from fastmcp.server.middleware import Middleware
from pydantic import ValidationError as PydanticValidationError

_admin_call: ContextVar[bool] = ContextVar("admin_mcp_call", default=False)


class AdminDiagnosticFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _admin_call.get():
            record.msg = "Admin MCP diagnostic omitted; consult audit trace"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


class AdminPrivacyMiddleware(Middleware):
    async def on_call_tool(self, context, call_next):
        token = _admin_call.set(True)
        try:
            return await call_next(context)
        except (ValidationError, PydanticValidationError):
            raise ToolError("Invalid admin tool arguments") from None
        finally:
            _admin_call.reset(token)


def install_diagnostic_filter() -> None:
    logger = logging.getLogger("fastmcp.server.server")
    if not any(isinstance(item, AdminDiagnosticFilter) for item in logger.filters):
        logger.addFilter(AdminDiagnosticFilter())
