import json
from dataclasses import dataclass
from collections.abc import Sequence

from ag_ui.core import RunAgentInput, UserMessage
from pydantic import TypeAdapter, ValidationError

from agent_adapter_service.agent.contracts import EventRecord
from agent_adapter_service.agui.schemas import ForwardedProps, TrustedStorefrontContext
from agent_adapter_service.core.exceptions import AppError, AuthorizationError
from agent_adapter_service.customer_identity.models import Identifier, StorefrontIdentity


def invalid_input() -> AppError:
    return AppError("Invalid AG-UI input", code="invalid_agui_input", http_status=422)


def extract_storefront_identity(context: TrustedStorefrontContext) -> StorefrontIdentity:
    if not isinstance(context, TrustedStorefrontContext):
        raise AuthorizationError("Verified Storefront identity is required", http_status=401)
    return context.identity


def extract_latest_user_message(
    input: RunAgentInput, history: Sequence[EventRecord]
) -> UserMessage | None:
    """Compare stable client IDs with Parlant metadata, including across restarts.

    A request submits at most one new user message. Browser assistant/system/tool
    history is never ingested as authoritative conversation content.
    """
    persisted = {
        event.metadata.get("agui_message_id"): event
        for event in history
        if event.source == "customer"
        and event.kind == "message"
        and isinstance(event.metadata.get("agui_message_id"), str)
    }
    users = [message for message in input.messages if isinstance(message, UserMessage)]
    new = []
    for message in users:
        previous = persisted.get(message.id)
        if previous is None:
            new.append(message)
        elif not isinstance(previous.data, dict) or previous.data.get("message") != message.content:
            raise AppError("Message ID was already used", code="message_conflict", http_status=409)
    if len(new) > 1 or (new and new[0] is not users[-1]):
        raise invalid_input()
    if new and any(event.metadata.get("agui_run_id") == input.run_id for event in history):
        raise AppError("Run ID was already used", code="run_conflict", http_status=409)
    return new[0] if new else None


@dataclass(frozen=True)
class AdaptedInput:
    input: RunAgentInput
    forwarded: ForwardedProps


class AguiInputAdapter:
    def __init__(self, *, max_state_bytes: int = 65536, max_request_bytes: int = 1048576):
        self.max_state_bytes = max_state_bytes
        self.max_request_bytes = max_request_bytes

    def adapt(self, input: RunAgentInput) -> AdaptedInput:
        try:
            identifier = TypeAdapter(Identifier)
            identifier.validate_python(input.thread_id)
            identifier.validate_python(input.run_id)
            size = len(input.model_dump_json().encode("utf-8"))
            if size > self.max_request_bytes or len(input.messages) > 1000:
                raise invalid_input()
            ids = set()
            for message in input.messages:
                identifier.validate_python(message.id)
                if message.id in ids:
                    raise invalid_input()
                ids.add(message.id)
                if isinstance(message, UserMessage) and (
                    not isinstance(message.content, str)
                    or not message.content.strip()
                    or len(message.content) > 32000
                ):
                    raise invalid_input()
            if input.state is not None:
                if not isinstance(input.state, dict):
                    raise invalid_input()
                if len(json.dumps(input.state, allow_nan=False).encode()) > self.max_state_bytes:
                    raise invalid_input()
            forwarded = ForwardedProps.model_validate(input.forwarded_props or {})
            for result in forwarded.tool_results:
                if result.thread_id != input.thread_id or result.run_id != input.run_id:
                    raise invalid_input()
            return AdaptedInput(input, forwarded)
        except (ValidationError, ValueError, TypeError, UnicodeError, RecursionError):
            raise invalid_input() from None
