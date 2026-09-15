from decimal import Decimal

import pytest

from agent_adapter_service.saleor.errors import SaleorPermissionError, SaleorValidationError
from agent_adapter_service.saleor.saleor_client.input_types import (
    CustomerInput,
    OrderFulfillInput,
    OrderFulfillLineInput,
    OrderFulfillStockInput,
    ProductCreateInput,
    ProductInput,
    ProductVariantCreateInput,
    ProductVariantInput,
    PromotionCreateInput,
    PromotionRuleCreateInput,
    PromotionRuleUpdateInput,
    PromotionUpdateInput,
    StockInput,
    UserCreateInput,
)
from agent_adapter_service.saleor.services.customer_service import CustomerService
from agent_adapter_service.saleor.services.inventory_service import InventoryService
from agent_adapter_service.saleor.services.order_service import OrderService
from agent_adapter_service.saleor.services.product_service import ProductService
from agent_adapter_service.saleor.services.promotion_service import PromotionService


async def test_product_and_variant_admin_writes(harness, admin, channel):
    client, responses, calls = harness
    product = {"id": "p", "name": "Jacket", "slug": "jacket"}
    variant = {
        "id": "v",
        "name": "Black",
        "sku": "BLACK",
        "product": {"id": "p"},
        "trackInventory": True,
    }
    responses.update(
        {
            "ProductCreate": {"productCreate": {"product": product, "errors": []}},
            "ProductUpdate": {"productUpdate": {"product": product, "errors": []}},
            "VariantCreate": {"productVariantCreate": {"productVariant": variant, "errors": []}},
            "VariantUpdate": {"productVariantUpdate": {"productVariant": variant, "errors": []}},
            "ChannelById": {"channel": channel},
            "ProductChannelListingUpdate": {
                "productChannelListingUpdate": {
                    "product": {"id": "p", "channelListings": []},
                    "errors": [],
                }
            },
            "VariantChannelListingUpdate": {
                "productVariantChannelListingUpdate": {
                    "variant": {
                        "id": "v",
                        "sku": "BLACK",
                        "channelListings": [
                            {"channel": channel, "price": {"amount": 99.25, "currency": "USD"}}
                        ],
                    },
                    "errors": [],
                }
            },
        }
    )
    service = ProductService(client)
    assert (
        await service.create_product(admin, ProductCreateInput(name="Jacket", productType="type"))
    ).id == "p"
    await service.update_product(admin, "p", ProductInput(description=None))
    assert calls[-1][0]["variables"]["input"] == {"description": None}
    await service.create_variant(admin, ProductVariantCreateInput(product="p", attributes=[]))
    await service.update_variant(admin, "v", ProductVariantInput(trackInventory=False))
    await service.publish_product(admin, "p", "channel-1", published=True)
    assert calls[-1][0]["variables"]["input"] == {
        "updateChannels": [{"channelId": "channel-1", "isPublished": True}]
    }
    result = await service.update_variant_price(admin, "v", "channel-1", Decimal("99.25"), "USD")
    assert result.channel_listings[0].price.amount == Decimal("99.25")
    assert calls[-1][0]["variables"]["input"][0]["price"] == "99.25"
    before = len(calls)
    with pytest.raises(SaleorValidationError, match="Currency"):
        await service.update_variant_price(admin, "v", "channel-1", Decimal("5"), "EUR")
    assert [c[0]["operationName"] for c in calls[before:]] == ["ChannelById"]


async def test_admin_context_and_field_whitelist(harness, customer):
    client, responses, calls = harness
    with pytest.raises(SaleorPermissionError):
        await ProductService(client).update_product(customer, "p", ProductInput(name="x"))
    from agent_adapter_service.saleor.auth import AdminContext

    with pytest.raises(SaleorValidationError):
        await CustomerService(client).update_customer(
            AdminContext("admin"), "u", CustomerInput(isConfirmed=True)
        )
    assert not calls


async def test_customer_mutations(harness, admin, customer_data):
    client, responses, calls = harness
    responses.update(
        {
            "CustomerCreate": {"customerCreate": {"user": customer_data, "errors": []}},
            "CustomerUpdate": {"customerUpdate": {"user": customer_data, "errors": []}},
        }
    )
    service = CustomerService(client)
    assert (
        await service.create_customer(admin, UserCreateInput(email="a@example.test"))
    ).id == "user-1"
    await service.update_customer(admin, "user-1", CustomerInput(firstName="A"))
    assert calls[-1][0]["variables"]["input"] == {"firstName": "A"}


async def test_inventory_absolute_quantity_and_validation(harness, admin):
    client, responses, calls = harness
    for operation in ("Create", "Update"):
        responses[f"VariantStocks{operation}"] = {
            f"productVariantStocks{operation}": {
                "productVariant": {"id": "v", "sku": "BLACK"},
                "errors": [],
            }
        }
    service = InventoryService(client)
    assert (
        await service.create_stock(admin, "v", [StockInput(warehouse="w", quantity=4)])
    ).id == "v"
    await service.update_stock(admin, "v", [StockInput(warehouse="w", quantity=0)])
    assert calls[-1][0]["variables"]["stocks"] == [{"warehouse": "w", "quantity": 0}]
    for stocks in (
        [],
        [StockInput(warehouse="w", quantity=-1)],
        [StockInput(warehouse="w", quantity=1)] * 2,
    ):
        with pytest.raises(SaleorValidationError):
            await service.update_stock(admin, "v", stocks)
    assert len(calls) == 2


async def test_order_cancel_fulfill_and_remaining_quantity(harness, admin, order):
    client, responses, calls = harness
    responses.update(
        {
            "OrderById": {"order": order},
            "OrderCancel": {
                "orderCancel": {
                    "order": {"id": "order-1", "number": "100", "status": "CANCELED"},
                    "errors": [],
                }
            },
            "OrderFulfill": {
                "orderFulfill": {
                    "order": {"id": "order-1", "number": "100", "status": "FULFILLED"},
                    "fulfillments": [],
                    "errors": [],
                }
            },
        }
    )
    service = OrderService(client)
    assert (await service.cancel_order(admin, "order-1")).status == "CANCELED"
    input = OrderFulfillInput(
        lines=[
            OrderFulfillLineInput(
                orderLineId="line-1", stocks=[OrderFulfillStockInput(warehouse="w", quantity=2)]
            )
        ],
        notifyCustomer=False,
    )
    assert (await service.fulfill_order(admin, "order-1", input)).order.status == "FULFILLED"
    assert calls[-1][0]["variables"]["input"]["notifyCustomer"] is False
    input.lines[0].stocks[0].quantity = 3
    with pytest.raises(SaleorValidationError, match="remaining"):
        await service.fulfill_order(admin, "order-1", input)
    assert calls[-1][0]["operationName"] == "OrderById"
    order["status"] = "CANCELED"
    with pytest.raises(SaleorValidationError, match="state"):
        await service.cancel_order(admin, "order-1")


async def test_promotion_and_rule_writes(harness, admin):
    client, responses, calls = harness
    promotion = {
        "id": "p",
        "name": "Sale",
        "type": "CATALOGUE",
        "startDate": "2026-09-16T00:00:00Z",
        "endDate": None,
    }
    rule = {
        "id": "r",
        "name": None,
        "cataloguePredicate": {},
        "orderPredicate": None,
        "rewardValue": "10",
        "rewardValueType": "PERCENTAGE",
        "rewardType": None,
        "channels": [],
    }
    responses.update(
        {
            "PromotionCreate": {"promotionCreate": {"promotion": promotion, "errors": []}},
            "PromotionUpdate": {"promotionUpdate": {"promotion": promotion, "errors": []}},
            "PromotionRuleCreate": {"promotionRuleCreate": {"promotionRule": rule, "errors": []}},
            "PromotionRuleUpdate": {"promotionRuleUpdate": {"promotionRule": rule, "errors": []}},
        }
    )
    service = PromotionService(client)
    assert (
        await service.create_promotion(admin, PromotionCreateInput(name="Sale", type="CATALOGUE"))
    ).id == "p"
    await service.update_promotion(admin, "p", PromotionUpdateInput(endDate=None))
    assert calls[-1][0]["variables"]["input"] == {"endDate": None}
    assert (
        await service.create_rule(admin, PromotionRuleCreateInput(promotion="p", rewardValue="10"))
    ).reward_value == Decimal("10")
    await service.update_rule(admin, "r", PromotionRuleUpdateInput(rewardValue=Decimal("5")))
    with pytest.raises(SaleorValidationError, match="after"):
        await service.update_promotion(
            admin,
            "p",
            PromotionUpdateInput(startDate="2026-09-17T00:00:00Z", endDate="2026-09-16T00:00:00Z"),
        )
    with pytest.raises(SaleorValidationError, match="timezone"):
        await service.update_promotion(admin, "p", PromotionUpdateInput(startDate="2026-09-16"))
    with pytest.raises(SaleorValidationError, match="100"):
        await service.update_rule(
            admin, "r", PromotionRuleUpdateInput(rewardValue="101", rewardValueType="PERCENTAGE")
        )
    with pytest.raises(SaleorValidationError):
        await service.update_rule(
            admin, "r", PromotionRuleUpdateInput(addChannels=["c"], removeChannels=["c"])
        )
    assert len(calls) == 4
