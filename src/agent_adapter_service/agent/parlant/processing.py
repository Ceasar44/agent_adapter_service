"""Run the embedded engine in the caller-owned task, without detached SDK jobs."""

from agent_adapter_service.agent.parlant.common import sdk_errors
from agent_adapter_service.observability.tracing import observed
from agent_adapter_service.observability.logging import get_context


class ParlantProcessingGateway:
    def __init__(self, engine, emitters, tracer, context_type) -> None:
        self.engine = engine
        self.emitters = emitters
        self.tracer = tracer
        self.context_type = context_type

    @observed("parlant", "process")
    async def process(self, session_id: str, agent_id: str, *, run_id: str) -> None:
        with (
            sdk_errors("process_session"),
            self.tracer.span("agui_run", {"agui.run_id": run_id, "session_id": session_id, **get_context()}),
        ):
            emitter = await self.emitters.create_event_emitter(
                emitting_agent_id=agent_id, session_id=session_id
            )
            result = await self.engine.process(
                self.context_type(session_id=session_id, agent_id=agent_id),
                event_emitter=emitter,
            )
            if result is False:
                raise RuntimeError("Engine processing failed")
