"""M10 SDK tool contracts and authorization regressions."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from agent_adapter_service.agent.config.loader import ConfigError
from agent_adapter_service.agent.config.models import ToolConfig
from agent_adapter_service.agent.tools import (
    ActiveToolContexts,
    AgentToolContext,
    FrontendApproval,
    ToolDependencies,
    register_all,
)
from agent_adapter_service.conversations.models import ConversationBinding, ConversationContext
from agent_adapter_service.core.exceptions import AuthorizationError
from agent_adapter_service.core.types import RiskLevel
from agent_adapter_service.customer_identity.models import ResolvedCustomerIdentity
from agent_adapter_service.frontend.state.manager import FrontendStateManager
from agent_adapter_service.frontend.tools.dispatcher import FrontendToolDispatcher
from agent_adapter_service.frontend.tools.models import FrontendToolCall, FrontendToolResult
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.rag.models import KnowledgeAnswer, DocumentSource
from agent_adapter_service.saleor.errors import SaleorPermissionError
from agent_adapter_service.saleor.models import Page, PageInfo, ProductSummary
from agent_adapter_service.saleor.services.order_service import OrderService
from agent_adapter_service.security.customer_policy import CustomerToolPolicy
from agent_adapter_service.security.frontend_tool_policy import FrontendToolAuthorizationPolicy

p = pytest.importorskip("parlant.sdk")


@pytest.fixture
async def env():
    scope = StoreScope(tenant_id="tenant", store_id="store")
    contexts = ActiveToolContexts(scope)
    states = FrontendStateManager(scope)
    await states.update(
        "thread",
        {
            "version": 1,
            "store": {"channel": "web"},
            "product": {"id": "P1"},
            "cart": {"item_count": 2},
            "checkout": {"step": "shipping"},
        },
    )
    products, orders, rag = AsyncMock(), AsyncMock(spec=OrderService), AsyncMock()
    deps = ToolDependencies(
        scope,
        contexts,
        states,
        CustomerToolPolicy(scope, orders=orders),
        FrontendToolAuthorizationPolicy(scope),
        products,
        orders,
        rag,
    )
    registry = register_all(deps)
    tools = await registry.register(tuple(ToolConfig(name=n) for n in registry.available_tools))
    identity = ResolvedCustomerIdentity(
        visitor_id="visitor",
        saleor_user_id="U1",
        parlant_customer_id="customer",
        status="authenticated",
    )
    conversation = ConversationContext(
        binding=ConversationBinding(
            thread_id="thread",
            parlant_session_id="session",
            parlant_customer_id="customer",
            agent_id="agent",
        ),
        identity=identity,
        run_id="run",
    )
    active = AgentToolContext(conversation, scope, trace_id="trace")
    sdk = p.ToolContext(agent_id="agent", session_id="session", customer_id="customer")
    return deps, registry, tools, active, sdk


async def test_real_sdk_schema_and_whitelist(env):
    deps, registry, tools, active, sdk = env
    assert len(tools) == 14
    for tool in tools.values():
        assert not set(tool.tool.parameters) & {
            "context",
            "customer_id",
            "saleor_user_id",
            "session_id",
            "thread_id",
            "confirmed",
            "confirmation_id",
            "channel",
            "risk",
        }
    with pytest.raises(ConfigError):
        registry.add(tools["get_cart"])
    for configs in (
        (ToolConfig(name="place_order"),),
        (ToolConfig(name="get_cart"), ToolConfig(name="get_cart")),
        (ToolConfig(name="get_cart", confirmation="deny"),),
        (ToolConfig(name="get_cart", confirmation="confirm"),),
        (ToolConfig(name="navigate", risk="high"),),
    ):
        with pytest.raises(ConfigError):
            await registry.register(configs)
    await registry.register((ToolConfig(name="get_cart", enabled=False),))
    async with deps.contexts.bind(active):
        assert (await tools["get_cart"](sdk)).data == {"error": "not_authorized"}


@pytest.mark.parametrize("field", ["agent_id", "session_id", "customer_id"])
async def test_context_mismatch_blocks_all_service_access(env, field):
    deps, _, tools, active, sdk = env
    setattr(sdk, field, "other")
    async with deps.contexts.bind(active):
        result = await tools["get_my_orders"](sdk)
    assert result.data == {"error": "not_authorized"}
    deps.orders.list_customer_orders.assert_not_awaited()


async def test_context_scope_lifecycle_and_overlapping_runs(env):
    deps, _, tools, active, sdk = env
    assert (await tools["get_cart"](sdk)).data == {"error": "not_authorized"}
    with pytest.raises(AuthorizationError):
        async with deps.contexts.bind(
            replace(active, scope=StoreScope(tenant_id="t", store_id="s"))
        ):
            pass
    async with deps.contexts.bind(active):
        with pytest.raises(AuthorizationError):
            async with deps.contexts.bind(active):
                pass
        assert (await tools["get_cart"](sdk)).data["cart"]["item_count"] == 2
    assert (await tools["get_cart"](sdk)).data == {"error": "not_authorized"}


def product():
    return ProductSummary(
        id="P1",
        name="Jacket",
        slug="jacket",
        thumbnail=None,
        is_available=True,
        is_available_for_purchase=True,
        pricing=None,
    )


async def test_product_pagination_channel_and_page_context(env):
    deps, _, tools, active, sdk = env
    deps.products.search_products.return_value = Page(
        items=[product()],
        page_info=PageInfo(
            has_next_page=True, has_previous_page=False, start_cursor="a", end_cursor="b"
        ),
    )
    deps.products.get_product.return_value = product()
    deps.products.get_variants.return_value = deps.products.search_products.return_value
    async with deps.contexts.bind(active):
        result = await tools["search_products"](sdk, "jacket", first=5, after="cursor")
        assert result.data["page_info"]["end_cursor"] == "b"
        json.dumps(result.data)
        await tools["get_product"](sdk)
        await tools["get_product_variants"](sdk)
    deps.products.search_products.assert_awaited_once_with("jacket", "web", first=5, after="cursor")
    deps.products.get_product.assert_awaited_once_with("web", id="P1", slug=None)
    deps.products.get_variants.assert_awaited_once_with(
        "P1", "web", first=10, after=None, attribute_limit=20
    )


@pytest.mark.parametrize("first", [0, 21, True])
async def test_page_size_rejected_before_service(env, first):
    deps, _, tools, active, sdk = env
    async with deps.contexts.bind(active):
        assert "error" in (await tools["search_products"](sdk, "q", first=first)).data
    deps.products.search_products.assert_not_awaited()


@pytest.mark.parametrize("status", ["anonymous", "unavailable"])
async def test_order_tools_reject_missing_current_auth_even_with_retained_id(env, status):
    deps, _, tools, active, sdk = env
    identity = active.conversation.identity.model_copy(update={"status": status})
    active = replace(
        active, conversation=active.conversation.model_copy(update={"identity": identity})
    )
    async with deps.contexts.bind(active):
        assert (await tools["get_my_orders"](sdk)).data == {"error": "not_authorized"}
        assert (await tools["get_my_order"](sdk, "O1")).data == {"error": "not_authorized"}
    deps.orders.list_customer_orders.assert_not_awaited()
    deps.orders.assert_order_ownership.assert_not_awaited()


async def test_order_ownership_before_read_and_identity_from_context(env):
    deps, _, tools, active, sdk = env
    deps.orders.list_customer_orders.return_value = {"items": []}
    deps.orders.assert_order_ownership.side_effect = SaleorPermissionError("secret user ID")
    async with deps.contexts.bind(active):
        await tools["get_my_orders"](sdk)
        assert (await tools["get_my_order"](sdk, "foreign")).data == {"error": "not_authorized"}
    assert deps.orders.list_customer_orders.call_args.args[0].customer_id() == "U1"
    deps.orders.get_customer_order.assert_not_awaited()


async def test_knowledge_sources_and_trace_preserved(env):
    deps, _, tools, active, sdk = env
    deps.rag.search_knowledge.return_value = KnowledgeAnswer(
        answer="Warm jacket",
        sources=[DocumentSource(document_id="doc", title="Guide")],
    )
    async with deps.contexts.bind(active):
        result = await tools["search_knowledge"](sdk, "winter?")
    assert result.data["answer"] == "Warm jacket"
    assert result.metadata["sources"][0]["document_id"] == "doc"
    json.dumps(result.metadata)
    deps.rag.search_knowledge.assert_awaited_once_with(
        "winter?", collections=None, trace_id="trace"
    )


@pytest.mark.parametrize("name,field", [("get_cart", "cart"), ("get_checkout_summary", "checkout")])
async def test_frontend_summaries_never_claim_commerce_authority(env, name, field):
    deps, _, tools, active, sdk = env
    async with deps.contexts.bind(active):
        result = await tools[name](sdk)
        assert result.data["authoritative"] is False
        assert result.data["source"] == "frontend"
        await deps.states.clear()
        result = await tools[name](sdk)
        assert result.data[field] is None
        assert result.data["source"] == "unavailable"


async def test_browser_add_requires_server_approval_and_returns_new_state(env):
    deps, _, tools, active, sdk = env
    dispatcher = AsyncMock()
    active = replace(active, dispatcher=dispatcher)
    async with deps.contexts.bind(active):
        result = await tools["add_to_cart"](sdk, "P1", "V1")
    assert result.data == {"error": "confirmation_required"}
    dispatcher.dispatch.assert_not_awaited()
    approval = FrontendApproval(
        FrontendToolCall(
            name="add_to_cart",
            thread_id="thread",
            risk=RiskLevel.MEDIUM,
            arguments={"product_id": "P1", "variant_id": "V1", "quantity": 1},
        ),
        "approval",
    )
    verifier = AsyncMock()
    verifier.consume.side_effect = [True, False]
    deps.frontend_policy.confirmations = verifier
    active = replace(active, approvals=(approval,))

    async def receipt(call, *, confirmed):
        assert confirmed is True
        await deps.states.update(
            "thread",
            {
                "base_version": 1,
                "version": 2,
                "changes": {"cart": {"item_count": 3}},
            },
        )
        return FrontendToolResult(call_id=call.call_id, status="success", resulting_state_version=2)

    dispatcher.dispatch.side_effect = receipt
    async with deps.contexts.bind(active):
        result = await tools["add_to_cart"](sdk, "P1", "V1")
        replay = await tools["add_to_cart"](sdk, "P1", "V1")
    assert result.data["frontend_state"]["cart"]["item_count"] == 3
    assert replay.data == {"error": "confirmation_required"}
    dispatcher.dispatch.assert_awaited_once()


@pytest.mark.parametrize(
    "name,args",
    [
        ("navigate", {"pathname": "/products/jacket"}),
        ("open_product", {"product_id": "P1"}),
        ("select_variant", {"product_id": "P1", "variant_id": "V1"}),
        ("open_cart", {}),
        ("open_checkout", {}),
    ],
)
async def test_frontend_tools_pass_real_policy(env, name, args):
    deps, _, tools, active, sdk = env
    dispatcher = AsyncMock()
    dispatcher.dispatch.side_effect = lambda call, **kw: FrontendToolResult(
        call_id=call.call_id, status="success"
    )
    async with deps.contexts.bind(replace(active, dispatcher=dispatcher)):
        result = await tools[name](sdk, **args)
    assert result.data["result"]["status"] == "success"
    call = dispatcher.dispatch.call_args.args[0]
    assert (call.name, call.thread_id, call.arguments) == (name, "thread", args)


async def test_yaml_confirmation_strengthens_navigation_and_invalid_path_denied(env):
    deps, registry, tools, active, sdk = env
    dispatcher = AsyncMock()
    active = replace(active, dispatcher=dispatcher)
    async with deps.contexts.bind(active):
        result = await tools["navigate"](sdk, "https://evil.example")
        assert result.data == {"error": "invalid_arguments"}
        await registry.register((ToolConfig(name="navigate", confirmation="confirm"),))
        result = await tools["navigate"](sdk, "/cart")
        assert result.data == {"error": "confirmation_required"}
    dispatcher.dispatch.assert_not_awaited()


async def test_errors_redacted_and_cancellation_propagates(env):
    deps, _, tools, active, sdk = env
    async with deps.contexts.bind(active):
        deps.rag.search_knowledge.side_effect = RuntimeError("Bearer secret-token")
        assert (await tools["search_knowledge"](sdk, "q")).data == {"error": "tool_failed"}
        deps.rag.search_knowledge.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await tools["search_knowledge"](sdk, "q")


async def test_real_dispatcher_timeout(env):
    deps, _, tools, active, sdk = env
    sink = AsyncMock()
    # Allow Windows scheduling/instrumentation overhead while testing missing receipts.
    dispatcher = FrontendToolDispatcher(deps.states, sink, timeout=0.2)
    try:
        async with deps.contexts.bind(replace(active, dispatcher=dispatcher)):
            result = await tools["open_cart"](sdk)
        assert result.data["result"]["status"] == "timeout"
        assert sink.send.await_count == 3
    finally:
        await dispatcher.close()


async def test_get_my_order_uses_customer_service_and_removes_owner(env):
    from unittest.mock import Mock

    deps, _, tools, active, sdk = env
    detail = Mock()
    detail.model_dump.return_value = {"id": "O1", "status": "FULFILLED"}
    deps.orders.get_customer_order.return_value = detail
    async with deps.contexts.bind(active):
        result = await tools["get_my_order"](sdk, "O1")
    assert result.data == {"id": "O1", "status": "FULFILLED"}
    assert deps.orders.assert_order_ownership.await_args.args[0].customer_id() == "U1"
    assert deps.orders.get_customer_order.await_args.args[0].customer_id() == "U1"
    detail.model_dump.assert_called_once_with(mode="json", exclude={"user"})


async def test_missing_channel_and_explicit_product_slug(env):
    deps, _, tools, active, sdk = env
    deps.products.get_product.return_value = product()
    async with deps.contexts.bind(active):
        await tools["get_product"](sdk, slug="other")
        deps.products.get_product.assert_awaited_once_with("web", id=None, slug="other")
        await deps.states.clear()
        assert (await tools["search_products"](sdk, "q")).data == {"error": "channel_required"}
    deps.products.search_products.assert_not_awaited()


async def test_bounded_results_and_citation_metadata_limit(env):
    deps, _, tools, active, sdk = env
    deps.products.get_product.return_value = {"description": "x" * 10000}
    deps.rag.search_knowledge.return_value = KnowledgeAnswer(answer="x" * 70000)
    async with deps.contexts.bind(active):
        result = await tools["get_product"](sdk)
        assert result.data["truncated"] is True
        assert len(result.data["result"]["description"]) == 2000
        result = await tools["search_knowledge"](sdk, "q")
        assert result.data["error"] == "result_too_large"


async def test_default_parlant_resource_injects_registry_and_cleans_up(env, monkeypatch):
    from unittest.mock import Mock

    from agent_adapter_service.app.lifespan import AppResources, parlant_resource
    from agent_adapter_service.core.settings import Settings

    _, registry, _, _, _ = env
    settings = Settings(_env_file=None)
    resources = AppResources(settings=settings, agent_tool_registry=registry)
    runtime = AsyncMock()
    factory = Mock(return_value=runtime)
    monkeypatch.setattr("agent_adapter_service.app.lifespan.AgentRuntime", factory)
    async with parlant_resource(settings, resources) as actual:
        assert actual is runtime
    factory.assert_called_once_with(settings, tool_registry=registry)
    runtime.start.assert_awaited_once()
    runtime.stop.assert_awaited_once()


async def test_customer_tool_denies_foreign_order_with_real_saleor_service(env):
    import httpx
    from pydantic import SecretStr

    from agent_adapter_service.saleor.client import SaleorGraphQLClient

    deps, _, tools, active, sdk = env
    calls = []

    async def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200, json={"data": {"order": {"id": "O1", "user": {"id": "foreign"}}}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SaleorGraphQLClient(
            "https://saleor.test/graphql/", SecretStr("secret"), http_client=http
        )
        deps.orders = OrderService(client)
        deps.customer_policy = CustomerToolPolicy(deps.scope, orders=deps.orders)
        try:
            async with deps.contexts.bind(active):
                result = await tools["get_my_order"](sdk, "O1")
            assert result.data == {"error": "not_authorized"}
            assert [call["operationName"] for call in calls] == ["OrderOwnershipById"]
        finally:
            await client.close()


async def test_tool_http_task_restores_trusted_run_context(env, monkeypatch):
    from contextvars import Context
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from agent_adapter_service.observability.tracing import traces, span
    from agent_adapter_service.observability.logging import bind_context, get_context

    deps, _, tools, active, sdk = env
    exporter, provider = InMemorySpanExporter(), TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(traces, "tracer", provider.get_tracer("test"))
    with bind_context(run_id="run", thread_id="thread"), span("agui.run") as parent:
        active = AgentToolContext(active.conversation, active.scope)
    async def search(*args, **kwargs):
        assert get_context()["run_id"] == "run"
        with span("saleor.graphql"):
            return {"items": []}
    deps.products.search_products.side_effect = search
    async with deps.contexts.bind(active):
        task = asyncio.create_task(tools["search_products"](sdk, "test"), context=Context())
        assert "error" not in (await task).data
    child = next(s for s in exporter.get_finished_spans() if s.name == "saleor.graphql")
    assert child.context.trace_id == parent.get_span_context().trace_id
    assert get_context() == {}
    provider.shutdown()
