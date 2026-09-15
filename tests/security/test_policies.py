import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from agent_adapter_service.core.exceptions import AuthorizationError, IntegrationError
from agent_adapter_service.core.types import RiskLevel
from agent_adapter_service.customer_identity.models import IdentityStatus, ResolvedCustomerIdentity
from agent_adapter_service.frontend.tools.models import FrontendToolCall
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.saleor.auth import AdminContext
from agent_adapter_service.saleor.client import SaleorGraphQLClient
from agent_adapter_service.saleor.services.order_service import OrderService
from agent_adapter_service.security.admin_mcp_policy import (
    AdminMcpPolicy,
    AdminMcpPrincipal,
    sanitize_tool_arguments_for_audit,
)
from agent_adapter_service.security.authorization import (
    AuthorizationContext,
    AuthorizationService,
    AuthorizationSurface,
)
from agent_adapter_service.security.customer_policy import CustomerToolPolicy
from agent_adapter_service.security.frontend_tool_policy import FrontendToolAuthorizationPolicy


@pytest.fixture
def scope():
    return StoreScope(tenant_id="tenant", store_id="store")


@pytest.fixture
def context(scope):
    return AuthorizationContext(
        actor="customer",
        surface="customer_agent",
        store_scope=scope,
        customer_identity=ResolvedCustomerIdentity(
            visitor_id="visitor",
            parlant_customer_id="customer",
            saleor_user_id="user",
            status="authenticated",
        ),
        thread_id="thread",
        trace_id="trace",
    )


@pytest.fixture
def principal(scope):
    return AdminMcpPrincipal(
        client_id="client",
        app_id="app",
        **scope.model_dump(),
        scopes=frozenset(AdminMcpPolicy.tool_scopes.values()),
    )


def test_context_is_immutable_and_separates_customer_from_admin(context):
    with pytest.raises(ValidationError):
        context.actor = "other"
    for changes in ({"surface": "admin_mcp"}, {"actor": "other"}, {"unknown": True}):
        with pytest.raises(ValidationError):
            AuthorizationContext.model_validate(context.model_dump() | changes)


def test_unified_authorization_denies_truthy_non_bool_and_missing_scope(context):
    auth = AuthorizationService()
    auth.check(context, lambda ctx: ctx.actor == "customer")
    for predicate in (lambda ctx: False, lambda ctx: "yes", lambda ctx: 1):
        with pytest.raises(AuthorizationError) as error:
            auth.check(context, predicate)
        assert error.value.http_status == 403
    with pytest.raises(AuthorizationError):
        auth.require(context, "products:write")


@pytest.mark.parametrize("tool", list(AdminMcpPolicy.tool_scopes) + ["unknown", "place_order"])
async def test_customer_cannot_gain_admin_tools_even_with_scopes(context, scope, tool):
    context = context.model_copy(update={"scopes": frozenset(AdminMcpPolicy.tool_scopes.values())})
    with pytest.raises(AuthorizationError):
        await CustomerToolPolicy(scope).require_tool_access(context, tool)


@pytest.mark.parametrize("status", list(IdentityStatus))
async def test_order_authentication_is_checked_before_service(context, scope, status):
    checker = AsyncMock(spec=OrderService)
    identity = context.customer_identity.model_copy(update={"status": status})
    context = context.model_copy(update={"customer_identity": identity})
    policy = CustomerToolPolicy(scope, orders=checker)
    await policy.require_tool_access(context, "search_products")
    if status == IdentityStatus.AUTHENTICATED:
        await policy.require_tool_access(context, "get_my_order", order_id="order")
        assert checker.assert_order_ownership.await_args.args[0].customer_id() == "user"
    else:
        for tool in ("get_my_orders", "get_my_order"):
            with pytest.raises(AuthorizationError):
                await policy.require_tool_access(context, tool, order_id="order")
        checker.assert_order_ownership.assert_not_called()


@pytest.mark.parametrize("owner", ["user", "other", None])
async def test_policy_uses_real_order_service_with_fake_graphql(context, scope, owner):
    calls = []

    async def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "data": {
                    "order": {
                        "id": "order",
                        "user": {"id": owner} if owner else None,
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = SaleorGraphQLClient(
            "https://saleor.test/graphql/", SecretStr("secret"), http_client=http
        )
        policy = CustomerToolPolicy(scope, orders=OrderService(client))
        if owner == "user":
            await policy.require_tool_access(context, "get_my_order", order_id="order")
        else:
            with pytest.raises(AuthorizationError):
                await policy.require_tool_access(context, "get_my_order", order_id="order")
        assert [call["operationName"] for call in calls] == ["OrderOwnershipById"]
        await client.close()


async def test_missing_checker_or_order_and_upstream_failure_fail_closed(context, scope):
    checker = AsyncMock(spec=OrderService)
    checker.assert_order_ownership.side_effect = IntegrationError("Unavailable")
    with pytest.raises(AuthorizationError):
        await CustomerToolPolicy(scope).require_tool_access(context, "get_my_order", order_id="o")
    policy = CustomerToolPolicy(scope, orders=checker)
    with pytest.raises(AuthorizationError):
        await policy.require_tool_access(context, "get_my_order")
    with pytest.raises(IntegrationError):
        await policy.require_tool_access(context, "get_my_order", order_id="o")


@pytest.mark.parametrize("tool,required", list(AdminMcpPolicy.tool_scopes.items()))
def test_each_admin_tool_requires_exact_scope(scope, principal, tool, required):
    policy = AdminMcpPolicy(scope)
    assert policy.require_tool_access(principal, tool) == AdminContext("client")
    without_scope = principal.model_copy(
        update={
            "scopes": principal.scopes - {required},
            "roles": frozenset({"admin"}),
        }
    )
    with pytest.raises(AuthorizationError):
        policy.require_tool_access(without_scope, tool)


def test_admin_rejects_customer_or_unknown_tool(scope, principal, context):
    policy = AdminMcpPolicy(scope)
    for value in (context, context.customer_identity, AdminContext("client"), None):
        with pytest.raises(AuthorizationError):
            policy.require_tool_access(value, "update_product")
    with pytest.raises(AuthorizationError):
        policy.require_tool_access(principal, "unregistered_tool")


@pytest.mark.parametrize("field", ["tenant_id", "store_id"])
async def test_all_surfaces_reject_foreign_scope(scope, context, principal, field):
    foreign = scope.model_copy(update={field: "foreign"})
    with pytest.raises(AuthorizationError):
        await CustomerToolPolicy(foreign).require_tool_access(context, "search_products")
    with pytest.raises(AuthorizationError):
        AdminMcpPolicy(foreign).require_tool_access(principal, "update_product")
    with pytest.raises(AuthorizationError):
        await FrontendToolAuthorizationPolicy(foreign).require_tool_access(
            context, FrontendToolCall(name="open_cart", thread_id="thread")
        )


@pytest.mark.parametrize(
    "name,args",
    [
        ("open_product", {"product_id": "p"}),
        ("select_variant", {"product_id": "p", "variant_id": "v"}),
        ("navigate", {"pathname": "/products/p"}),
        ("open_cart", {}),
        ("open_checkout", {}),
    ],
)
async def test_frontend_navigation_auto(context, scope, name, args):
    call = FrontendToolCall(name=name, arguments=args, thread_id="thread")
    assert await FrontendToolAuthorizationPolicy(scope).require_tool_access(context, call) == call


@pytest.mark.parametrize(
    "name,args",
    [
        ("place_order", {}),
        ("submit_payment", {}),
        ("delete_account", {}),
        ("unknown", {}),
        ("navigate", {"pathname": "https://evil.test"}),
        ("open_cart", {"confirmed": True}),
    ],
)
async def test_approval_never_overrides_denied_tools_or_arguments(context, scope, name, args):
    verifier = AsyncMock()
    verifier.consume.return_value = True
    policy = FrontendToolAuthorizationPolicy(scope, confirmations=verifier)
    dispatcher = AsyncMock()
    with pytest.raises(AuthorizationError):
        await policy.dispatch(
            context,
            FrontendToolCall(name=name, arguments=args, thread_id="thread"),
            dispatcher,
            confirmation_id="approval",
        )
    verifier.consume.assert_not_called()
    dispatcher.dispatch.assert_not_called()


async def test_frontend_rejects_foreign_thread_and_admin(context, scope):
    policy = FrontendToolAuthorizationPolicy(scope)
    with pytest.raises(AuthorizationError):
        await policy.require_tool_access(context, FrontendToolCall(name="open_cart", thread_id="x"))
    admin = AuthorizationContext(actor="admin", surface="admin_mcp", store_scope=scope)
    with pytest.raises(AuthorizationError):
        await policy.require_tool_access(
            admin, FrontendToolCall(name="open_cart", thread_id="thread")
        )


@pytest.mark.parametrize(
    "name,args,risk",
    [
        ("add_to_cart", {"product_id": "p", "variant_id": "v"}, RiskLevel.LOW),
        ("remove_from_cart", {"line_id": "line"}, RiskLevel.LOW),
        ("change_quantity", {"line_id": "line", "quantity": 2}, RiskLevel.LOW),
        ("open_cart", {}, RiskLevel.HIGH),
    ],
)
async def test_confirmation_required_by_default(context, scope, name, args, risk):
    with pytest.raises(AuthorizationError) as error:
        await FrontendToolAuthorizationPolicy(scope).require_tool_access(
            context,
            FrontendToolCall(name=name, arguments=args, thread_id="thread", risk=risk),
            confirmation_id="unverified-browser-value",
        )
    assert error.value.code == "confirmation_required"


async def test_confirmation_receives_exact_normalized_action(context, scope):
    # A fake approval adapter implements the atomic single-use contract.
    class Approvals:
        used = False

        async def consume(self, confirmation_id, received_context, call):
            assert received_context == context
            assert call.arguments == {"product_id": "p", "variant_id": "v", "quantity": 1}
            assert call.call_id == "call"
            assert call.risk == RiskLevel.MEDIUM
            if confirmation_id != "approval" or self.used:
                return False
            self.used = True
            return True

    policy = FrontendToolAuthorizationPolicy(scope, confirmations=Approvals())
    call = FrontendToolCall(
        name="add_to_cart",
        call_id="call",
        thread_id="thread",
        arguments={"product_id": "p", "variant_id": "v"},
    )
    dispatcher = AsyncMock()
    results = await asyncio.gather(
        *(policy.dispatch(context, call, dispatcher, confirmation_id="approval") for _ in range(2)),
        return_exceptions=True,
    )
    assert sum(isinstance(result, AuthorizationError) for result in results) == 1
    dispatcher.dispatch.assert_awaited_once()
    assert dispatcher.dispatch.await_args.kwargs == {"confirmed": True}


async def test_customer_policy_rejects_admin_surface(scope):
    context = AuthorizationContext(
        actor="admin", surface=AuthorizationSurface.ADMIN_MCP, store_scope=scope
    )
    with pytest.raises(AuthorizationError):
        await CustomerToolPolicy(scope).require_tool_access(context, "get_product")


def test_audit_redaction_is_nested_bounded_and_does_not_mutate():
    original = {
        "access_token": "secret",
        "email": "alice@example.test",
        "quantity": 2,
        "nested": {"items": [{"password": "pass", "currency": "USD"}]},
        "free_text": "sensitive" * 10000,
        "alice@example.test": "hidden",
    }
    before = json.dumps(original)
    summary = sanitize_tool_arguments_for_audit(original)
    rendered = json.dumps(summary)
    for sensitive in ("secret", "alice@example.test", '"pass"', "sensitive", "hidden"):
        assert sensitive not in rendered
    assert summary["quantity"] == 2
    assert summary["nested"]["items"][0]["currency"] == "USD"
    assert before == json.dumps(original)
    assert sanitize_tool_arguments_for_audit(original, max_bytes=32) == {"_truncated": True}
    with pytest.raises(ValueError):
        sanitize_tool_arguments_for_audit({}, max_bytes=1)


async def test_frontend_audit_records_denial_success_and_blocks_on_outage(scope, context):
    from types import SimpleNamespace
    from agent_adapter_service.observability.audit import AuditService
    from agent_adapter_service.frontend.tools.models import FrontendToolResult

    repository = SimpleNamespace(append=AsyncMock())
    policy = FrontendToolAuthorizationPolicy(scope, audit=AuditService(repository))
    call = FrontendToolCall(name="open_cart", arguments={}, thread_id="thread")
    dispatcher = SimpleNamespace(dispatch=AsyncMock(return_value=FrontendToolResult(
        call_id=call.call_id, status="success"
    )))
    await policy.dispatch(context, call, dispatcher)
    assert [c.args[0].result_status for c in repository.append.call_args_list] == [
        "pending", "success"
    ]
    repository.append.reset_mock()
    dispatcher.dispatch.reset_mock()
    repository.append.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        await policy.dispatch(context, call, dispatcher)
    dispatcher.dispatch.assert_not_awaited()
    repository.append.side_effect = None
    repository.append.reset_mock()
    with pytest.raises(AuthorizationError):
        await policy.dispatch(context, call.model_copy(update={"name": "place_order"}), dispatcher)
    assert repository.append.call_args.args[0].result_status == "denied"
    dispatcher.dispatch.assert_not_awaited()
