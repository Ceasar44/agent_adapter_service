import asyncio
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from agent_adapter_service.frontend.state.manager import FrontendStateStore
from agent_adapter_service.frontend.state.models import (
    FrontendState,
    FrontendStateDelta,
    FrontendStateError,
    StateVersionConflict,
)
from agent_adapter_service.frontend.tools.models import (
    FrontendToolCall,
    FrontendToolError,
    FrontendToolResult,
)
from agent_adapter_service.frontend.tools.policy import FrontendToolPolicy, ToolDecision


class FrontendEventSink(Protocol):
    """M11 supplies the authorized connection's AG-UI event writer, not HTTP here."""

    async def send(self, thread_id: str, event: Mapping[str, JsonValue]) -> None: ...


@dataclass
class PendingCall:
    call: FrontendToolCall
    future: asyncio.Future[FrontendToolResult]
    deadline: float
    resolving: bool = False
    update_task: asyncio.Task[FrontendState] | None = None
    send_task: asyncio.Task[None] | None = None
    accepting_result: bool = False


class FrontendToolDispatcher:
    """One active browser connection in a trusted store scope.

    The caller must authorize thread ownership before dispatch/result submission.
    Confirmation comes from a trusted server-side approval check, never model arguments.
    IDs stay retired until close; max_calls bounds connection-local memory.
    """

    def __init__(
        self,
        states: FrontendStateStore,
        sink: FrontendEventSink,
        *,
        policy: FrontendToolPolicy | None = None,
        timeout: float = 30,
        max_calls: int = 10000,
        max_payload_bytes: int = 65536,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0 or max_calls <= 0 or max_payload_bytes <= 0:
            raise ValueError("Dispatcher limits must be positive and finite")
        self.states = states
        self.sink = sink
        self.policy = policy or FrontendToolPolicy()
        self.timeout = timeout
        self.max_calls = max_calls
        self.max_payload_bytes = max_payload_bytes
        self._pending: dict[str, PendingCall] = {}
        self._used: set[str] = set()
        self._disconnected: set[str] = set()
        self._closed = False

    def _bounded(self, value: object) -> None:
        try:
            size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise FrontendToolError("Tool payload must be bounded JSON") from None
        if size > self.max_payload_bytes:
            raise FrontendToolError("Tool payload exceeds size limit")

    @staticmethod
    def _failure(call_id: str, status: str, error: str) -> FrontendToolResult:
        return FrontendToolResult(call_id=call_id, status=status, error=error)

    def to_events(self, call: FrontendToolCall) -> list[dict[str, JsonValue]]:
        """AG-UI TOOL_CALL_START → TOOL_CALL_ARGS → TOOL_CALL_END.

        END closes argument streaming, not browser execution. M11 owns SSE encoding.
        """
        return [
            {"type": "TOOL_CALL_START", "toolCallId": call.call_id, "toolCallName": call.name},
            {
                "type": "TOOL_CALL_ARGS",
                "toolCallId": call.call_id,
                "delta": json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":")),
            },
            {"type": "TOOL_CALL_END", "toolCallId": call.call_id},
        ]

    async def dispatch(
        self,
        call: FrontendToolCall,
        *,
        confirmed: bool = False,
    ) -> FrontendToolResult:
        call = FrontendToolCall.model_validate(call.model_dump())
        if self._closed or call.thread_id in self._disconnected:
            return self._failure(call.call_id, "disconnected", "disconnected")
        if call.call_id in self._used:
            raise FrontendToolError("Tool call ID has already been used", http_status=409)
        if len(self._used) >= self.max_calls:
            raise FrontendToolError("Connection tool call limit reached", http_status=429)
        self._used.add(call.call_id)
        decision = self.policy.evaluate(call.name, call.risk)
        if decision == ToolDecision.DENY:
            return self._failure(call.call_id, "denied", "denied")
        try:
            self._bounded(call.arguments)
            arguments = self.policy.validate_arguments(call.name, call.arguments)
        except FrontendToolError:
            return self._failure(call.call_id, "error", "invalid_arguments")
        if decision == ToolDecision.CONFIRM and confirmed is not True:
            return self._failure(call.call_id, "confirmation_required", "confirmation_required")
        call = call.model_copy(
            update={
                "arguments": arguments,
                "risk": self.policy.effective_risk(call.name, call.risk),
            }
        )
        loop = asyncio.get_running_loop()
        pending = PendingCall(call, loop.create_future(), loop.time() + self.timeout)
        self._pending[call.call_id] = pending
        try:
            async with asyncio.timeout_at(pending.deadline):
                for event in self.to_events(call):
                    if pending.future.done():
                        break
                    pending.accepting_result = event["type"] == "TOOL_CALL_END"
                    pending.send_task = asyncio.create_task(self.sink.send(call.thread_id, event))
                    await asyncio.wait(
                        {pending.send_task, pending.future},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if pending.future.done():
                        break
                    await pending.send_task
                return await asyncio.shield(pending.future)
        except TimeoutError:
            return self._failure(call.call_id, "timeout", "timeout")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - normalize failures at the injected transport boundary.
            # Sink internals and browser errors may contain credentials.
            return self._failure(call.call_id, "error", "send_failed")
        finally:
            self._pending.pop(call.call_id, None)
            if not pending.future.done():
                pending.future.cancel()
            if pending.update_task is not None and not pending.update_task.done():
                pending.update_task.cancel()
            if pending.send_task is not None:
                if not pending.send_task.done():
                    pending.send_task.cancel()
                # Retrieve sender exceptions even if a browser result won the race.
                await asyncio.gather(pending.send_task, return_exceptions=True)
            if pending.update_task is not None:
                await asyncio.gather(pending.update_task, return_exceptions=True)

    async def resolve_result(
        self,
        thread_id: str,
        result: FrontendToolResult,
        *,
        state_or_delta: FrontendState | FrontendStateDelta | Mapping | None = None,
    ) -> bool:
        """False for duplicate/late/foreign results; no state mutation in those cases."""
        pending = self._pending.get(result.call_id)
        if (
            pending is None
            or pending.call.thread_id != thread_id
            or pending.resolving
            or not pending.accepting_result
            or pending.future.done()
            or asyncio.get_running_loop().time() >= pending.deadline
        ):
            return False
        self._bounded(result.model_dump(mode="json"))
        result = FrontendToolResult.model_validate(result.model_dump())
        if result.status not in {"success", "error"}:
            raise FrontendToolError("Browser result must be success or error")
        if result.status == "error":
            result = self._failure(result.call_id, "error", "execution_failed")
            if state_or_delta is not None:
                raise FrontendToolError("Failed tool results cannot update state")
        elif result.error is not None:
            raise FrontendToolError("Successful result cannot contain an error")
        if state_or_delta is None and result.resulting_state_version is not None:
            raise FrontendToolError("A resulting revision requires a state update")
        pending.resolving = True
        try:
            if state_or_delta is not None:
                version = (
                    state_or_delta.get("version")
                    if isinstance(state_or_delta, Mapping)
                    else state_or_delta.version
                )
                if type(version) is not int or result.resulting_state_version != version:
                    raise FrontendToolError("Result and state revisions must match")
                try:
                    async with asyncio.timeout_at(pending.deadline):
                        pending.update_task = asyncio.create_task(
                            self.states.update(thread_id, state_or_delta)
                        )
                        state = await pending.update_task
                    result = result.model_copy(update={"resulting_state_version": state.version})
                except StateVersionConflict:
                    result = self._failure(result.call_id, "error", "state_conflict")
                except FrontendStateError:
                    result = self._failure(result.call_id, "error", "invalid_state")
                except TimeoutError:
                    return False
                except asyncio.CancelledError:
                    if pending.future.done():
                        return False
                    raise
            if pending.future.done():
                return False
            pending.future.set_result(result)
            return True
        finally:
            pending.resolving = False

    async def disconnect(self, thread_id: str) -> None:
        self._disconnected.add(thread_id)
        for pending in tuple(self._pending.values()):
            if pending.call.thread_id == thread_id and not pending.future.done():
                pending.future.set_result(
                    self._failure(pending.call.call_id, "disconnected", "disconnected")
                )
                if pending.update_task is not None:
                    pending.update_task.cancel()

    async def close(self) -> None:
        self._closed = True
        for thread_id in {p.call.thread_id for p in self._pending.values()}:
            await self.disconnect(thread_id)
