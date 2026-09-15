from tests.agui.conftest import run_input
from tests.support.flows import storefront_run


async def test_browser_receipt_updates_state_before_tool_continuation(flow):
    async def process(session, agent, *, run_id):
        sdk = await flow.context(session, agent)
        result = await flow.tools["select_variant"](
            sdk,
            product_id="product-1",
            variant_id="variant-1",
        )
        assert result.data["result"]["status"] == "success"
        assert (await flow.deps.states.get("t1")).product.variant_id == "variant-1"
        assert any(e.source == "customer_ui" for e in flow.agui.events.records[session])
        flow.agui.events.add(session, data={"message": "Selected the requested variant."})

    def browser(name, call_id):
        assert name == "select_variant"
        return {
            "result": {"call_id": call_id, "status": "success", "resulting_state_version": 1},
            "state": {"version": 1, "product": {"id": "product-1", "variant_id": "variant-1"}},
        }

    flow.agui.processing.process.side_effect = process
    events = await storefront_run(flow.agui, run_input(), browser=browser)
    kinds = [e["type"] for e in events]
    assert kinds[-1] == "RUN_FINISHED"
    assert kinds.index("TOOL_CALL_END") < kinds.index("TEXT_MESSAGE_CONTENT")
    assert "STATE_SNAPSHOT" in kinds
