"""FastAPI dependencies; service implementations are registered during startup."""

from typing import Annotated, TypeVar

from fastapi import Depends, Request

from agent_adapter_service.agent.runtime import AgentRuntime
from agent_adapter_service.app.lifespan import AppResources
from agent_adapter_service.conversations.service import ConversationService
from agent_adapter_service.core.exceptions import AppError
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.rag.client import RagClient
from agent_adapter_service.saleor.client import SaleorGraphQLClient
from agent_adapter_service.saleor.services.channel_service import ChannelService
from agent_adapter_service.saleor.services.customer_service import CustomerService
from agent_adapter_service.saleor.services.inventory_service import InventoryService
from agent_adapter_service.saleor.services.order_service import OrderService
from agent_adapter_service.saleor.services.product_service import ProductService
from agent_adapter_service.saleor.services.promotion_service import PromotionService

ServiceT = TypeVar("ServiceT")


def get_resources(request: Request) -> AppResources:
    resources = getattr(request.app.state, "resources", None)
    if not isinstance(resources, AppResources) or not resources.ready:
        raise AppError("Application resources are not ready", code="not_ready", http_status=503)
    return resources


ResourcesDependency = Annotated[AppResources, Depends(get_resources)]


def get_rag_client(resources: ResourcesDependency) -> RagClient:
    if not isinstance(resources.rag_client, RagClient):
        raise AppError("RAG is not available", code="rag_unavailable", http_status=503)
    return resources.rag_client


def get_agent_runtime(resources: ResourcesDependency) -> AgentRuntime:
    if not isinstance(resources.parlant_runtime, AgentRuntime):
        raise AppError("Parlant is not available", code="parlant_unavailable", http_status=503)
    resources.parlant_runtime.get_agent()
    return resources.parlant_runtime


def get_database(resources: ResourcesDependency) -> Database:
    if not isinstance(resources.database, Database):
        raise AppError("Database is not available", code="database_unavailable", http_status=503)
    return resources.database


def get_service(resources: AppResources, name: str, service_type: type[ServiceT]) -> ServiceT:
    service = resources.services.get(name)
    if service is None:
        raise AppError("Service is not available", code="service_unavailable", http_status=503)
    if not isinstance(service, service_type):
        raise TypeError(f"Service '{name}' does not implement its registered interface")
    return service


def get_identity_service(resources: ResourcesDependency) -> object:
    return get_service(resources, "identity", object)


def get_conversation_service(resources: ResourcesDependency) -> ConversationService:
    return get_service(resources, "conversation", ConversationService)


def get_saleor_client(resources: ResourcesDependency) -> SaleorGraphQLClient:
    if not isinstance(resources.saleor_client, SaleorGraphQLClient):
        raise AppError("Saleor is not available", code="saleor_unavailable", http_status=503)
    return resources.saleor_client


def get_product_service(resources: ResourcesDependency) -> ProductService:
    return get_service(resources, "product", ProductService)


def get_order_service(resources: ResourcesDependency) -> OrderService:
    return get_service(resources, "order", OrderService)


def get_channel_service(resources: ResourcesDependency) -> ChannelService:
    return get_service(resources, "channel", ChannelService)


def get_customer_service(resources: ResourcesDependency) -> CustomerService:
    return get_service(resources, "customer", CustomerService)


def get_inventory_service(resources: ResourcesDependency) -> InventoryService:
    return get_service(resources, "inventory", InventoryService)


def get_promotion_service(resources: ResourcesDependency) -> PromotionService:
    return get_service(resources, "promotion", PromotionService)
