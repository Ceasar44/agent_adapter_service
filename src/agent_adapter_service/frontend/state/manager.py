import asyncio
from collections.abc import Mapping
from typing import Protocol

from agent_adapter_service.frontend.state.models import FrontendState, FrontendStateDelta
from agent_adapter_service.frontend.state.reducer import FrontendStateReducer
from agent_adapter_service.persistence.contracts import StoreScope, identifier_adapter


class FrontendStateStore(Protocol):
    """One trusted store scope; implementations must make revision checks atomic."""

    async def get(self, thread_id: str) -> FrontendState | None: ...
    async def update(
        self,
        thread_id: str,
        state_or_delta: FrontendState | FrontendStateDelta | Mapping,
    ) -> FrontendState: ...


class FrontendStateManager:
    """Process-local state for one store. Call only after ConversationService authorization.

    Share one instance per store in a worker; Redis can implement FrontendStateStore later.
    """

    def __init__(self, scope: StoreScope, reducer: FrontendStateReducer | None = None) -> None:
        self.scope = scope
        self.reducer = reducer or FrontendStateReducer()
        self._states: dict[str, FrontendState] = {}
        self._lock = asyncio.Lock()

    async def get(self, thread_id: str) -> FrontendState | None:
        identifier_adapter.validate_python(thread_id)
        async with self._lock:
            state = self._states.get(thread_id)
            return state.model_copy(deep=True) if state else None

    async def update(
        self,
        thread_id: str,
        state_or_delta: FrontendState | FrontendStateDelta | Mapping,
    ) -> FrontendState:
        identifier_adapter.validate_python(thread_id)
        async with self._lock:
            current = self._states.get(thread_id)
            is_delta = isinstance(state_or_delta, FrontendStateDelta) or (
                isinstance(state_or_delta, Mapping) and "changes" in state_or_delta
            )
            method = self.reducer.apply_delta if is_delta else self.reducer.apply_snapshot
            state = method(current, state_or_delta)
            self._states[thread_id] = state
            return state.model_copy(deep=True)

    async def clear(self) -> None:
        """Release transient state on application shutdown."""
        async with self._lock:
            self._states.clear()
