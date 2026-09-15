from ..auth import AdminContext, require_admin
from ..client import SaleorGateway
from ..errors import SaleorValidationError
from ..models import ProductReference
from ..saleor_client.input_types import CollectionCreateInput, CollectionInput
from .common import dto, identifier, input_fields, payload, unique_ids


class CollectionService:
    def __init__(self, client: SaleorGateway) -> None:
        self.client = client

    async def create_collection(
        self, context: AdminContext, input: CollectionCreateInput
    ) -> ProductReference:
        require_admin(context)
        input_fields(input, {"name", "slug", "description", "seo", "products"})
        if not input.name or not input.name.strip():
            raise SaleorValidationError("Collection name is required")
        if input.products is not None:
            self._products(input.products)
        result = await self.client.call(
            self.client.api.collection_create, context=context, input=input
        )
        return dto(ProductReference, payload(result.collectionCreate).collection)

    async def update_collection(
        self, context: AdminContext, id: str, input: CollectionInput
    ) -> ProductReference:
        require_admin(context)
        input_fields(input, {"name", "slug", "description", "seo"})
        if "name" in input.model_fields_set and (not input.name or not input.name.strip()):
            raise SaleorValidationError("Collection name is required")
        result = await self.client.call(
            self.client.api.collection_update, context=context, id=identifier(id), input=input
        )
        return dto(ProductReference, payload(result.collectionUpdate).collection)

    @staticmethod
    def _products(products: list[str]) -> None:
        if not 1 <= len(products) <= 100:
            raise SaleorValidationError("Supply between 1 and 100 products")
        unique_ids(products)

    async def add_products_to_collection(
        self, context: AdminContext, id: str, products: list[str]
    ) -> ProductReference:
        require_admin(context)
        self._products(products)
        result = await self.client.call(
            self.client.api.collection_add_products,
            context=context,
            collectionId=identifier(id),
            products=products,
        )
        return dto(ProductReference, payload(result.collectionAddProducts).collection)
