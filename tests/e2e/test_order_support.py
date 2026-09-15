from dataclasses import replace

import pytest

from agent_adapter_service.customer_identity.models import StorefrontIdentity
from tests.agui.conftest import run_input
from tests.support.flows import storefront_run


@pytest.mark.parametrize("owner", ["user-1", "other-user"])
async def test_http_order_support_enforces_ownership(flow, owner):
    flow.responses["OrderOwnershipById"]["order"]["user"] = {"id": owner}
    trusted = replace(
        flow.agui.trusted,
        identity=StorefrontIdentity(
            visitor_id="v1",
            status="authenticated",
            saleor_user_id="user-1",
        ),
    )
    results = []

    async def process(session, agent, *, run_id):
        result = await flow.tools["get_my_order"](
            await flow.context(session, agent),
            order_id="order-1",
        )
        results.append(result.data)
        flow.agui.events.add(session, data={"message": "Order request processed."})

    flow.agui.processing.process.side_effect = process
    events = await storefront_run(flow.agui, run_input(), trusted=trusted)
    assert events[-1]["type"] == "RUN_FINISHED"
    if owner == "user-1":
        assert results[0]["id"] == "order-1" and "user" not in results[0]
    else:
        assert results == [{"error": "not_authorized"}]
        assert not any(body["operationName"] == "OrderById" for body, _ in flow.calls)
