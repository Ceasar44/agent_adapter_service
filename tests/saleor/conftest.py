import json

import httpx
import pytest
from pydantic import SecretStr

from agent_adapter_service.customer_identity.models import IdentityStatus, ResolvedCustomerIdentity
from agent_adapter_service.saleor.auth import AdminContext, CustomerContext
from agent_adapter_service.saleor.client import SaleorGraphQLClient


@pytest.fixture
def admin():
    return AdminContext("verified-admin")


@pytest.fixture
def customer():
    return CustomerContext(
        ResolvedCustomerIdentity(
            visitor_id="visitor",
            parlant_customer_id="parlant",
            saleor_user_id="user-1",
            status=IdentityStatus.AUTHENTICATED,
        )
    )


@pytest.fixture
async def harness():
    responses = {}
    calls = []

    async def handler(request):
        body = json.loads(request.content)
        calls.append((body, dict(request.headers)))
        result = responses[body["operationName"]]
        if callable(result):
            result = result(body)
        return httpx.Response(200, json={"data": result})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SaleorGraphQLClient(
            "https://saleor.test/graphql/", SecretStr("service"), http_client=http
        )
        yield client, responses, calls
        await client.close()


@pytest.fixture
def channel():
    return {
        "id": "channel-1",
        "name": "US",
        "slug": "us",
        "currencyCode": "USD",
        "isActive": True,
        "defaultCountry": {"code": "US", "country": "United States"},
    }


@pytest.fixture
def customer_data():
    return {
        "id": "user-1",
        "email": "a@example.test",
        "firstName": "A",
        "lastName": "B",
        "isActive": True,
        "languageCode": "EN",
    }


@pytest.fixture
def page_info():
    return {
        "hasNextPage": False,
        "hasPreviousPage": False,
        "startCursor": "one",
        "endCursor": "one",
    }


@pytest.fixture
def product():
    return {
        "id": "product-1",
        "name": "Jacket",
        "slug": "jacket",
        "thumbnail": None,
        "isAvailable": True,
        "isAvailableForPurchase": True,
        "pricing": None,
        "description": None,
        "category": None,
        "productType": {"id": "type", "name": "Clothes"},
        "defaultVariant": None,
    }


@pytest.fixture
def variant():
    return {
        "id": "variant-1",
        "name": "Black",
        "sku": "BLACK",
        "trackInventory": True,
        "quantityAvailable": None,
        "quantityLimitPerCustomer": None,
        "pricing": None,
        "product": {
            "id": "product-1",
            "name": "Jacket",
            "slug": "jacket",
            "isAvailable": True,
            "isAvailableForPurchase": True,
        },
        "assignedAttributes": [
            {
                "__typename": "AssignedSingleChoiceAttribute",
                "attribute": {"id": "color", "name": "Color", "slug": "color"},
                "choice": {"name": "Black", "slug": "black"},
            }
        ],
    }


@pytest.fixture
def order():
    money = {
        "gross": {"amount": 12.34, "currency": "USD"},
        "net": {"amount": 10, "currency": "USD"},
    }
    return {
        "id": "order-1",
        "number": "100",
        "created": "2026-09-16T00:00:00Z",
        "status": "UNFULFILLED",
        "statusDisplay": "Unfulfilled",
        "isPaid": True,
        "authorizeStatus": "FULL",
        "chargeStatus": "FULL",
        "channel": {"id": "channel-1", "slug": "us", "currencyCode": "USD"},
        "total": money,
        "user": {"id": "user-1"},
        "shippingMethodName": None,
        "subtotal": money,
        "shippingPrice": money,
        "fulfillments": [],
        "lines": [
            {
                "id": "line-1",
                "productName": "Jacket",
                "variantName": "Black",
                "productSku": "BLACK",
                "quantity": 2,
                "quantityFulfilled": 0,
                "quantityToFulfill": 2,
                "unitPrice": money,
                "totalPrice": money,
            }
        ],
    }
