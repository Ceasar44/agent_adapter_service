"""Shared service validation and mapping; no protocol-adapter dependencies."""

from decimal import Decimal, InvalidOperation
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..errors import SaleorError, SaleorNotFoundError, SaleorValidationError
from ..models import DTO, Page, PageInfo

T = TypeVar("T", bound=DTO)


def identifier(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise SaleorValidationError("Invalid identifier")
    return value


def limit(value: int, maximum: int = 100) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise SaleorValidationError(f"Limit must be between 1 and {maximum}")
    return value


def amount(value: Decimal | str | int) -> Decimal:
    if isinstance(value, bool):
        raise SaleorValidationError("Amount must be a finite nonnegative decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise SaleorValidationError("Invalid amount") from None
    if not result.is_finite() or result < 0:
        raise SaleorValidationError("Amount must be a finite nonnegative decimal")
    return result


def dto(kind: type[T], value: BaseModel | None) -> T:
    if value is None:
        raise SaleorNotFoundError("Saleor resource not found")
    try:
        return kind.model_validate(value.model_dump(by_alias=True))
    except ValidationError:
        raise SaleorError("Invalid Saleor resource data") from None


def page(kind: type[T], connection: BaseModel | None) -> Page[T]:
    if connection is None:
        raise SaleorError("Saleor connection is unavailable")
    return Page[kind](
        items=[dto(kind, edge.node) for edge in connection.edges],
        page_info=dto(PageInfo, connection.pageInfo),
    )


def input_fields(value: BaseModel, allowed: set[str]) -> None:
    if not value.model_fields_set or value.model_fields_set - allowed:
        raise SaleorValidationError("Empty update or unsupported input fields")


def unique_ids(values: list[str]) -> None:
    for value in values:
        identifier(value)
    if len(values) != len(set(values)):
        raise SaleorValidationError("Duplicate identifiers")


def payload(value: BaseModel | None) -> BaseModel:
    if value is None:
        raise SaleorError("Saleor mutation returned no result")
    return value
