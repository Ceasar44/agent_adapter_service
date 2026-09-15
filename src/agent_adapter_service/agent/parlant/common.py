"""SDK boundary helpers. SDK types are injected, so unit tests need no model provider."""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from agent_adapter_service.agent.contracts import EventRecord
from agent_adapter_service.core.exceptions import AppError, IntegrationError


@contextmanager
def sdk_errors(operation: str):
    try:
        yield
    except AppError:
        raise
    except Exception:  # noqa: BLE001 - translate all third-party errors at this boundary
        # SDK exceptions can contain prompts, provider keys and customer data.
        raise IntegrationError(
            "Parlant operation failed", details={"integration": "parlant", "operation": operation}
        ) from None


@dataclass(frozen=True)
class GatewayContext:
    application: Any
    event_source: Any
    event_kind: Any
    moderation: Any
    timeout: Any


def event_record(event: Any) -> EventRecord:
    return EventRecord(
        id=event.id,
        offset=event.offset,
        trace_id=event.trace_id,
        source=event.source.value,
        kind=event.kind.value,
        data=event.data,
        metadata=event.metadata,
    )
