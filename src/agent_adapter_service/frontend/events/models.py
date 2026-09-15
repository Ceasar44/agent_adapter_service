from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AwareDatetime, Field, JsonValue

from agent_adapter_service.frontend.state.models import Revision, SemanticModel


class FrontendEventType(StrEnum):
    PAGE_CHANGED = "PAGE_CHANGED"
    PRODUCT_OPENED = "PRODUCT_OPENED"
    VARIANT_CHANGED = "VARIANT_CHANGED"
    CART_CHANGED = "CART_CHANGED"
    CHECKOUT_STEP_CHANGED = "CHECKOUT_STEP_CHANGED"
    STORE_CHANGED = "STORE_CHANGED"


class FrontendEvent(SemanticModel):
    event_type: FrontendEventType
    data: dict[str, JsonValue]
    timestamp: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    source_revision: Revision
