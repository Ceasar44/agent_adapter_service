"""Validate references and customer-facing configuration before any SDK side effects."""

from collections.abc import Collection

from agent_adapter_service.agent.config.loader import ConfigError
from agent_adapter_service.agent.config.models import CustomerServiceConfig

FORBIDDEN_TOOLS = {
    "submit_payment",
    "place_order",
    "delete_account",
    "create_product",
    "update_product",
    "publish_product",
    "update_variant",
    "update_variant_price",
    "update_price",
    "update_stock",
    "update_inventory",
    "cancel_order",
    "fulfill_order",
    "update_customer",
    "create_collection",
    "update_collection",
    "add_products_to_collection",
    "create_promotion",
    "update_promotion",
    "disable_promotion",
}


class AgentConfigValidator:
    def validate(self, config: CustomerServiceConfig, available_tools: Collection[str]) -> None:
        def fail(file: str, identifier: str, message: str) -> None:
            raise ConfigError(f"{file}.yaml [{identifier}]: {message}")

        def unique(file: str, ids: Collection[str]) -> None:
            seen = set()
            for identifier in ids:
                if identifier in seen:
                    fail(file, identifier, "duplicate ID")
                seen.add(identifier)

        for section in ("guidelines", "journeys", "glossary"):
            unique(section, [item.id for item in getattr(config, section)])
        unique("tools", [tool.name for tool in config.tools])
        enabled = {tool.name for tool in config.tools if tool.enabled}
        for tool in config.tools:
            if not tool.enabled:
                continue
            if tool.name in FORBIDDEN_TOOLS or tool.confirmation == "deny":
                fail("tools", tool.name, "tool is forbidden for the customer agent")
            if tool.name not in available_tools:
                fail("tools", tool.name, "tool is not registered")
            if tool.risk != "low" and tool.confirmation != "confirm":
                fail("tools", tool.name, "non-low risk tools require confirmation")

        def references(file: str, identifier: str, names: Collection[str]) -> None:
            for name in names:
                if name not in enabled:
                    fail(file, identifier, f"unknown or disabled tool '{name}'")

        for guideline in config.guidelines:
            references("guidelines", guideline.id, guideline.tools)
        for journey in config.journeys:
            unique("journeys", [state.id for state in journey.states])
            states = {state.id for state in journey.states}
            if states & {"START", "END"}:
                fail("journeys", journey.id, "START and END are reserved state IDs")
            edges: dict[str, set[str]] = {}
            seen_edges = set()
            for state in journey.states:
                references("journeys", f"{journey.id}/{state.id}", state.tools)
            for edge in journey.transitions:
                if edge.source not in states | {"START"} or edge.target not in states | {"END"}:
                    fail("journeys", journey.id, "invalid transition state")
                key = (edge.source, edge.target, edge.condition)
                if key in seen_edges:
                    fail("journeys", journey.id, "duplicate transition")
                seen_edges.add(key)
                edges.setdefault(edge.source, set()).add(edge.target)
            reached = {"START"}
            while expanded := set().union(*(edges.get(s, set()) for s in reached)) - reached:
                reached.update(expanded)
            if not states | {"END"} <= reached:
                fail("journeys", journey.id, "all states and END must be reachable from START")
            exiting = {"END"}
            while expanded := {s for s, targets in edges.items() if targets & exiting} - exiting:
                exiting.update(expanded)
            if not states <= exiting:
                fail("journeys", journey.id, "every state must have a path to END")
