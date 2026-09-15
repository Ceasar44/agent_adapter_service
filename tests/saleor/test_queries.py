from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import SecretStr

from agent_adapter_service.customer_identity.models import IdentityStatus
from agent_adapter_service.saleor.auth import CustomerContext
from agent_adapter_service.saleor.errors import (
    SaleorNotFoundError,
    SaleorPermissionError,
    SaleorValidationError,
)
from agent_adapter_service.saleor.saleor_client.input_types import ProductWhereInput
from agent_adapter_service.saleor.services.channel_service import ChannelService
from agent_adapter_service.saleor.services.customer_service import CustomerService
from agent_adapter_service.saleor.services.order_service import OrderService
from agent_adapter_service.saleor.services.product_service import ProductService


async def test_channel_and_product_queries(harness, admin, channel, product, page_info):
    client, responses, calls = harness
    responses.update(
        {
            "ChannelList": {"channels": [channel]},
            "ChannelById": {"channel": channel},
            "ChannelBySlug": {"channel": channel},
            "ProductById": {"product": product},
            "ProductBySlug": {"product": product},
            "ProductList": {
                "products": {"edges": [{"cursor": "one", "node": product}], "pageInfo": page_info}
            },
        }
    )
    channels = ChannelService(client)
    assert (await channels.list_channels(admin))[0].currency_code == "USD"
    assert (await channels.get_channel(id="channel-1")).slug == "us"
    service = ProductService(client, channels)
    assert (await service.get_product("us", id="product-1")).name == "Jacket"
    assert (await service.get_product("us", slug="jacket")).description is None
    found = await service.search_products(
        "jacket", "us", filters=ProductWhereInput(isPublished=False)
    )
    assert found.items[0].pricing is None
    body, headers = calls[-1]
    assert headers["authorization"] == ""
    assert body["variables"]["where"]["isPublished"] is True
    assert body["variables"]["where"]["AND"] == [{"isPublished": False}]
    assert not found.page_info.has_next_page
    before = len(calls)
    with pytest.raises(SaleorValidationError):
        await service.search_products(None, "us", first=101)
    assert len(calls) == before
    responses["ProductById"] = {"product": None}
    with pytest.raises(SaleorNotFoundError):
        await service.get_product("us", id="missing")
    channel["isActive"] = False
    with pytest.raises(SaleorValidationError, match="inactive"):
        await service.get_product("us", id="product-1")


async def test_variant_lookup_and_resolution(harness, channel, variant, page_info):
    client, responses, calls = harness
    responses.update(
        {
            "ChannelBySlug": {"channel": channel},
            "VariantById": {"productVariant": variant},
            "VariantBySku": {"productVariant": variant},
            "VariantList": {
                "product": {
                    "id": "product-1",
                    "productVariants": {
                        "edges": [{"cursor": "one", "node": variant}],
                        "pageInfo": page_info,
                    },
                }
            },
        }
    )
    service = ProductService(client)
    result = await service.get_variant("us", id="variant-1", product_id="product-1")
    assert result.quantity_available is None  # Missing availability is not zero.
    assert (await service.get_variant("us", sku="BLACK")).sku == "BLACK"
    assert (await service.resolve_variant("product-1", "us", {"color": "black"})).id == "variant-1"
    with pytest.raises(SaleorValidationError, match="belong"):
        await service.get_variant("us", id="variant-1", product_id="other")
    with pytest.raises(SaleorNotFoundError):
        await service.resolve_variant("product-1", "us", {"color": "blue"})
    second = deepcopy(variant)
    second["id"] = "variant-2"
    responses["VariantList"]["product"]["productVariants"]["edges"].append(
        {"cursor": "two", "node": second}
    )
    with pytest.raises(SaleorValidationError, match="ambiguous"):
        await service.resolve_variant("product-1", "us", {"color": "black"})


async def test_variant_resolution_requires_complete_pagination(
    harness, channel, variant, page_info
):
    client, responses, calls = harness
    responses["ChannelBySlug"] = {"channel": channel}
    responses["VariantList"] = {
        "product": {
            "id": "product-1",
            "productVariants": {
                "edges": [{"cursor": "one", "node": variant}],
                "pageInfo": {**page_info, "hasNextPage": True},
            },
        }
    }
    with pytest.raises(SaleorValidationError, match="pagination limit"):
        await ProductService(client).resolve_variant(
            "product-1", "us", {"color": "black"}, max_pages=1
        )
    with pytest.raises(SaleorValidationError, match="pagination"):
        await ProductService(client).resolve_variant("product-1", "us", {"color": "black"})


async def test_customer_trusted_identity_and_me(harness, customer, admin, customer_data):
    client, responses, calls = harness
    responses.update({"CustomerById": {"user": customer_data}, "Me": {"me": customer_data}})
    service = CustomerService(client)
    assert (await service.resolve_saleor_customer(customer)).id == "user-1"
    assert calls[-1][0]["variables"]["id"] == "user-1"
    assert (await service.get_admin_customer(admin, "user-1")).email == "a@example.test"
    token_context = CustomerContext(customer.identity, SecretStr("customer-token"))
    assert (await service.get_customer(token_context)).id == "user-1"
    assert calls[-1][0]["operationName"] == "Me"
    assert calls[-1][1]["authorization"] == "Bearer customer-token"
    customer_data["id"] = "someone-else"
    with pytest.raises(SaleorPermissionError):
        await service.get_customer(token_context)


@pytest.mark.parametrize("status", [IdentityStatus.ANONYMOUS, IdentityStatus.AUTH_UNAVAILABLE])
async def test_non_authenticated_cannot_reuse_retained_user(harness, customer, status):
    client, responses, calls = harness
    context = CustomerContext(customer.identity.model_copy(update={"status": status}))
    with pytest.raises(SaleorPermissionError):
        await OrderService(client).list_customer_orders(context)
    with pytest.raises(SaleorPermissionError):
        await CustomerService(client).get_customer(context)
    assert calls == []


async def test_customer_orders_and_decimal(harness, customer, order, page_info):
    client, responses, calls = harness
    responses.update(
        {
            "OrdersByCustomer": {
                "user": {
                    "id": "user-1",
                    "orders": {"edges": [{"cursor": "one", "node": order}], "pageInfo": page_info},
                }
            },
            "OrderOwnershipById": {"order": {"id": "order-1", "user": {"id": "user-1"}}},
            "OrderById": {"order": order},
        }
    )
    service = OrderService(client)
    assert (await service.list_customer_orders(customer)).items[0].total.gross.amount == Decimal(
        "12.34"
    )
    assert (await service.get_customer_order(customer, "order-1")).lines[0].quantity_to_fulfill == 2
    assert [c[0]["operationName"] for c in calls[-2:]] == ["OrderOwnershipById", "OrderById"]
    order["user"] = {"id": "changed-owner"}
    with pytest.raises(SaleorPermissionError):
        await service.get_customer_order(customer, "order-1")


@pytest.mark.parametrize("owner", [None, {"id": "someone-else"}])
async def test_ownership_denial_never_fetches_details(harness, customer, owner):
    client, responses, calls = harness
    responses["OrderOwnershipById"] = {"order": {"id": "order-1", "user": owner}}
    with pytest.raises(SaleorPermissionError):
        await OrderService(client).get_customer_order(customer, "order-1")
    assert [c[0]["operationName"] for c in calls] == ["OrderOwnershipById"]
