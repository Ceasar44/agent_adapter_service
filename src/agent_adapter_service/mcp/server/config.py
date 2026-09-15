from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.security.admin_mcp_policy import AdminMcpPolicy, AdminMcpPrincipal


class AdminClient(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token_env: str = Field(pattern=r"^ADMIN_MCP_[A-Z0-9_]+$")
    principal: AdminMcpPrincipal


class AdminServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    auth_mode: Literal["opaque_token"] = "opaque_token"
    path: str = Field(default="/mcp", pattern=r"^/[a-zA-Z0-9_-]+$")
    store_scope: StoreScope
    enabled_tools: list[str]
    required_scopes: dict[str, str]
    clients: list[AdminClient] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        if len(set(self.enabled_tools)) != len(self.enabled_tools) or not self.enabled_tools:
            raise ValueError("Enabled tools must be nonempty and unique")
        if set(self.required_scopes) != set(self.enabled_tools):
            raise ValueError("Every enabled tool must declare its required scope")
        for tool, scope in self.required_scopes.items():
            if AdminMcpPolicy.tool_scopes.get(tool) != scope:
                raise ValueError("Unknown tool or incorrect required scope")
        known = set(AdminMcpPolicy.tool_scopes.values())
        ids = [client.principal.client_id for client in self.clients]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate admin client")
        for client in self.clients:
            if client.principal.store_scope != self.store_scope or client.principal.scopes - known:
                raise ValueError("Invalid admin store scope or permission scope")
        return self

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
