"""Project extensions; core wire types come from the official AG-UI SDK."""

from dataclasses import dataclass

from ag_ui.core import RunAgentInput
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent_adapter_service.agent.tools.context import FrontendApproval
from agent_adapter_service.customer_identity.models import Identifier, StorefrontIdentity
from agent_adapter_service.frontend.tools.models import FrontendToolResult
from agent_adapter_service.persistence.contracts import StoreScope


@dataclass(frozen=True)
class TrustedStorefrontContext:
    """Only authentication middleware/BFF verification may construct this capability.

    The verifier must authenticate the BFF, protect visitor integrity and enforce
    CSRF/origin checks for cookie sessions. Never deserialize it from request JSON.
    """

    scope: StoreScope
    identity: StorefrontIdentity
    approvals: tuple[FrontendApproval, ...] = ()


class ToolResultInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    thread_id: Identifier = Field(alias="threadId")
    run_id: Identifier = Field(alias="runId")
    result: FrontendToolResult
    state: dict[str, JsonValue] | None = None


class ForwardedProps(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tool_results: list[ToolResultInput] = Field(
        default_factory=list, alias="toolResults", max_length=20
    )


__all__ = ["RunAgentInput", "TrustedStorefrontContext", "ToolResultInput", "ForwardedProps"]
