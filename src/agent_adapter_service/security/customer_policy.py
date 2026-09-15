"""Customer allowlist and ownership checks through the existing Saleor service."""

from typing import Protocol

from agent_adapter_service.core.exceptions import AuthorizationError
from agent_adapter_service.customer_identity.models import IdentityStatus
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.saleor.auth import CustomerContext
from agent_adapter_service.saleor.errors import SaleorNotFoundError, SaleorPermissionError
from agent_adapter_service.security.authorization import (
    AuthorizationContext,
    AuthorizationService,
    AuthorizationSurface,
)


class OrderOwnershipChecker(Protocol):
    async def assert_order_ownership(self, context: CustomerContext, id: str) -> None: ...


class CustomerToolPolicy:
    """One trusted store; the injected OrderService must use that store's backend.

    Frontend capabilities also require FrontendToolAuthorizationPolicy before
    execution. A customer allowlist grant alone never confirms a browser action.
    """

    allowed_tools = frozenset(
        {
            "search_products",
            "get_product",
            "get_product_variants",
            "get_my_orders",
            "get_my_order",
            "search_knowledge",
            "get_cart",
            "get_checkout_summary",
            "navigate",
            "open_product",
            "select_variant",
            "open_cart",
            "open_checkout",
            "add_to_cart",
            "remove_from_cart",
            "change_quantity",
            "highlight_element",
            "scroll_to",
        }
    )
    authenticated_tools = frozenset({"get_my_orders", "get_my_order"})

    def __init__(
        self, store_scope: StoreScope, *, orders: OrderOwnershipChecker | None = None
    ) -> None:
        self.store_scope = store_scope
        self.orders = orders
        self.authorization = AuthorizationService()

    async def require_tool_access(
        self, context: AuthorizationContext, tool_name: str, *, order_id: str | None = None
    ) -> None:
        self.authorization.check(
            context,
            lambda ctx: (
                ctx.surface == AuthorizationSurface.CUSTOMER_AGENT
                and ctx.store_scope == self.store_scope
                and ctx.customer_identity is not None
                and ctx.actor == ctx.customer_identity.parlant_customer_id
                and tool_name in self.allowed_tools
            ),
        )
        if tool_name in self.authenticated_tools:
            identity = context.customer_identity
            if (
                identity is None
                or identity.status != IdentityStatus.AUTHENTICATED
                or not identity.saleor_user_id
            ):
                raise AuthorizationError("Authenticated customer identity required")
            if tool_name == "get_my_order":
                if not order_id or self.orders is None:
                    raise AuthorizationError("Order ownership verification required")
                try:
                    await self.orders.assert_order_ownership(CustomerContext(identity), order_id)
                except (SaleorPermissionError, SaleorNotFoundError):
                    raise AuthorizationError("Order is unavailable to this customer") from None
