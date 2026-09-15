"""Conversation data only; messages remain exclusively in Parlant."""

from pydantic import BaseModel, ConfigDict, JsonValue

from agent_adapter_service.customer_identity.models import Identifier, ResolvedCustomerIdentity


class ConversationBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    thread_id: Identifier
    parlant_session_id: Identifier
    parlant_customer_id: Identifier
    agent_id: Identifier


class ConversationContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    binding: ConversationBinding
    identity: ResolvedCustomerIdentity
    run_id: Identifier
    # Request-local UX context, never persisted as conversation history.
    frontend_state: dict[str, JsonValue] | None = None
