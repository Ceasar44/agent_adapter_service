import asyncio
import math
from time import monotonic
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from agent_adapter_service.observability.logging import bind_context, get_context
from agent_adapter_service.observability.tracing import traces
from agent_adapter_service.observability.metrics import metrics
from collections.abc import AsyncIterator, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Protocol

from ag_ui.core import (
    BaseEvent,
    RunAgentInput,
    RunStartedEvent,
    RunFinishedEvent,
    RunErrorEvent,
    StateSnapshotEvent,
)
from pydantic import JsonValue
from anyio import CancelScope

from agent_adapter_service.agent.contracts import EventRecord
from agent_adapter_service.agent.tools.context import ActiveToolContexts, AgentToolContext
from agent_adapter_service.agui.input_adapter import (
    AguiInputAdapter,
    extract_latest_user_message,
    extract_storefront_identity,
)
from agent_adapter_service.agui.schemas import TrustedStorefrontContext, ToolResultInput
from agent_adapter_service.agui.stream import AguiEventStream, EventGateway
from agent_adapter_service.conversations.models import ConversationContext
from agent_adapter_service.conversations.service import ConversationService
from agent_adapter_service.core.exceptions import AppError, AuthorizationError
from agent_adapter_service.customer_identity.resolver import CustomerIdentityResolver
from agent_adapter_service.frontend.events.handler import FrontendEventHandler
from agent_adapter_service.frontend.state.manager import FrontendStateManager
from agent_adapter_service.frontend.state.models import FrontendState, StateVersionConflict
from agent_adapter_service.frontend.tools.dispatcher import FrontendToolDispatcher
from agent_adapter_service.persistence.contracts import StoreScope


class MessageGateway(Protocol):
    async def create_customer_message(
        self,
        session_id: str,
        message: str,
        *,
        trigger_processing: bool = True,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> EventRecord: ...


class ProcessingGateway(Protocol):
    async def process(self, session_id: str, agent_id: str, *, run_id: str) -> None: ...


@dataclass
class ActiveRun:
    trusted: TrustedStorefrontContext
    conversation: ConversationContext
    dispatcher: FrontendToolDispatcher
    stream: AguiEventStream
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class _RunStateStore:
    """Publish state/UI events before the dispatcher wakes the waiting agent tool."""

    def __init__(
        self, runner: "AguiRunRunner", conversation: ConversationContext, stream: AguiEventStream
    ):
        self.runner, self.conversation, self.stream = runner, conversation, stream

    async def get(self, thread_id: str) -> FrontendState | None:
        return await self.runner.states.get(thread_id)

    async def update(self, thread_id: str, state_or_delta) -> FrontendState:
        previous = await self.get(thread_id)
        current = await self.runner.states.update(thread_id, state_or_delta)
        await self.runner.frontend_events.handle_state_change(
            self.conversation.binding, previous, current
        )
        await self.stream.queue.put(StateSnapshotEvent(snapshot=current.model_dump(mode="json")))
        return current


class AguiRunRunner:
    def __init__(
        self,
        *,
        scope: StoreScope,
        identities: CustomerIdentityResolver,
        conversations: ConversationService,
        states: FrontendStateManager,
        frontend_events: FrontendEventHandler,
        messages: MessageGateway,
        events: EventGateway,
        processing: ProcessingGateway,
        contexts: ActiveToolContexts,
        input_adapter: AguiInputAdapter | None = None,
        run_timeout: float = 120,
        tool_timeout: float = 30,
    ) -> None:
        if states.scope != scope or contexts.scope != scope:
            raise ValueError("AG-UI dependencies must share a store scope")
        if any(not math.isfinite(value) or value <= 0 for value in (run_timeout, tool_timeout)):
            raise ValueError("Run and tool timeouts must be positive and finite")
        self.scope, self.identities, self.conversations = scope, identities, conversations
        self.states, self.frontend_events = states, frontend_events
        self.messages, self.events, self.processing, self.contexts = (
            messages,
            events,
            processing,
            contexts,
        )
        self.input_adapter = input_adapter or AguiInputAdapter()
        self.run_timeout, self.tool_timeout = run_timeout, tool_timeout
        self._reserved: set[str] = set()
        self._active: dict[str, ActiveRun] = {}
        self._tasks: set[asyncio.Task] = set()
        self._closed = False

    def authorize(self, trusted: TrustedStorefrontContext) -> None:
        extract_storefront_identity(trusted)
        if trusted.scope != self.scope:
            raise AuthorizationError("Storefront scope does not match this service")

    async def handle_tool_result(
        self, input: ToolResultInput, trusted: TrustedStorefrontContext
    ) -> bool:
        self.authorize(trusted)
        active = self._active.get(input.thread_id)
        if active is None:
            return False
        # Revalidate fresh authenticated request context; never upgrade an in-flight run.
        old, new = active.trusted.identity, trusted.identity
        if (old.visitor_id, old.saleor_user_id, old.status) != (
            new.visitor_id,
            new.saleor_user_id,
            new.status,
        ):
            raise AuthorizationError("Tool receipt does not belong to this identity")
        if input.run_id != active.conversation.run_id:
            return False
        async with active.state_lock:
            return await active.dispatcher.resolve_result(
                input.thread_id, input.result, state_or_delta=input.state
            )

    async def _sync_state(self, active: ActiveRun, state: dict) -> FrontendState:
        thread_id = active.conversation.binding.thread_id
        previous = await self.states.get(thread_id)
        if previous and state.get("version") == previous.version and "changes" not in state:
            candidate = self.states.reducer.apply_snapshot(
                previous.model_copy(update={"version": previous.version - 1}),
                state,
            )
            if candidate.model_dump(exclude={"updated_at"}) != previous.model_dump(
                exclude={"updated_at"}
            ):
                raise StateVersionConflict("Same revision has different state")
            return previous
        return await active.dispatcher.states.update(thread_id, state)

    async def run(
        self, input: RunAgentInput, trusted: TrustedStorefrontContext
    ) -> AsyncIterator[BaseEvent]:
        started, status = monotonic(), "cancelled"
        # Input IDs are UX correlation only, never authorization capabilities.
        with bind_context(thread_id=input.thread_id, run_id=input.run_id):
            fields = get_context()
        current = traces.tracer.start_span("agui.run", attributes=fields)
        fields["trace_id"] = format(current.get_span_context().trace_id, "032x")
        output = self._run(input, trusted)
        try:
            while True:
                with bind_context(**fields), trace.use_span(
                    current, record_exception=False, set_status_on_exception=False
                ):
                    try:
                        item = await anext(output)
                    except StopAsyncIteration:
                        break
                if isinstance(item, RunErrorEvent):
                    status = "failure"
                elif isinstance(item, RunFinishedEvent):
                    status = "success"
                yield item
        except (asyncio.CancelledError, GeneratorExit):
            status = "cancelled"
            raise
        except BaseException:
            status = "failure"
            raise
        finally:
            try:
                with bind_context(**fields), trace.use_span(
                    current, record_exception=False, set_status_on_exception=False
                ):
                    await output.aclose()
            finally:
                if status != "success":
                    current.set_status(Status(StatusCode.ERROR))
                current.end()
                metrics.record("run", "agui", monotonic() - started, status)

    async def _run(
        self, input: RunAgentInput, trusted: TrustedStorefrontContext
    ) -> AsyncIterator[BaseEvent]:
        self.authorize(trusted)
        adapted = self.input_adapter.adapt(input)
        if self._closed:
            raise AppError("AG-UI is shutting down", http_status=503)
        # Receipt-only calls may reach the currently streaming run over a second HTTP request.
        if adapted.forwarded.tool_results:
            if input.messages or input.state:
                raise AppError("Tool receipt requests cannot start a new turn", http_status=422)
            yield RunStartedEvent(thread_id=input.thread_id, run_id=input.run_id)
            try:
                for receipt in adapted.forwarded.tool_results:
                    if not await self.handle_tool_result(receipt, trusted):
                        raise ValueError("Stale receipt")
            except Exception:
                yield RunErrorEvent(
                    message="Tool receipt was rejected", code="tool_result_rejected"
                )
                return
            yield RunFinishedEvent(thread_id=input.thread_id, run_id=input.run_id)
            return
        if input.thread_id in self._reserved:
            raise AppError("Thread already has an active run", code="run_conflict", http_status=409)
        self._reserved.add(input.thread_id)
        task = asyncio.current_task()
        self._tasks.add(task)
        active = None
        yield_started = False
        try:
            yield_started = True
            yield RunStartedEvent(thread_id=input.thread_id, run_id=input.run_id)
            self._tasks.discard(task)
            task = asyncio.current_task()
            self._tasks.add(task)
            async with asyncio.timeout(self.run_timeout):
                existing = await self.conversations.bindings.repository.find_by_thread(
                    input.thread_id
                )
                identity = await self.identities.resolve(
                    trusted.identity, thread_id=input.thread_id if existing else None
                )
                conversation = await self.conversations.resolve(
                    input.thread_id, input.run_id, identity
                )
                session_id = conversation.binding.parlant_session_id
                history = await self.events.list(session_id, min_offset=0)
                message = extract_latest_user_message(input, history)
                stream = AguiEventStream(input.thread_id, self.events)
                dispatcher = FrontendToolDispatcher(
                    _RunStateStore(self, conversation, stream),
                    stream,
                    timeout=self.tool_timeout,
                )
                active = ActiveRun(trusted, conversation, dispatcher, stream)
                self._active[input.thread_id] = active
                if input.state:
                    await self._sync_state(active, input.state)
                while not stream.queue.empty():
                    yield stream.queue.get_nowait()
                if message is not None:
                    context = AgentToolContext(
                        conversation, self.scope, get_context().get("trace_id"), dispatcher, trusted.approvals
                    )
                    async with self.contexts.bind(context):
                        # Parlant remains the only history store. Never enqueue detached SDK processing.
                        event = await self.messages.create_customer_message(
                            session_id,
                            message.content,
                            trigger_processing=False,
                            metadata={
                                "trace_id": get_context().get("trace_id"),
                                "agui_message_id": message.id,
                                "agui_run_id": input.run_id,
                                "agui_thread_id": input.thread_id,
                            },
                        )
                        async with aclosing(
                            stream.stream_run(
                                session_id,
                                min_offset=event.offset + 1,
                                processing=self.processing.process(
                                    session_id, conversation.binding.agent_id, run_id=input.run_id
                                ),
                            )
                        ) as output:
                            async for item in output:
                                yield item
                        if stream.adapter.failed:
                            return
                yield RunFinishedEvent(thread_id=input.thread_id, run_id=input.run_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            if not yield_started:
                raise
            # Input/SDK exception text can contain tokens, SQL, PII, or model content.
            yield RunErrorEvent(message="Agent run could not be completed", code="run_failed")
        finally:
            with CancelScope(shield=True):
                if active is not None:
                    await active.dispatcher.disconnect(input.thread_id)
                    await active.dispatcher.close()
                self._active.pop(input.thread_id, None)
                self._reserved.discard(input.thread_id)
                self._tasks.discard(task)

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._tasks - {asyncio.current_task()})
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.states.clear()
