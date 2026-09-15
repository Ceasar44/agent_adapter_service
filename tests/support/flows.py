"""Deterministic external boundaries; real policies, SQL, tools and transports remain active."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import parlant.sdk as p

from agent_adapter_service.agent.config.loader import AgentConfigLoader
from agent_adapter_service.agent.tools.common import ToolDependencies
from agent_adapter_service.agent.tools.registry import register_all
from agent_adapter_service.agui.router import get_storefront_context
from agent_adapter_service.observability.audit import AuditService
from agent_adapter_service.persistence.repositories.audit_log import SqlAuditLogRepository
from agent_adapter_service.saleor.services.registry import create_services
from agent_adapter_service.security.customer_policy import CustomerToolPolicy
from agent_adapter_service.security.frontend_tool_policy import FrontendToolAuthorizationPolicy
from tests.agui.conftest import agui, harness  # noqa: F401
from tests.agui.test_router import application
from tests.saleor.conftest import (  # noqa: F401
    channel,
    product,
    variant,
    order,
    page_info,
    harness as saleor_harness,
)


@pytest.fixture
async def flow(agui, saleor_harness, channel, product, variant, order, page_info):  # noqa: F811
    client, responses, calls = saleor_harness
    responses.update(
        {
            "ChannelBySlug": {"channel": channel},
            "ProductById": {"product": product},
            "ProductList": {
                "products": {"edges": [{"cursor": "one", "node": product}], "pageInfo": page_info}
            },
            "VariantList": {
                "product": {
                    "id": product["id"],
                    "productVariants": {
                        "edges": [{"cursor": "one", "node": variant}],
                        "pageInfo": page_info,
                    },
                }
            },
            "OrderOwnershipById": {"order": {"id": order["id"], "user": order["user"]}},
            "OrderById": {"order": order},
            "OrdersByCustomer": {
                "orders": {"edges": [{"cursor": "one", "node": order}], "pageInfo": page_info}
            },
        }
    )
    services = create_services(client)
    scope = agui.trusted.scope
    audit = AuditService(SqlAuditLogRepository(agui.harness.database, scope))
    deps = ToolDependencies(
        scope,
        agui.runner.contexts,
        agui.runner.states,
        CustomerToolPolicy(scope, orders=services["order"]),
        FrontendToolAuthorizationPolicy(scope, audit=audit),
        products=services["product"],
        orders=services["order"],
        audit=audit,
    )
    registry = register_all(deps)
    config = AgentConfigLoader("configs/agents/customer_service").load()
    tools = await registry.register(config.tools)

    async def context(session, agent):
        record = await agui.harness.sessions.get(session)
        return p.ToolContext(session_id=session, agent_id=agent, customer_id=record.customer_id)

    return SimpleNamespace(
        agui=agui,
        deps=deps,
        tools=tools,
        context=context,
        responses=responses,
        calls=calls,
        services=services,
        config=config,
    )


async def storefront_run(agui, payload, *, trusted=None, browser=None):  # noqa: F811
    """Drive streaming ASGI and submit browser receipts over a second HTTP request."""
    app = application(agui)
    app.dependency_overrides[get_storefront_context] = lambda: trusted or agui.trusted
    events, statuses = [], []
    sent = False
    buffer = ""
    tools = {}

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {
                "type": "http.request",
                "body": payload.model_dump_json(by_alias=True).encode(),
                "more_body": False,
            }
        await asyncio.Event().wait()

    async def send(message):
        nonlocal buffer
        if message["type"] == "http.response.start":
            statuses.append(message["status"])
        if message["type"] != "http.response.body":
            return
        buffer += message.get("body", b"").decode()
        while "\n\n" in buffer:
            packet, buffer = buffer.split("\n\n", 1)
            for line in packet.splitlines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                events.append(event)
                if event["type"] == "TOOL_CALL_START":
                    tools[event["toolCallId"]] = event["toolCallName"]
                if event["type"] == "TOOL_CALL_END" and browser:
                    call_id = event["toolCallId"]
                    receipt = browser(tools[call_id], call_id)
                    response = await http.post(
                        "/api/agent/tool-results",
                        json={
                            "threadId": payload.thread_id,
                            "runId": payload.run_id,
                            **receipt,
                        },
                    )
                    assert response.status_code == 200
                    assert response.json() == {"accepted": True}

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
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        await asyncio.wait_for(app(scope, receive, send), timeout=10)
    assert statuses == [200]
    assert not agui.runner.contexts._active
    return events
