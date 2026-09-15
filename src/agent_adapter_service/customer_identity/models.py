from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_adapter_service.core.types import AuthState

Identifier = Annotated[
    str, Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/+=-]*$")
]


class IdentityStatus(StrEnum):
    ANONYMOUS = "anonymous"
    AUTHENTICATED = "authenticated"
    AUTH_UNAVAILABLE = "unavailable"


class IdentityModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StorefrontIdentity(IdentityModel):
    """Construct only from verified BFF context, never directly from browser JSON.

    visitor_id must be integrity protected by the BFF. Validation is not authentication.
    """

    visitor_id: Identifier
    saleor_user_id: Identifier | None = None
    status: IdentityStatus
    display_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None

    @model_validator(mode="after")
    def validate_authentication(self) -> Self:
        if (self.status == IdentityStatus.AUTHENTICATED) != (self.saleor_user_id is not None):
            raise ValueError("Only authenticated identity must carry a Saleor user ID")
        return self


class CustomerIdentityBinding(IdentityModel):
    visitor_id: Identifier | None = None
    saleor_user_id: Identifier | None = None
    parlant_customer_id: Identifier
    # Retained server-side for retrying upgrades of this visitor's existing sessions.
    anonymous_customer_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if self.visitor_id is None and self.saleor_user_id is None:
            raise ValueError("A visitor or Saleor user ID is required")
        if "guest" in (self.parlant_customer_id, self.anonymous_customer_id):
            raise ValueError("Shared guest identity is forbidden")
        return self


class ResolvedCustomerIdentity(IdentityModel):
    visitor_id: Identifier
    saleor_user_id: Identifier | None = None
    parlant_customer_id: Identifier
    status: IdentityStatus

    @property
    def auth_state(self) -> AuthState:
        return AuthState(self.status.value)

    @property
    def is_authenticated(self) -> bool:
        return self.status == IdentityStatus.AUTHENTICATED
