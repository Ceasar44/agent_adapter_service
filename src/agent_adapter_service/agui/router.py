"""Authenticated BFF entry points. No browser identity headers are trusted here."""

import json
from contextlib import aclosing
from typing import Annotated

from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from agent_adapter_service.agui.input_adapter import invalid_input
from agent_adapter_service.agui.runner import AguiRunRunner
from agent_adapter_service.agui.schemas import TrustedStorefrontContext, ToolResultInput
from agent_adapter_service.core.exceptions import AppError, AuthorizationError

router = APIRouter(tags=["agui"])


async def get_storefront_context(request: Request) -> TrustedStorefrontContext:
    headers = request.headers.getlist("x-storefront-authorization")
    if len(headers) != 1 or not headers[0]:
        raise AuthorizationError("Verified Storefront identity is required", http_status=401)
    verifier = getattr(request.app.state, "storefront_bff_verifier", None)
    if verifier is None:
        raise AppError("BFF authentication is not ready", code="not_ready", http_status=503)
    verified = await verifier.verify(
        headers[0], method=request.method,
        path=request.scope.get("raw_path", request.url.path.encode()).decode("ascii"),
        query=request.scope.get("query_string", b""),
        body=await read_request_bytes(request, 1048576),
    )
    request.state.storefront_context = verified.context
    return verified.context


def get_agui_runner(request: Request) -> AguiRunRunner:
    resources = getattr(request.app.state, "resources", None)
    runner = resources.services.get("agui_runner") if resources and resources.ready else None
    if not isinstance(runner, AguiRunRunner):
        raise AppError("AG-UI is not configured", code="agui_unavailable", http_status=503)
    return runner


ContextDependency = Annotated[TrustedStorefrontContext, Depends(get_storefront_context)]
RunnerDependency = Annotated[AguiRunRunner, Depends(get_agui_runner)]


async def read_request_bytes(request: Request, limit: int) -> bytes:
    cached = getattr(request.state, "agui_body", None)
    if cached is not None:
        if len(cached) > limit:
            raise AppError("Request is too large", code="request_too_large", http_status=413)
        return cached
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise AppError("Request is too large", code="request_too_large", http_status=413)
    request.state.agui_body = bytes(body)
    return request.state.agui_body


async def read_json(request: Request, limit: int) -> dict:
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise AppError("Expected application/json", http_status=415)
    body = await read_request_bytes(request, limit)
    try:

        def reject_constant(value):
            raise ValueError("Non-finite number")

        value = json.loads(body, parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise invalid_input()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise invalid_input() from None


@router.post("/api/agent")
@router.post("/agui", include_in_schema=False)
async def run_agent(request: Request, trusted: ContextDependency, runner: RunnerDependency):
    runner.authorize(trusted)
    try:
        input = RunAgentInput.model_validate(
            await read_json(request, runner.input_adapter.max_request_bytes)
        )
    except ValidationError:
        raise invalid_input() from None
    runner.input_adapter.adapt(input)
    output = runner.run(input, trusted)
    # Validate/reserve before sending HTTP headers; execution continues in the response task.
    first = await anext(output)
    encoder = EventEncoder(accept="text/event-stream")

    async def body():
        async with aclosing(output):
            yield encoder.encode(first)
            async for event in output:
                yield encoder.encode(event)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/api/agent/tool-results")
async def tool_result(request: Request, trusted: ContextDependency, runner: RunnerDependency):
    runner.authorize(trusted)
    try:
        input = ToolResultInput.model_validate(
            await read_json(request, runner.input_adapter.max_request_bytes)
        )
    except ValidationError:
        raise invalid_input() from None
    return {"accepted": await runner.handle_tool_result(input, trusted)}
