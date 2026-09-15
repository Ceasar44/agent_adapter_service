"""Public errors never include upstream messages, response bodies or credentials."""

import re
from collections.abc import Iterable, Mapping
from typing import Any

from agent_adapter_service.core.exceptions import IntegrationError


class SaleorError(IntegrationError):
    code = "saleor_error"


class SaleorPermissionError(SaleorError):
    code = "saleor_permission_denied"
    http_status = 403


class SaleorNotFoundError(SaleorError):
    code = "saleor_not_found"
    http_status = 404


class SaleorValidationError(SaleorError):
    code = "saleor_validation_error"
    http_status = 422


def parse_saleor_errors(errors: Iterable[Mapping[str, Any]], *, mutation: bool) -> SaleorError:
    safe = []
    codes = set()
    for error in errors:
        extensions = error.get("extensions")
        code = error.get("code") or (
            extensions.get("code") if isinstance(extensions, dict) else None
        )
        code = code if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,80}", code) else "UNKNOWN"
        codes.add(code)
        item: dict[str, Any] = {"code": code}
        field = error.get("field")
        if isinstance(field, str) and re.fullmatch(r"[A-Za-z0-9_.\[\]]{1,100}", field):
            item["field"] = field
        index = error.get("index")
        if type(index) is int and index >= 0:
            item["index"] = index
        safe.append(item)
    error_type: type[SaleorError] = SaleorValidationError if mutation else SaleorError
    if codes & {"PERMISSION_DENIED", "FORBIDDEN", "UNAUTHENTICATED", "JWT_INVALID_TOKEN"}:
        error_type = SaleorPermissionError
    elif "NOT_FOUND" in codes:
        error_type = SaleorNotFoundError
    return error_type("Saleor rejected the operation", details={"errors": safe})
