from collections.abc import Mapping
from typing import Protocol

from pydantic import JsonValue

from agent_adapter_service.conversations.models import ConversationBinding
from agent_adapter_service.frontend.events.models import FrontendEvent, FrontendEventType
from agent_adapter_service.frontend.state.models import FrontendState, StateVersionConflict


class FrontendEventGateway(Protocol):
    async def create(
        self,
        session_id: str,
        event_type: str,
        data: Mapping[str, JsonValue],
        *,
        trigger_processing: bool = False,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> object: ...


class FrontendEventHandler:
    def __init__(self, gateway: FrontendEventGateway) -> None:
        self.gateway = gateway

    def diff(
        self,
        previous: FrontendState | None,
        current: FrontendState,
    ) -> list[FrontendEvent]:
        current = FrontendState.model_validate(current.model_dump())
        old = FrontendState.model_validate(previous.model_dump()) if previous else FrontendState()
        if current.version <= old.version:
            raise StateVersionConflict("Semantic events require a newer state revision")
        changes: list[tuple[FrontendEventType, dict[str, JsonValue]]] = []
        if current.route != old.route:
            changes.append((FrontendEventType.PAGE_CHANGED, current.route.model_dump(mode="json")))
        if current.store != old.store:
            changes.append((FrontendEventType.STORE_CHANGED, current.store.model_dump(mode="json")))
        if current.product.id != old.product.id and current.product.id is not None:
            changes.append(
                (FrontendEventType.PRODUCT_OPENED, current.product.model_dump(mode="json"))
            )
        elif current.product.id is not None and current.product != old.product:
            changes.append(
                (FrontendEventType.VARIANT_CHANGED, current.product.model_dump(mode="json"))
            )
        if current.cart.item_count != old.cart.item_count:
            changes.append(
                (FrontendEventType.CART_CHANGED, {"item_count": current.cart.item_count})
            )
        if current.checkout.step != old.checkout.step:
            changes.append(
                (FrontendEventType.CHECKOUT_STEP_CHANGED, {"step": current.checkout.step})
            )
        return [
            FrontendEvent(
                event_type=kind,
                data=data,
                timestamp=current.updated_at,
                source_revision=current.version,
            )
            for kind, data in changes
        ]

    async def handle_state_change(
        self,
        binding: ConversationBinding,
        previous: FrontendState | None,
        current: FrontendState,
        *,
        trigger_processing: bool = False,
    ) -> list[FrontendEvent]:
        """Use a binding already authorized by ConversationService in this store.

        Process revisions in order in the caller. Gateway failures propagate; there is no
        local message history or claim of exactly-once delivery across network failures.
        """
        events = self.diff(previous, current)
        for event in events:
            await self.gateway.create(
                binding.parlant_session_id,
                event.event_type.value,
                event.data,
                trigger_processing=trigger_processing,
                metadata={
                    "thread_id": binding.thread_id,
                    "source_revision": event.source_revision,
                    "timestamp": event.timestamp.isoformat(),
                },
            )
        return events
