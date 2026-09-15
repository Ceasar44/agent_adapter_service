from decimal import Decimal

from ..auth import AdminContext, require_admin
from ..client import SaleorGateway
from ..errors import SaleorNotFoundError, SaleorValidationError
from ..models import (
    Page,
    ProductDetail,
    ProductPublicationResult,
    ProductReference,
    ProductSummary,
    VariantMutationResult,
    VariantPriceResult,
    VariantSummary,
)
from ..saleor_client.input_types import (
    ProductChannelListingAddInput,
    ProductChannelListingUpdateInput,
    ProductCreateInput,
    ProductInput,
    ProductOrder,
    ProductVariantChannelListingAddInput,
    ProductVariantCreateInput,
    ProductVariantInput,
    ProductWhereInput,
)
from .channel_service import ChannelService
from .common import amount, dto, identifier, input_fields, limit, page, payload

PRODUCT_FIELDS = {
    "name",
    "slug",
    "description",
    "category",
    "collections",
    "attributes",
    "seo",
    "weight",
    "taxClass",
    "externalReference",
}
VARIANT_FIELDS = {
    "name",
    "sku",
    "attributes",
    "trackInventory",
    "quantityLimitPerCustomer",
    "weight",
}


class ProductService:
    def __init__(self, client: SaleorGateway, channels: ChannelService | None = None) -> None:
        self.client = client
        self.channels = channels or ChannelService(client)

    async def search_products(
        self,
        query: str | None,
        channel: str,
        *,
        filters: ProductWhereInput | None = None,
        first: int = 20,
        after: str | None = None,
        sort_by: ProductOrder | None = None,
    ) -> Page[ProductSummary]:
        limit(first)
        await self.channels.require_active(slug=channel)
        # Enforce outer publication constraints even if caller supplies nested OR filters.
        where = ProductWhereInput(isPublished=True, isVisibleInListing=True)
        if filters is not None:
            where.AND = [filters]
        result = await self.client.call(
            self.client.api.product_list,
            channel=channel,
            first=first,
            after=after,
            search=query,
            where=where,
            sortBy=sort_by,
        )
        return page(ProductSummary, result.products)

    async def get_product(
        self,
        channel: str,
        *,
        id: str | None = None,
        slug: str | None = None,
    ) -> ProductDetail:
        if (id is None) == (slug is None):
            raise SaleorValidationError("Supply exactly one product ID or slug")
        await self.channels.require_active(slug=channel)
        if id is not None:
            result = await self.client.call(
                self.client.api.product_by_id, id=identifier(id), channel=channel
            )
        else:
            result = await self.client.call(
                self.client.api.product_by_slug, slug=identifier(slug), channel=channel
            )
        return dto(ProductDetail, result.product)

    async def get_variants(
        self,
        product_id: str,
        channel: str,
        *,
        first: int = 20,
        after: str | None = None,
        attribute_limit: int = 100,
    ) -> Page[VariantSummary]:
        limit(first)
        limit(attribute_limit)
        await self.channels.require_active(slug=channel)
        result = await self.client.call(
            self.client.api.variant_list,
            productId=identifier(product_id),
            channel=channel,
            first=first,
            after=after,
            attributeLimit=attribute_limit,
        )
        if result.product is None:
            raise SaleorNotFoundError("Product not found")
        variants = page(VariantSummary, result.product.productVariants)
        if any(v.product.id != product_id for v in variants.items):
            raise SaleorValidationError("Variant does not belong to product")
        return variants

    async def get_variant(
        self,
        channel: str,
        *,
        id: str | None = None,
        sku: str | None = None,
        product_id: str | None = None,
    ) -> VariantSummary:
        if (id is None) == (sku is None):
            raise SaleorValidationError("Supply exactly one variant ID or SKU")
        await self.channels.require_active(slug=channel)
        if id is not None:
            result = await self.client.call(
                self.client.api.variant_by_id,
                id=identifier(id),
                channel=channel,
                attributeLimit=100,
            )
        else:
            result = await self.client.call(
                self.client.api.variant_by_sku,
                sku=identifier(sku),
                channel=channel,
                attributeLimit=100,
            )
        variant = dto(VariantSummary, result.productVariant)
        if product_id is not None and variant.product.id != product_id:
            raise SaleorValidationError("Variant does not belong to product")
        return variant

    async def resolve_variant(
        self,
        product_id: str,
        channel: str,
        attributes: dict[str, str],
        *,
        max_pages: int = 10,
    ) -> VariantSummary:
        """Match exact attribute slug -> choice slug, never select an arbitrary first match."""
        limit(max_pages, 20)
        if not attributes or any(not k or not v for k, v in attributes.items()):
            raise SaleorValidationError("Attribute selections are required")
        matches: dict[str, VariantSummary] = {}
        cursor = None
        seen = set()
        for _ in range(max_pages):
            batch = await self.get_variants(product_id, channel, first=100, after=cursor)
            for variant in batch.items:
                choices = {}
                for attr in variant.assigned_attributes:
                    values = list(attr.choices or [])
                    if attr.choice is not None:
                        values.append(attr.choice)
                    selected = {v.slug for v in values}
                    if isinstance(attr.swatch, dict):
                        selected.add(attr.swatch.get("slug"))
                    if attr.text is not None:
                        selected.add(attr.text)
                    choices[attr.attribute.slug] = selected
                if all(value in choices.get(key, set()) for key, value in attributes.items()):
                    matches[variant.id] = variant
            if len(matches) > 1:
                raise SaleorValidationError("Variant selection is ambiguous")
            if not batch.page_info.has_next_page:
                if not matches:
                    raise SaleorNotFoundError("No matching variant")
                return next(iter(matches.values()))
            cursor = batch.page_info.end_cursor
            if not cursor or cursor in seen:
                raise SaleorValidationError("Invalid variant pagination")
            seen.add(cursor)
        raise SaleorValidationError("Narrow variant selection; pagination limit reached")

    async def create_product(
        self, context: AdminContext, input: ProductCreateInput
    ) -> ProductReference:
        require_admin(context)
        input_fields(input, PRODUCT_FIELDS | {"productType"})
        identifier(input.productType)
        if not input.name or not input.name.strip():
            raise SaleorValidationError("Product name is required")
        result = await self.client.call(
            self.client.api.product_create, context=context, input=input
        )
        return dto(ProductReference, payload(result.productCreate).product)

    async def update_product(
        self,
        context: AdminContext,
        id: str,
        input: ProductInput,
    ) -> ProductReference:
        require_admin(context)
        input_fields(input, PRODUCT_FIELDS)
        result = await self.client.call(
            self.client.api.product_update, context=context, id=identifier(id), input=input
        )
        return dto(ProductReference, payload(result.productUpdate).product)

    async def publish_product(
        self,
        context: AdminContext,
        id: str,
        channel_id: str,
        *,
        published: bool,
    ) -> ProductPublicationResult:
        require_admin(context)
        if type(published) is not bool:
            raise SaleorValidationError("Publication flag must be boolean")
        await self.channels.require_active(id=channel_id)
        result = await self.client.call(
            self.client.api.product_channel_listing_update,
            context=context,
            id=identifier(id),
            input=ProductChannelListingUpdateInput(
                updateChannels=[
                    ProductChannelListingAddInput(channelId=channel_id, isPublished=published)
                ]
            ),
        )
        return dto(ProductPublicationResult, payload(result.productChannelListingUpdate).product)

    async def create_variant(
        self,
        context: AdminContext,
        input: ProductVariantCreateInput,
    ) -> VariantMutationResult:
        require_admin(context)
        input_fields(input, VARIANT_FIELDS | {"product"})
        identifier(input.product)
        self._variant_input(input)
        result = await self.client.call(
            self.client.api.variant_create, context=context, input=input
        )
        return dto(VariantMutationResult, payload(result.productVariantCreate).productVariant)

    async def update_variant(
        self,
        context: AdminContext,
        id: str,
        input: ProductVariantInput,
    ) -> VariantMutationResult:
        require_admin(context)
        input_fields(input, VARIANT_FIELDS)
        self._variant_input(input)
        result = await self.client.call(
            self.client.api.variant_update, context=context, id=identifier(id), input=input
        )
        return dto(VariantMutationResult, payload(result.productVariantUpdate).productVariant)

    @staticmethod
    def _variant_input(input: ProductVariantInput | ProductVariantCreateInput) -> None:
        value = input.quantityLimitPerCustomer
        if value is not None and value < 1:
            raise SaleorValidationError("Quantity limit must be positive")

    async def update_variant_price(
        self,
        context: AdminContext,
        id: str,
        channel_id: str,
        price: Decimal,
        currency: str,
    ) -> VariantPriceResult:
        require_admin(context)
        value = amount(price)
        await self.channels.require_active(id=channel_id, currency=currency)
        result = await self.client.call(
            self.client.api.variant_channel_listing_update,
            context=context,
            id=identifier(id),
            input=[ProductVariantChannelListingAddInput(channelId=channel_id, price=value)],
        )
        return dto(VariantPriceResult, payload(result.productVariantChannelListingUpdate).variant)
