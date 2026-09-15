from datetime import UTC, datetime, timedelta

import pytest

from agent_adapter_service.saleor.errors import SaleorValidationError
from agent_adapter_service.saleor.saleor_client.input_types import (
    CollectionCreateInput,
    CollectionInput,
    StockFilterInput,
)
from agent_adapter_service.saleor.services.collection_service import CollectionService
from agent_adapter_service.saleor.services.inventory_service import InventoryService
from agent_adapter_service.saleor.services.promotion_service import PromotionService


async def test_collection_generated_operations_and_validation(harness, admin):
    client, responses, calls = harness
    collection = {"id": "c1", "name": "Jackets", "slug": "jackets"}
    for name in ("CollectionCreate", "CollectionUpdate", "CollectionAddProducts"):
        responses[name] = {name[0].lower() + name[1:]: {"collection": collection, "errors": []}}
    service = CollectionService(client)
    assert (
        await service.create_collection(admin, CollectionCreateInput(name="Jackets"))
    ).id == "c1"
    await service.update_collection(admin, "c1", CollectionInput(name="Coats"))
    await service.add_products_to_collection(admin, "c1", ["p1"])
    assert calls[-1][0]["variables"] == {"collectionId": "c1", "products": ["p1"]}
    assert calls[-1][1]["authorization"] == "Bearer service"
    before = len(calls)
    for products in ([], ["p1", "p1"], ["p"] * 101):
        with pytest.raises(SaleorValidationError):
            await service.add_products_to_collection(admin, "c1", products)
    with pytest.raises(SaleorValidationError):
        await service.update_collection(admin, "c1", CollectionInput(privateMetadata=[]))
    assert len(calls) == before


async def test_stock_read_pagination_and_auth(harness, admin, page_info):
    client, responses, calls = harness
    responses["StockList"] = {
        "stocks": {
            "edges": [
                {
                    "node": {
                        "id": "s1",
                        "warehouse": {"id": "w1"},
                        "productVariant": {"id": "v1"},
                        "quantity": 10,
                        "quantityAllocated": 3,
                    }
                }
            ],
            "pageInfo": page_info,
        }
    }
    result = await InventoryService(client).get_stock(
        admin, first=5, filter=StockFilterInput(search="Jacket")
    )
    assert result.items[0].quantity == 10
    assert result.items[0].product_variant.id == "v1"
    assert calls[-1][0]["variables"]["filter"] == {"search": "Jacket"}
    assert calls[-1][1]["authorization"] == "Bearer service"
    with pytest.raises(SaleorValidationError):
        await InventoryService(client).get_stock(admin, first=101)


@pytest.mark.parametrize("state", ["active", "scheduled", "expired"])
async def test_disable_promotion_ends_valid_schedule(harness, admin, state):
    client, responses, calls = harness
    now = datetime.now(UTC)
    start = now + timedelta(days=1) if state == "scheduled" else now - timedelta(days=2)
    end = now - timedelta(days=1) if state == "expired" else None
    promotion = {
        "id": "promo1",
        "name": "Sale",
        "type": "CATALOGUE",
        "startDate": start.isoformat(),
        "endDate": end.isoformat() if end else None,
    }
    responses["PromotionById"] = {"promotion": promotion}

    def update(body):
        schedule = body["variables"]["input"]
        assert datetime.fromisoformat(schedule["startDate"]) < datetime.fromisoformat(
            schedule["endDate"]
        )
        return {"promotionUpdate": {"promotion": {**promotion, **schedule}, "errors": []}}

    responses["PromotionUpdate"] = update
    result = await PromotionService(client).disable_promotion(admin, "promo1")
    assert result.end_date <= datetime.now(UTC)
    assert len(calls) == (1 if state == "expired" else 2)
