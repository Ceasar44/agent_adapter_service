from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, JsonValue, field_validator

from agent_adapter_service.core.exceptions import AppError
from agent_adapter_service.core.types import RiskLevel
from agent_adapter_service.frontend.state.models import Revision, SemanticModel
from agent_adapter_service.persistence.contracts import Identifier


class FrontendToolError(AppError):
    code = "frontend_tool_error"


class ToolOutput(SemanticModel):
    """Small browser receipt; commerce facts still require backend verification."""

    applied: bool | None = None
    product_id: Identifier | None = None
    variant_id: Identifier | None = None
    item_count: Annotated[int, Field(ge=0, le=100000)] | None = None


class FrontendToolCall(SemanticModel):
    call_id: Identifier = Field(default_factory=lambda: uuid4().hex)
    name: Annotated[str, Field(min_length=1, max_length=100)]
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    thread_id: Identifier
    risk: RiskLevel = RiskLevel.LOW


class FrontendToolResult(SemanticModel):
    call_id: Identifier
    status: Literal[
        "success", "error", "timeout", "disconnected", "denied", "confirmation_required"
    ]
    data: dict[str, JsonValue] = Field(default_factory=dict)
    error: (
        Literal[
            "execution_failed",
            "timeout",
            "disconnected",
            "send_failed",
            "denied",
            "confirmation_required",
            "invalid_arguments",
            "state_conflict",
            "invalid_state",
        ]
        | None
    ) = None
    resulting_state_version: Revision | None = None

    @field_validator("data")
    @classmethod
    def validate_output(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return ToolOutput.model_validate(value).model_dump(mode="json", exclude_none=True)
