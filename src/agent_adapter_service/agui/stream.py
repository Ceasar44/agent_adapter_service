import asyncio
from collections.abc import AsyncIterator, Awaitable, Mapping
from typing import Protocol

from ag_ui.core import BaseEvent, Event
from anyio import CancelScope
from pydantic import JsonValue, TypeAdapter

from agent_adapter_service.agent.contracts import EventRecord
from agent_adapter_service.agui.event_adapter import ParlantToAguiEventAdapter


class EventGateway(Protocol):
    async def list(self, session_id: str, *, min_offset: int = 0) -> list[EventRecord]: ...


class AguiEventStream:
    """Bounded multiplexing of browser calls and Parlant events, including chunk updates."""

    def __init__(self, thread_id: str, events: EventGateway, *, poll_seconds: float = 0.05):
        self.thread_id = thread_id
        self.events = events
        self.poll_seconds = poll_seconds
        self.queue: asyncio.Queue[BaseEvent | None] = asyncio.Queue(maxsize=128)
        self.adapter = ParlantToAguiEventAdapter()
        self.offset = 0

    async def send(self, thread_id: str, event: Mapping[str, JsonValue]) -> None:
        if thread_id != self.thread_id:
            raise ValueError("Foreign thread event")
        # Only the dispatcher can enqueue executable browser calls.
        if event.get("type") not in {"TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END"}:
            raise ValueError("Unsupported dispatcher event")
        await self.queue.put(TypeAdapter(Event).validate_python(dict(event)))

    async def _poll(self, session_id: str, open_offsets: dict[str, int]) -> None:
        minimum = min([self.offset, *open_offsets.values()])
        records = await self.events.list(session_id, min_offset=minimum)
        for event in sorted(records, key=lambda item: item.offset):
            if event.offset < self.offset and event.id not in open_offsets:
                continue
            if (
                event.kind == "message"
                and event.source == "ai_agent"
                and isinstance(event.data, dict)
            ):
                chunks = event.data.get("chunks")
                if isinstance(chunks, list) and (not chunks or chunks[-1] is not None):
                    open_offsets[event.id] = event.offset
                else:
                    open_offsets.pop(event.id, None)
            self.offset = max(self.offset, event.offset + 1)
            for mapped in self.adapter.convert(event):
                await self.queue.put(mapped)
            if self.adapter.failed:
                return

    async def stream_run(
        self, session_id: str, *, min_offset: int, processing: Awaitable[None]
    ) -> AsyncIterator[BaseEvent]:
        self.offset = min_offset
        engine = asyncio.ensure_future(processing)

        async def produce() -> None:
            open_offsets: dict[str, int] = {}
            try:
                while True:
                    completed = engine.done()
                    await self._poll(session_id, open_offsets)
                    if self.adapter.failed:
                        break
                    if completed:
                        await engine
                        if self.adapter.incomplete_messages:
                            raise RuntimeError("Agent ended with an incomplete message")
                        for event in self.adapter.finish_steps():
                            await self.queue.put(event)
                        break
                    await asyncio.sleep(self.poll_seconds)
            finally:
                # Do not block cancellation attempting to write to a full queue.
                if not asyncio.current_task().cancelling():
                    await self.queue.put(None)

        producer = asyncio.create_task(produce())
        try:
            while (event := await self.queue.get()) is not None:
                yield event
            await producer
        finally:
            # Starlette disconnects use an AnyIO cancellation scope. Shield the
            # join so repeated cancellation cannot interrupt SDK/tool finalizers.
            with CancelScope(shield=True):
                for task in (producer, engine):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(producer, engine, return_exceptions=True)
