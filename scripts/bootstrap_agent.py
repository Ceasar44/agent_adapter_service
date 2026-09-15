"""Bootstrap the configured customer agent using the application's resource lifecycle."""

import argparse

from agent_adapter_service.agent.config.loader import AgentConfigLoader
from agent_adapter_service.agent.bootstrap import validate_registry
from agent_adapter_service.app.factory import create_app
from agent_adapter_service.core.settings import Settings
from agent_adapter_service.persistence.contracts import StoreScope

try:
    from ._common import catalog_registry, run
except ImportError:
    from _common import catalog_registry, run


async def bootstrap(settings, scope, *, app_factory=create_app):
    if not settings.parlant_enabled or not settings.database_enabled:
        raise ValueError("Bootstrap requires Parlant and database enabled")
    app = app_factory(settings=settings, agui_scope=scope)
    async with app.router.lifespan_context(app):
        # AgentRuntime.start invokes AgentBootstrapper.bootstrap exactly once.
        runtime = app.state.resources.parlant_runtime
        agent = runtime.get_agent()
        config = await runtime.loader.aload()
        return {
            "status": "synchronized",
            "agent_id": agent.id,
            "tools": sorted(t.name for t in config.tools if t.enabled),
            "guidelines": len(config.guidelines),
            "journeys": len(config.journeys),
            "glossary": len(config.glossary),
        }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--store-id", required=True)
    parser.add_argument(
        "--check", action="store_true", help="Validate local config without starting Parlant"
    )
    args = parser.parse_args()
    settings = Settings()
    if args.check:
        config = await AgentConfigLoader(settings.parlant_config_dir).aload()
        validate_registry(config, catalog_registry())
        if config.agent.id != settings.default_agent_id:
            raise ValueError("Agent ID does not match settings")
        return {"status": "valid", "agent_id": config.agent.id}
    return await bootstrap(settings, StoreScope(tenant_id=args.tenant_id, store_id=args.store_id))


if __name__ == "__main__":
    run(main)
