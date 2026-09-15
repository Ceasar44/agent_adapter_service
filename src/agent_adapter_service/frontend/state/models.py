from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, field_validator

from agent_adapter_service.core.exceptions import AppError

Text = Annotated[str, Field(max_length=200)]
Identifier = Annotated[str, Field(min_length=1, max_length=255)]
Revision = Annotated[int, Field(ge=0, strict=True)]


class FrontendStateError(AppError):
    code = "invalid_frontend_state"


class StateVersionConflict(FrontendStateError):
    code = "frontend_state_version_conflict"
    http_status = 409


class SemanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RouteState(SemanticModel):
    pathname: Annotated[str, Field(max_length=1024)] | None = None
    page_type: Literal["home", "product", "category", "search", "cart", "checkout", "other"] = (
        "other"
    )

    @field_validator("pathname")
    @classmethod
    def local_path(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.startswith("/")
            or value.startswith("//")
            or any(c in value for c in "?#\\%")
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError("Expected a local pathname without query or fragment")
        return value


class StoreState(SemanticModel):
    channel: Text | None = None
    locale: Annotated[str, Field(max_length=35)] | None = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None


class ProductState(SemanticModel):
    id: Identifier | None = None
    variant_id: Identifier | None = None
    color: Text | None = None
    size: Text | None = None


class CartState(SemanticModel):
    item_count: Annotated[int, Field(ge=0, le=100000, strict=True)] = 0
    is_open: bool = False


class CheckoutState(SemanticModel):
    step: Literal["contact", "shipping", "delivery", "payment", "review", "complete"] | None = None


class UserState(SemanticModel):
    # Display hint only. No user/customer IDs, names, email, or credentials.
    is_logged_in: bool | None = None


class UIState(SemanticModel):
    cart_open: bool = False
    chat_open: bool = False


class FrontendState(SemanticModel):
    route: RouteState = Field(default_factory=RouteState)
    store: StoreState = Field(default_factory=StoreState)
    product: ProductState = Field(default_factory=ProductState)
    cart: CartState = Field(default_factory=CartState)
    checkout: CheckoutState = Field(default_factory=CheckoutState)
    user: UserState = Field(default_factory=UserState)
    ui: UIState = Field(default_factory=UIState)
    version: Revision = 0
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class FrontendStateDelta(SemanticModel):
    """Object merge: omitted leaves stay; null clears nullable leaves; no JSON Patch."""

    base_version: Revision
    version: Annotated[int, Field(gt=0, strict=True)]
    changes: dict[str, JsonValue]
