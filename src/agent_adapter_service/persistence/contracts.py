"""ORM-free inputs, immutable records and repository interfaces for domain services."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    model_validator,
)

Identifier = Annotated[
    str, Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/+=-]*$")
]
identifier_adapter = TypeAdapter(Identifier)


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)


class StoreScope(Record):
    tenant_id: Identifier
    store_id: Identifier


class IdentityBindingCreate(Record):
    visitor_id: Identifier | None = None
    saleor_user_id: Identifier | None = None
    parlant_customer_id: Identifier

    @model_validator(mode="after")
    def require_identity(self) -> Self:
        if self.visitor_id is None and self.saleor_user_id is None:
            raise ValueError("A visitor_id or saleor_user_id is required")
        return self


class IdentityBinding(IdentityBindingCreate, StoreScope):
    id: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ConversationBindingCreate(Record):
    agui_thread_id: Identifier
    parlant_session_id: Identifier
    parlant_customer_id: Identifier
    parlant_agent_id: Identifier


class ConversationBinding(ConversationBindingCreate, StoreScope):
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AuditEvent(Record):
    actor: Identifier
    surface: Literal["customer_agent", "frontend_tool", "admin_mcp"]
    tool_name: Identifier
    arguments_summary: dict[str, JsonValue] = Field(default_factory=dict)
    result_status: Literal["success", "failure", "denied", "pending"]
    trace_id: Identifier | None = None
    timestamp: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditLog(AuditEvent, StoreScope):
    id: str


class AuditQuery(Record):
    actor: Identifier | None = None
    surface: Literal["customer_agent", "frontend_tool", "admin_mcp"] | None = None
    tool_name: Identifier | None = None
    result_status: Literal["success", "failure", "denied", "pending"] | None = None
    trace_id: Identifier | None = None
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    limit: int = Field(default=100, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def ordered_time_range(self) -> Self:
        if self.since and self.until and self.since > self.until:
            raise ValueError("since must not be after until")
        return self


class IdentityBindingRepository(Protocol):
    async def find_by_visitor_id(self, visitor_id: str) -> IdentityBinding | None: ...
    async def find_by_saleor_user_id(self, saleor_user_id: str) -> IdentityBinding | None: ...
    async def find_by_parlant_customer_id(
        self, parlant_customer_id: str
    ) -> IdentityBinding | None: ...
    async def create(self, data: IdentityBindingCreate) -> IdentityBinding: ...
    async def upsert(
        self, data: IdentityBindingCreate, *, expected_customer_id: str | None = None
    ) -> IdentityBinding: ...
    async def update(
        self, binding_id: str, *, expected_customer_id: str, parlant_customer_id: str
    ) -> IdentityBinding: ...
    async def bind_saleor_user(
        self,
        visitor_id: str,
        saleor_user_id: str,
        *,
        expected_customer_id: str,
        parlant_customer_id: str | None = None,
    ) -> IdentityBinding: ...


class ConversationBindingRepository(Protocol):
    async def find_by_thread(self, thread_id: str) -> ConversationBinding | None: ...
    async def find_by_session(self, session_id: str) -> ConversationBinding | None: ...
    async def create(self, data: ConversationBindingCreate) -> ConversationBinding: ...
    async def get_or_create(self, data: ConversationBindingCreate) -> ConversationBinding: ...
    async def update_customer(
        self, thread_id: str, *, expected_customer_id: str, parlant_customer_id: str
    ) -> ConversationBinding: ...


class AuditLogRepository(Protocol):
    async def append(self, event: AuditEvent) -> AuditLog: ...
    async def search(self, query: AuditQuery | None = None) -> list[AuditLog]: ...
