import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from agent_adapter_service.saleor.auth import CustomerContext, PublicContext, ServiceContext
from agent_adapter_service.saleor.client import SaleorGraphQLClient, saleor_trace
from agent_adapter_service.saleor.errors import (
    SaleorError,
    SaleorPermissionError,
    SaleorValidationError,
)
from agent_adapter_service.saleor.saleor_client.input_types import PromotionUpdateInput, StockInput


async def test_concurrent_auth_and_public_override(customer, channel):
    seen = []

    async def handler(request):
        await asyncio.sleep(0)
        seen.append((request.headers["authorization"], request.headers["x-request-id"]))
        return httpx.Response(200, json={"data": {"channel": channel}})

    async with httpx.AsyncClient(
        headers={"Authorization": "Bearer injected-default"},
        transport=httpx.MockTransport(handler),
    ) as http:
        client = SaleorGraphQLClient("https://saleor.test", SecretStr("service"), http_client=http)
        contexts = [
            PublicContext(),
            ServiceContext(),
            CustomerContext(customer.identity, SecretStr("user")),
        ]
        await asyncio.gather(
            *[
                client.call(client.api.channel_by_id, id="channel-1", context=c, trace_id=str(i))
                for i, c in enumerate(contexts)
            ]
        )
        assert set(seen) == {("", "0"), ("Bearer service", "1"), ("Bearer user", "2")}
        await client.close()
        assert not http.is_closed
        with pytest.raises(SaleorError, match="closed"):
            await client.call(client.api.channel_list)


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (401, {}, SaleorPermissionError),
        (500, {"secret": "private-token"}, SaleorError),
        (
            200,
            {"errors": [{"message": "private-token", "extensions": {"code": "PERMISSION_DENIED"}}]},
            SaleorPermissionError,
        ),
        (200, {"data": {"channels": []}, "errors": [{"message": "private-token"}]}, SaleorError),
        (200, {"data": None}, SaleorError),
        (200, {"data": {"channels": [{"wrong": "private-token"}]}}, SaleorError),
    ],
)
async def test_safe_transport_errors(status, body, expected):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))
    ) as http:
        client = SaleorGraphQLClient("https://saleor.test", SecretStr("secret"), http_client=http)
        with pytest.raises(expected) as exc:
            await client.call(client.api.channel_list, context=ServiceContext())
        assert "private-token" not in str(exc.value)
        assert "private-token" not in json.dumps(exc.value.details)


async def test_mutation_bulk_errors_and_no_retry(admin):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "productVariantStocksUpdate": {
                        "productVariant": None,
                        "errors": [
                            {
                                "field": "stocks",
                                "code": "INVALID",
                                "message": "private-token",
                                "index": 1,
                            }
                        ],
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SaleorGraphQLClient("https://saleor.test", SecretStr("secret"), http_client=http)
        with pytest.raises(SaleorValidationError) as exc:
            await client.call(
                client.api.variant_stocks_update,
                context=admin,
                variantId="v",
                stocks=[StockInput(warehouse="w", quantity=2)],
            )
        assert exc.value.details == {"errors": [{"field": "stocks", "code": "INVALID", "index": 1}]}
        assert len(requests) == 1


async def test_timeout_no_retry_and_owned_close():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private-token")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SaleorGraphQLClient("https://saleor.test", SecretStr("secret"), http_client=http)
        with pytest.raises(SaleorError) as exc:
            await client.call(client.api.channel_list)
        assert exc.value.code == "saleor_timeout"
        assert len(calls) == 1
    owned = SaleorGraphQLClient("https://saleor.test", SecretStr("secret"))
    await owned.close()
    await owned.close()
    assert owned._http.is_closed


async def test_update_unset_versus_explicit_null(harness, admin):
    client, responses, calls = harness
    responses["PromotionUpdate"] = {"promotionUpdate": {"promotion": None, "errors": []}}
    for input in (PromotionUpdateInput(name="New"), PromotionUpdateInput(endDate=None)):
        await client.call(client.api.promotion_update, context=admin, id="p", input=input)
    assert calls[0][0]["variables"]["input"] == {"name": "New"}
    assert calls[1][0]["variables"]["input"] == {"endDate": None}


async def test_cancellation_propagates():
    async def handler(request):
        raise asyncio.CancelledError

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SaleorGraphQLClient("https://saleor.test", SecretStr("secret"), http_client=http)
        with pytest.raises(asyncio.CancelledError):
            await client.call(client.api.channel_list)


async def test_trace_binding_is_task_local(harness, channel):
    client, responses, calls = harness
    responses["ChannelById"] = {"channel": channel}

    async def run(value):
        with saleor_trace(value):
            await asyncio.sleep(0)
            await client.call(client.api.channel_by_id, id=value)

    await asyncio.gather(run("run-a"), run("run-b"))
    for body, headers in calls:
        assert headers["x-request-id"] == body["variables"]["id"]
    await client.call(client.api.channel_by_id, id="outside")
    assert calls[-1][1]["x-request-id"] not in {"run-a", "run-b"}
    assert calls[-1][1]["x-request-id"] == calls[-1][1]["traceparent"].split("-")[1]
