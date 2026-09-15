"""Compose one trusted store's AG-UI dependencies at application startup."""

from agent_adapter_service.agent.runtime import AgentRuntime
from agent_adapter_service.observability.audit import AuditService
from agent_adapter_service.persistence.repositories.audit_log import SqlAuditLogRepository
from agent_adapter_service.agent.tools.common import ToolDependencies
from agent_adapter_service.agent.tools.context import ActiveToolContexts
from agent_adapter_service.agent.tools.registry import AgentToolRegistry, register_all
from agent_adapter_service.agui.input_adapter import AguiInputAdapter
from agent_adapter_service.agui.runner import AguiRunRunner
from agent_adapter_service.conversations.bindings import ConversationBindingManager
from agent_adapter_service.conversations.service import ConversationService
from agent_adapter_service.customer_identity.resolver import CustomerIdentityResolver
from agent_adapter_service.customer_identity.service import CustomerIdentityService
from agent_adapter_service.frontend.events.handler import FrontendEventHandler
from agent_adapter_service.frontend.state.manager import FrontendStateManager
from agent_adapter_service.frontend.state.reducer import FrontendStateReducer
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.persistence.repositories.conversations import SqlConversationRepository
from agent_adapter_service.persistence.repositories.customer_identity import (
    SqlCustomerIdentityRepository,
)
from agent_adapter_service.security.customer_policy import CustomerToolPolicy
from agent_adapter_service.security.frontend_tool_policy import FrontendToolAuthorizationPolicy


def prepare_agui_tools(resources, scope: StoreScope, confirmations=None) -> None:
    """Run before Parlant bootstrap so enabled customer tools are available to the SDK."""
    if not isinstance(resources.database, Database):
        raise ValueError("AG-UI requires a database")
    if resources.agent_tool_registry is not None:
        registry = resources.agent_tool_registry
        if not isinstance(registry, AgentToolRegistry) or registry.dependencies.scope != scope:
            raise ValueError("AG-UI requires a matching customer tool registry")
        if not isinstance(registry.dependencies.contexts, ActiveToolContexts):
            raise ValueError("AG-UI requires active run contexts")
        audit = AuditService(SqlAuditLogRepository(resources.database, scope))
        if registry.dependencies.audit is None:
            registry.dependencies.audit = audit
        if registry.dependencies.frontend_policy.audit is None:
            registry.dependencies.frontend_policy.audit = audit
        return
    states = FrontendStateManager(
        scope, FrontendStateReducer(max_bytes=resources.settings.frontend_state_max_bytes)
    )
    audit = AuditService(SqlAuditLogRepository(resources.database, scope))
    orders = resources.services.get("order")
    dependencies = ToolDependencies(
        scope=scope,
        contexts=ActiveToolContexts(scope),
        states=states,
        customer_policy=CustomerToolPolicy(scope, orders=orders),
        frontend_policy=FrontendToolAuthorizationPolicy(scope, confirmations=confirmations, audit=audit),
        audit=audit,
        products=resources.services.get("product"),
        orders=orders,
        rag=resources.rag_client,
    )
    resources.agent_tool_registry = register_all(dependencies)


def assemble_agui(resources, scope: StoreScope) -> AguiRunRunner:
    runtime = resources.parlant_runtime
    if not isinstance(runtime, AgentRuntime):
        raise ValueError("AG-UI requires Parlant runtime")
    host = runtime.host
    dependencies = resources.agent_tool_registry.dependencies
    identities = SqlCustomerIdentityRepository(resources.database, scope)
    bindings = SqlConversationRepository(resources.database, scope)
    identity_service = CustomerIdentityService(
        identities,
        host.customers,
        host.sessions,
        conversations=bindings.storage,
    )
    conversations = ConversationService(
        ConversationBindingManager(bindings, host.sessions, agent_id=runtime.get_agent().id),
        identities,
    )
    runner = AguiRunRunner(
        scope=scope,
        identities=CustomerIdentityResolver(identity_service),
        conversations=conversations,
        states=dependencies.states,
        frontend_events=FrontendEventHandler(host.frontend_events),
        messages=host.messages,
        events=host.events,
        processing=host.processing,
        contexts=dependencies.contexts,
        input_adapter=AguiInputAdapter(max_state_bytes=resources.settings.frontend_state_max_bytes),
        tool_timeout=resources.settings.request_timeout_seconds,
    )
    resources.services.update(
        identity=identity_service, conversation=conversations, agui_runner=runner
    )
    return runner
