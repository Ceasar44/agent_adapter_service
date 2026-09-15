from tests.agui.conftest import collect, run_input


async def test_customer_tool_uses_real_service_and_graphql(flow):
    results = []

    async def process(session, agent, *, run_id):
        sdk = await flow.context(session, agent)
        results.append(await flow.tools["search_products"](sdk, query="jacket"))
        results.append(await flow.tools["get_product_variants"](sdk))
        flow.agui.events.add(session, data={"message": results[0].data["items"][0]["name"]})

    flow.agui.processing.process.side_effect = process
    output = await collect(
        flow.agui.runner,
        run_input(
            state={
                "version": 1,
                "store": {"channel": "us"},
                "product": {"id": "product-1"},
            }
        ),
        flow.agui.trusted,
    )
    assert output[-1]["type"] == "RUN_FINISHED"
    assert results[1].data["items"][0]["id"] == "variant-1"
    search = next(body for body, _ in flow.calls if body["operationName"] == "ProductList")
    assert search["variables"]["channel"] == "us"
    assert search["variables"]["where"]["isPublished"] is True
    assert all("traceparent" in headers for _, headers in flow.calls)
