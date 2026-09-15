from dataclasses import replace

from agent_adapter_service.agent.tools.context import FrontendApproval
from agent_adapter_service.core.types import RiskLevel
from agent_adapter_service.frontend.tools.models import FrontendToolCall
from tests.agui.conftest import run_input
from tests.support.flows import storefront_run


async def test_anonymous_search_select_and_confirmed_cart(flow):
    call = FrontendToolCall(
        call_id="approved-cart",
        thread_id="t1",
        name="add_to_cart",
        risk=RiskLevel.MEDIUM,
        arguments={"product_id": "product-1", "variant_id": "variant-1", "quantity": 1},
    )
    consumed = []

    class Approvals:
        async def consume(self, identifier, context, actual):
            if (
                identifier != "approval-1"
                or actual != call
                or consumed
                or context.store_scope != flow.agui.trusted.scope
                or context.thread_id != "t1"
                or context.customer_identity.visitor_id != "v1"
            ):
                return False
            consumed.append(identifier)
            return True

    flow.deps.frontend_policy.confirmations = Approvals()
    trusted = replace(flow.agui.trusted, approvals=(FrontendApproval(call, "approval-1"),))

    async def process(session, agent, *, run_id):
        sdk = await flow.context(session, agent)
        products = await flow.tools["search_products"](sdk, query="jacket")
        assert products.data["items"][0]["id"] == "product-1"
        variants = await flow.tools["get_product_variants"](sdk, product_id="product-1")
        assert variants.data["items"][0]["id"] == "variant-1"
        selected = await flow.tools["select_variant"](
            sdk,
            product_id="product-1",
            variant_id="variant-1",
        )
        assert selected.data["result"]["status"] == "success"
        cart = await flow.tools["add_to_cart"](
            sdk,
            product_id="product-1",
            variant_id="variant-1",
        )
        assert cart.data["frontend_state"]["cart"]["item_count"] == 1
        assert cart.data["authoritative"] is False
        replay = await flow.tools["add_to_cart"](
            sdk,
            product_id="product-1",
            variant_id="variant-1",
        )
        assert replay.data == {"error": "confirmation_required"}
        flow.agui.events.add(session, data={"message": "Your cart has been updated."})

    def browser(name, call_id):
        version = 2 if name == "select_variant" else 3
        changes = (
            {"product": {"id": "product-1", "variant_id": "variant-1"}}
            if version == 2
            else {"cart": {"item_count": 1}}
        )
        return {
            "result": {"call_id": call_id, "status": "success", "resulting_state_version": version},
            "state": {"base_version": version - 1, "version": version, "changes": changes},
        }

    flow.agui.processing.process.side_effect = process
    events = await storefront_run(
        flow.agui,
        run_input(
            state={
                "version": 1,
                "store": {"channel": "us"},
            }
        ),
        trusted=trusted,
        browser=browser,
    )
    assert events[-1]["type"] == "RUN_FINISHED"
    assert [e["toolCallName"] for e in events if e["type"] == "TOOL_CALL_START"] == [
        "select_variant",
        "add_to_cart",
    ]
    assert consumed == ["approval-1"]
    assert len(flow.agui.harness.sessions.records) == 1
