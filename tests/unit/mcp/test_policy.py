from pathlib import Path

import pytest

from agent_adapter_service.core.exceptions import AuthorizationError
from agent_adapter_service.mcp.server.config import AdminServerConfig
from agent_adapter_service.security.admin_mcp_policy import AdminMcpPolicy


def test_every_enabled_admin_tool_requires_its_declared_scope():
    config = AdminServerConfig.load(Path("configs/mcp/admin_server.yaml"))
    policy = AdminMcpPolicy(config.store_scope)
    principal = config.clients[0].principal
    for name, scope in config.required_scopes.items():
        allowed = principal.model_copy(update={"scopes": frozenset({scope})})
        assert policy.require_tool_access(allowed, name).principal_id == principal.client_id
        denied = principal.model_copy(update={"scopes": frozenset()})
        with pytest.raises(AuthorizationError):
            policy.require_tool_access(denied, name)
