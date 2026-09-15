import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from agent_adapter_service.agui.router import get_storefront_context
from agent_adapter_service.app.factory import create_app
from agent_adapter_service.core.settings import Settings
from tests.agui.conftest import run_input, wait_until
from tests.security.test_storefront_bff import signed, verifier
from agent_adapter_service.persistence.repositories.bff_nonce import SqlBffNonceRepository


def application(agui):
    @asynccontextmanager
    async def resource(settings, resources):
        resources.services["agui_runner"] = agui.runner
        yield object()

    app = create_app(
        Settings(_env_file=None, app_env="test"), resource_factories={"parlant_runtime": resource}
    )
    return app


@pytest.mark.parametrize("path", ["/api/agent", "/agui", "/api/agent/tool-results"])
async def test_real_bff_dependency_body_reuse_and_replay(agui, path):
    app = application(agui)
    claims = {
        "iss": "storefront", "aud": "adapter", "iat": 1800000000, "exp": 1800000060,
        "jti": "0123456789abcdef", "scope": agui.trusted.scope.model_dump(),
        "identity": agui.trusted.identity.model_dump(exclude_none=True, mode="json"),
    }
    payload = (dict(threadId="t1", runId="r1", result={"call_id": "unknown", "status": "success"})
               if path.endswith("tool-results") else run_input().model_dump(by_alias=True))
    body = json.dumps(payload).encode()
    headers = {"content-type": "application/json",
               "x-storefront-authorization": signed(claims, body=body, path=path)}
    async with app.router.lifespan_context(app):
        app.state.storefront_bff_verifier = verifier(SqlBffNonceRepository(agui.harness.database))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.post(path, content=body)).status_code == 401
            assert (await client.post(path, content=body + b" ", headers=headers)).status_code == 401
            assert (await client.post(path + "?x=1", content=body, headers=headers)).status_code == 401
            response = await client.post(path, content=body, headers=headers)
            assert response.status_code == 200, response.text
            assert (await client.post(path, content=body, headers=headers)).status_code == 409


async def test_request_state_cannot_bypass_bff(agui):
    app = application(agui)

    @app.middleware("http")
    async def inject(request, call_next):
        request.state.storefront_context = agui.trusted
        return await call_next(request)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.post("/api/agent", json={})).status_code == 401


async def test_http_authentication_validation_and_sse(agui):
    app = application(agui)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = run_input().model_dump(by_alias=True)
            result = await client.post(
                "/api/agent",
                json=payload,
                headers={"x-visitor-id": "v1", "x-saleor-user-id": "admin"},
            )
            assert result.status_code == 401 and not agui.events.writes
            app.dependency_overrides[get_storefront_context] = lambda: agui.trusted
            invalid = await client.post("/api/agent", json={"password": "super-secret"})
            assert invalid.status_code == 422 and "super-secret" not in invalid.text
            too_big = await client.post(
                "/api/agent", content="x" * 1048577, headers={"content-type": "application/json"}
            )
            assert too_big.status_code == 413
            for path in ["/api/agent", "/agui"]:
                response = await client.post(path, json=payload)
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                assert response.headers["x-accel-buffering"] == "no"
                events = [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                ]
                assert events[0]["type"] == "RUN_STARTED" and events[-1]["type"] == "RUN_FINISHED"
            assert len(agui.events.writes) == 1
            result = await client.post(
                "/api/agent/tool-results",
                json={
                    "threadId": "t1",
                    "runId": "r1",
                    "result": {"call_id": "unknown", "status": "success"},
                },
            )
            assert result.json() == {"accepted": False}


async def test_asgi_disconnect_cancels_engine_and_pending_tool(agui):
    from agent_adapter_service.frontend.tools.models import FrontendToolCall

    started, cleaned = asyncio.Event(), asyncio.Event()

    async def process(session, agent_id, *, run_id):
        active = agui.runner._active["t1"]
        started.set()
        try:
            await active.dispatcher.dispatch(FrontendToolCall(thread_id="t1", name="open_cart"))
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.02)
            cleaned.set()

    agui.processing.process.side_effect = process
    app = application(agui)
    app.dependency_overrides[get_storefront_context] = lambda: agui.trusted
    received_request = False
    disconnect = asyncio.Event()
    sent = []

    async def receive():
        nonlocal received_request
        if not received_request:
            received_request = True
            return {
                "type": "http.request",
                "body": run_input().model_dump_json(by_alias=True).encode(),
                "more_body": False,
            }
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and b"TOOL_CALL_END" in message.get("body", b""):
            disconnect.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "method": "POST",
        "scheme": "http",
        "path": "/api/agent",
        "raw_path": b"/api/agent",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "server": ("test", 80),
        "client": ("test", 123),
        "http_version": "1.1",
    }
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(app(scope, receive, send), timeout=3)
    assert started.is_set() and cleaned.is_set()
    assert not agui.runner._reserved and not agui.runner.contexts._active
    assert sent[0]["status"] == 200


async def test_receipt_over_second_http_request(agui):
    from agent_adapter_service.frontend.tools.models import FrontendToolCall

    async def process(session, agent_id, *, run_id):
        dispatcher = agui.runner._active["t1"].dispatcher
        result = await dispatcher.dispatch(
            FrontendToolCall(call_id="http-call", thread_id="t1", name="open_cart")
        )
        assert result.status == "success"

    agui.processing.process.side_effect = process
    app = application(agui)
    app.dependency_overrides[get_storefront_context] = lambda: agui.trusted
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            run = asyncio.create_task(
                client.post("/api/agent", json=run_input().model_dump(by_alias=True))
            )
            await wait_until(lambda: "t1" in agui.runner._active)
            dispatcher = agui.runner._active["t1"].dispatcher
            await wait_until(
                lambda: (
                    "http-call" in dispatcher._pending
                    and dispatcher._pending["http-call"].accepting_result
                )
            )
            result = await client.post(
                "/api/agent/tool-results",
                json={
                    "threadId": "t1",
                    "runId": "r1",
                    "result": {"call_id": "http-call", "status": "success"},
                },
            )
            assert result.json() == {"accepted": True}
            response = await run
            assert "RUN_FINISHED" in response.text and "RUN_ERROR" not in response.text
