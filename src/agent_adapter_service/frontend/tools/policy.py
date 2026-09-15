from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import Field, JsonValue, ValidationError, field_validator

from agent_adapter_service.core.types import RiskLevel
from agent_adapter_service.frontend.state.models import RouteState, SemanticModel
from agent_adapter_service.frontend.tools.models import FrontendToolError
from agent_adapter_service.persistence.contracts import Identifier


class ToolDecision(StrEnum):
    AUTO = "auto"
    CONFIRM = "confirm"
    DENY = "deny"


class NavigateArguments(SemanticModel):
    pathname: Annotated[str, Field(min_length=1, max_length=1024)]

    @field_validator("pathname")
    @classmethod
    def validate_path(cls, value: str) -> str:
        RouteState(pathname=value)
        return value


class ProductArguments(SemanticModel):
    product_id: Identifier


class VariantArguments(ProductArguments):
    variant_id: Identifier


class CartAddArguments(VariantArguments):
    quantity: Annotated[int, Field(ge=1, le=100)] = 1


class CartLineArguments(SemanticModel):
    line_id: Identifier


class QuantityArguments(CartLineArguments):
    quantity: Annotated[int, Field(ge=1, le=100)]


class TargetArguments(SemanticModel):
    target: Literal["product", "variants", "cart", "checkout"]


class FrontendToolPolicy:
    """Local frontend allowlist. Caller risk can strengthen, never weaken, policy."""

    _schemas: ClassVar[dict[str, type[SemanticModel]]] = {
        "navigate": NavigateArguments,
        "open_product": ProductArguments,
        "select_variant": VariantArguments,
        "open_cart": SemanticModel,
        "open_checkout": SemanticModel,
        "highlight_element": TargetArguments,
        "scroll_to": TargetArguments,
        "add_to_cart": CartAddArguments,
        "remove_from_cart": CartLineArguments,
        "change_quantity": QuantityArguments,
    }
    _confirm = frozenset({"add_to_cart", "remove_from_cart", "change_quantity"})

    def evaluate(self, name: str, risk: RiskLevel = RiskLevel.LOW) -> ToolDecision:
        if name not in self._schemas:
            return ToolDecision.DENY
        if name in self._confirm or risk != RiskLevel.LOW:
            return ToolDecision.CONFIRM
        return ToolDecision.AUTO

    def validate_arguments(
        self, name: str, arguments: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        schema = self._schemas.get(name)
        if schema is None:
            raise FrontendToolError("Frontend tool is not allowed")
        try:
            return schema.model_validate(arguments).model_dump(mode="json")
        except ValidationError:
            raise FrontendToolError("Invalid frontend tool arguments") from None

    def effective_risk(self, name: str, risk: RiskLevel) -> RiskLevel:
        if name in self._confirm and risk == RiskLevel.LOW:
            return RiskLevel.MEDIUM
        return risk
