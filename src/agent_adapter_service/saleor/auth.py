"""Construct contexts only in trusted server adapters, never from tool arguments."""

from dataclasses import dataclass, field
from typing import TypeAlias

from pydantic import SecretStr

from agent_adapter_service.customer_identity.models import ResolvedCustomerIdentity

from .errors import SaleorPermissionError, SaleorValidationError


@dataclass(frozen=True)
class PublicContext:
    """Unauthenticated storefront reads: no service privileges."""


@dataclass(frozen=True)
class ServiceContext:
    """Internal channel discovery, never exposed as a tool parameter."""


@dataclass(frozen=True)
class CustomerContext:
    identity: ResolvedCustomerIdentity
    token: SecretStr | None = field(default=None, repr=False)

    def customer_id(self) -> str:
        if not self.identity.is_authenticated or not self.identity.saleor_user_id:
            raise SaleorPermissionError("Authenticated customer identity required")
        return self.identity.saleor_user_id


@dataclass(frozen=True)
class AdminContext:
    """The adapter must authenticate and authorize this principal before construction.

    This context is not proof of scopes; M09/M12 enforce operation-specific scopes.
    """

    principal_id: str


AuthContext: TypeAlias = PublicContext | ServiceContext | CustomerContext | AdminContext


def require_admin(context: AdminContext) -> None:
    if not isinstance(context, AdminContext) or not context.principal_id.strip():
        raise SaleorPermissionError("Authorized admin context required")


class SaleorAuthProvider:
    def __init__(self, service_token: SecretStr) -> None:
        self._service_token = service_token

    def headers(self, context: AuthContext) -> dict[str, str]:
        if isinstance(context, PublicContext):
            # Override injected HTTP clients' default Authorization, too.
            return {"Authorization": ""}
        if isinstance(context, CustomerContext):
            context.customer_id()
            secret = context.token if context.token is not None else self._service_token
        elif isinstance(context, AdminContext):
            require_admin(context)
            secret = self._service_token
        elif isinstance(context, ServiceContext):
            secret = self._service_token
        else:
            raise SaleorPermissionError("Unsupported authentication context")
        token = secret.get_secret_value()
        if not token or any(c.isspace() for c in token):
            raise SaleorValidationError("Invalid Saleor credential configuration")
        return {"Authorization": f"Bearer {token}"}
