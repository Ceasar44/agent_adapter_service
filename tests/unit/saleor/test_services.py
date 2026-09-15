import pytest

from agent_adapter_service.saleor.errors import SaleorPermissionError, SaleorValidationError
from agent_adapter_service.saleor.services.order_service import OrderService
from agent_adapter_service.saleor.services.product_service import ProductService
from agent_adapter_service.saleor.saleor_client.input_types import ProductInput


async def test_ownership_denial_never_fetches_order_details(harness, customer):
    client, responses, calls = harness
    responses["OrderOwnershipById"] = {"order": {"id": "private", "user": {"id": "other"}}}
    with pytest.raises(SaleorPermissionError):
        await OrderService(client).get_customer_order(customer, "private")
    assert [body["operationName"] for body, _ in calls] == ["OrderOwnershipById"]


async def test_mutation_user_errors_do_not_become_success(harness, admin):
    client, responses, _ = harness
    responses["ProductUpdate"] = {
        "productUpdate": {
            "product": None,
            "errors": [
                {"field": "name", "code": "INVALID", "message": "invalid upstream value"},
            ],
        }
    }
    with pytest.raises(SaleorValidationError):
        await ProductService(client).update_product(admin, "p1", ProductInput(name="test"))
