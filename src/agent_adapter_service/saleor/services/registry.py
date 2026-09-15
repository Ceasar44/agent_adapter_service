from ..client import SaleorGateway
from .channel_service import ChannelService
from .collection_service import CollectionService
from .customer_service import CustomerService
from .inventory_service import InventoryService
from .order_service import OrderService
from .product_service import ProductService
from .promotion_service import PromotionService


def create_services(client: SaleorGateway) -> dict[str, object]:
    channels = ChannelService(client)
    return {
        "channel": channels,
        "collection": CollectionService(client),
        "product": ProductService(client, channels),
        "customer": CustomerService(client),
        "order": OrderService(client),
        "inventory": InventoryService(client),
        "promotion": PromotionService(client),
    }
