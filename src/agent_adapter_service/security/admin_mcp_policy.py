"""Authorize verified admin principals independently of customer identity."""

import json
from types import MappingProxyType
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent_adapter_service.core.exceptions import AuthorizationError
from agent_adapter_service.persistence.contracts import Identifier, StoreScope
from agent_adapter_service.persistence.sanitization import sanitize_arguments
from agent_adapter_service.saleor.auth import AdminContext
from agent_adapter_service.security.authorization import (
    AuthorizationContext,
    AuthorizationService,
    AuthorizationSurface,
)


class AdminMcpPrincipal(BaseModel):
    """Server-verified client/app credentials; never deserialize from tool arguments.

    Roles are descriptive only. The authentication adapter must explicitly map
    roles to granted scopes; neither 'admin' nor '*' grants implicit privileges.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: Identifier
    app_id: Identifier | None = None
    scopes: frozenset[Identifier] = frozenset()
    roles: frozenset[Annotated[str, Field(min_length=1, max_length=100)]] = frozenset()
    tenant_id: Identifier
    store_id: Identifier

    @property
    def store_scope(self) -> StoreScope:
        return StoreScope(tenant_id=self.tenant_id, store_id=self.store_id)


class AdminMcpPolicy:
    tool_scopes = MappingProxyType(
        {
            "create_product": "products:write",
            "update_product": "products:write",
            "publish_product": "products:write",
            "create_variant": "products:write",
            "update_variant": "products:write",
            "update_variant_price": "products:write",
            "get_admin_order": "orders:read",
            "get_order": "orders:read",
            "cancel_order": "orders:cancel",
            "fulfill_order": "orders:fulfill",
            "get_stock": "inventory:read",
            "create_stock": "inventory:write",
            "update_stock": "inventory:write",
            "update_inventory": "inventory:write",
            "get_admin_customer": "customers:read",
            "get_customer": "customers:read",
            "create_customer": "customers:write",
            "update_customer": "customers:write",
            "list_channels": "channels:read",
            "get_channel": "channels:read",
            "create_collection": "collections:write",
            "update_collection": "collections:write",
            "add_products_to_collection": "collections:write",
            "create_promotion": "promotions:write",
            "update_promotion": "promotions:write",
            "disable_promotion": "promotions:write",
            "create_promotion_rule": "promotions:write",
            "update_promotion_rule": "promotions:write",
        }
    )

    def __init__(self, store_scope: StoreScope) -> None:
        self.store_scope = store_scope
        self.authorization = AuthorizationService()

    def require_tool_access(
        self, principal: AdminMcpPrincipal, tool_name: str, *, trace_id: str | None = None
    ) -> AdminContext:
        if (
            not isinstance(principal, AdminMcpPrincipal)
            or principal.store_scope != self.store_scope
            or tool_name not in self.tool_scopes
        ):
            raise AuthorizationError("Admin tool is not authorized")
        context = AuthorizationContext(
            actor=principal.client_id,
            surface=AuthorizationSurface.ADMIN_MCP,
            store_scope=principal.store_scope,
            scopes=principal.scopes,
            trace_id=trace_id,
        )
        self.authorization.require(context, self.tool_scopes[tool_name])
        return AdminContext(principal_id=principal.client_id)


def sanitize_tool_arguments_for_audit(
    arguments: dict[str, JsonValue], *, max_bytes: int = 8192
) -> dict[str, JsonValue]:
    """Reuse persistence's conservative redaction, with a total UTF-8 output cap.

    Never retain free text, credentials, PII or arbitrary field names. A structural
    summary that exceeds the budget is replaced entirely, never sliced mid-JSON.
    """
    if max_bytes < 32:
        raise ValueError("Audit summary byte limit must be at least 32")
    summary = sanitize_arguments(arguments)
    if len(json.dumps(summary, ensure_ascii=False, allow_nan=False).encode("utf-8")) > max_bytes:
        return {"_truncated": True}
    return summary
