"""Allowlisted conversion: never expose raw SDK metadata or backend tool payloads."""

import json

from ag_ui.core import (
    BaseEvent,
    CustomEvent,
    RunErrorEvent,
    StepStartedEvent,
    StepFinishedEvent,
    TextMessageStartEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    ToolCallStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
)

from agent_adapter_service.agent.contracts import EventRecord
from agent_adapter_service.frontend.tools.models import FrontendToolCall


class ParlantToAguiEventAdapter:
    def __init__(self) -> None:
        self._text: dict[str, str] = {}
        self._ended: set[str] = set()
        self._step: str | None = None
        self.failed = False

    def map_message_event(self, event: EventRecord) -> list[BaseEvent]:
        if event.source != "ai_agent" or not isinstance(event.data, dict):
            return []
        if event.id in self._ended:
            return []
        chunks = event.data.get("chunks")
        if isinstance(chunks, list):
            text = "".join(chunk for chunk in chunks if isinstance(chunk, str))
            complete = bool(chunks) and chunks[-1] is None
        else:
            text = event.data.get("message", "")
            complete = True
        if not isinstance(text, str):
            return []
        output: list[BaseEvent] = []
        if event.id not in self._text:
            self._text[event.id] = ""
            output.append(TextMessageStartEvent(message_id=event.id))
        previous = self._text[event.id]
        if not text.startswith(previous):
            raise ValueError("Non-append-only message stream")
        if delta := text[len(previous) :]:
            output.append(TextMessageContentEvent(message_id=event.id, delta=delta))
        self._text[event.id] = text
        if complete:
            output.append(TextMessageEndEvent(message_id=event.id))
            self._ended.add(event.id)
        return output

    def finish_steps(self) -> list[BaseEvent]:
        if self._step is None:
            return []
        result = [StepFinishedEvent(step_name=self._step)]
        self._step = None
        return result

    @property
    def incomplete_messages(self) -> bool:
        return bool(self._text.keys() - self._ended)

    def map_status_event(self, event: EventRecord) -> list[BaseEvent]:
        if event.source != "ai_agent" or not isinstance(event.data, dict):
            return []
        status = event.data.get("status")
        if status in {"error", "cancelled"}:
            self.failed = True
            return self.finish_steps() + [
                RunErrorEvent(message="Agent run failed", code="agent_error")
            ]
        if status in {"processing", "typing"} and status != self._step:
            events = self.finish_steps()
            self._step = status
            return events + [StepStartedEvent(step_name=status)]
        if status == "ready":
            return self.finish_steps()
        return []

    def map_frontend_tool_call(self, call: FrontendToolCall) -> list[BaseEvent]:
        return [
            ToolCallStartEvent(tool_call_id=call.call_id, tool_call_name=call.name),
            ToolCallArgsEvent(tool_call_id=call.call_id, delta=json.dumps(call.arguments)),
            ToolCallEndEvent(tool_call_id=call.call_id),
        ]

    def convert(self, event: EventRecord) -> list[BaseEvent]:
        if event.kind == "message":
            return self.map_message_event(event)
        if event.kind == "status":
            return self.map_status_event(event)
        if event.source != "ai_agent" or not isinstance(event.data, dict):
            return []
        if event.kind == "tool":
            # Backend tools are already executed. Do not reissue them as browser calls.
            calls = event.data.get("tool_calls", [])
            count = len(calls) if isinstance(calls, list) else 0
            return [CustomEvent(name="agent_tools_completed", value={"count": count})]
        if event.kind == "custom" and event.data.get("event_type") == "handoff":
            return [CustomEvent(name="handoff", value={"requested": True})]
        return []
