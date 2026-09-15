import asyncio
import json
import sys

from agent_adapter_service.agent.tools.common import ToolDependencies
from agent_adapter_service.agent.tools.context import ActiveToolContexts
from agent_adapter_service.agent.tools.registry import register_all
from agent_adapter_service.frontend.state.manager import FrontendStateManager
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.security.customer_policy import CustomerToolPolicy
from agent_adapter_service.security.frontend_tool_policy import FrontendToolAuthorizationPolicy


def catalog_registry():
    """Construct genuine SDK schemas with no credentials or active customer contexts."""
    scope = StoreScope(tenant_id="catalog", store_id="catalog")
    return register_all(
        ToolDependencies(
            scope,
            ActiveToolContexts(scope),
            FrontendStateManager(scope),
            CustomerToolPolicy(scope),
            FrontendToolAuthorizationPolicy(scope),
        )
    )


def tool_metadata(registry):
    return {
        name: {
            "description": entry.reference.tool.description,
            "parameters": {
                parameter: {**descriptor, "description": options.description or ""}
                for parameter, (descriptor, options) in entry.reference.tool.parameters.items()
            },
            "required": entry.reference.tool.required,
            "confirmation_enforced": entry.confirmation_enforced,
        }
        for name, entry in registry.available_tools.items()
    }


def run(main):
    try:
        result = asyncio.run(main())
        if result is not None:
            print(json.dumps(result, ensure_ascii=False))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception:
        # Configuration, subprocess and network errors can contain secrets.
        print(
            "Command failed; check configuration, dependencies and connectivity.", file=sys.stderr
        )
        raise SystemExit(1) from None
