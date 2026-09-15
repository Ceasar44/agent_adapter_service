"""Stable service DTOs; only fields available in the existing documents are modeled."""

from datetime import datetime
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic.alias_generators import to_camel


class DTO(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True, allow_inf_nan=False
    )


class Money(DTO):
    # Source schema represents amounts as Float. This prevents further float arithmetic;
    # it cannot recover precision already lost by the upstream JSON/Float representation.
    amount: Decimal
    currency: str


class TaxedMoney(DTO):
    gross: Money
    net: Money


class PriceRange(DTO):
    start: TaxedMoney | None
    stop: TaxedMoney | None


class ProductPricing(DTO):
    on_sale: bool | None
    display_gross_prices: bool
    price_range: PriceRange | None


class VariantPricing(DTO):
    on_sale: bool | None
    price: TaxedMoney | None
    price_undiscounted: TaxedMoney | None


class PageInfo(DTO):
    has_next_page: bool
    has_previous_page: bool
    start_cursor: str | None
    end_cursor: str | None


ItemT = TypeVar("ItemT")


class Page(DTO, Generic[ItemT]):
    items: list[ItemT]
    page_info: PageInfo


class Entity(DTO):
    id: str


class NamedEntity(Entity):
    name: str


class ProductReference(NamedEntity):
    slug: str


class Image(DTO):
    url: str
    alt: str | None


class ProductSummary(ProductReference):
    thumbnail: Image | None
    is_available: bool | None
    is_available_for_purchase: bool | None
    pricing: ProductPricing | None


class VariantReference(NamedEntity):
    sku: str | None


class ProductDetail(ProductSummary):
    description: JsonValue
    category: ProductReference | None
    product_type: NamedEntity
    default_variant: VariantReference | None


class VariantProduct(ProductReference):
    is_available: bool | None
    is_available_for_purchase: bool | None


class AttributeChoice(DTO):
    name: str | None
    slug: str | None


class AttributeDefinition(Entity):
    name: str | None
    slug: str | None


class VariantAttribute(DTO):
    kind: str = Field(alias="__typename")
    attribute: AttributeDefinition
    choice: AttributeChoice | None = None
    choices: list[AttributeChoice] | None = None
    swatch: JsonValue = None
    text: str | None = None
    number: float | None = None
    boolean: bool | None = None


class VariantSummary(VariantReference):
    product: VariantProduct
    track_inventory: bool
    quantity_available: int | None
    quantity_limit_per_customer: int | None
    pricing: VariantPricing | None
    assigned_attributes: list[VariantAttribute]


class Country(DTO):
    code: str
    country: str


class ChannelReference(Entity):
    slug: str
    currency_code: str


class ChannelSummary(ChannelReference):
    name: str
    is_active: bool
    default_country: Country


class CustomerSummary(Entity):
    email: str
    first_name: str
    last_name: str
    is_active: bool
    language_code: str


class OrderSummary(Entity):
    number: str
    created: datetime
    status: str
    status_display: str
    is_paid: bool
    authorize_status: str
    charge_status: str
    channel: ChannelReference
    total: TaxedMoney


class OrderLine(Entity):
    product_name: str
    variant_name: str
    product_sku: str | None
    quantity: int
    quantity_fulfilled: int
    quantity_to_fulfill: int
    unit_price: TaxedMoney
    total_price: TaxedMoney


class FulfillmentLine(Entity):
    quantity: int
    order_line: Entity | None


class Fulfillment(Entity):
    status: str
    tracking_number: str
    lines: list[FulfillmentLine] | None


class FulfillmentDetail(Fulfillment):
    created: datetime


class OrderDetail(OrderSummary):
    user: Entity | None
    shipping_method_name: str | None
    subtotal: TaxedMoney
    shipping_price: TaxedMoney
    lines: list[OrderLine]
    fulfillments: list[FulfillmentDetail]


class OrderMutationResult(Entity):
    number: str
    status: str


class FulfillResult(DTO):
    order: OrderMutationResult
    fulfillments: list[Fulfillment] | None


class VariantMutationResult(VariantReference):
    product: Entity
    track_inventory: bool


class StockMutationResult(Entity):
    sku: str | None


class StockSummary(Entity):
    warehouse: Entity
    product_variant: Entity
    quantity: int
    quantity_allocated: int


class ProductChannelListing(DTO):
    channel: ChannelReference
    is_published: bool
    published_at: datetime | None
    visible_in_listings: bool
    is_available_for_purchase: bool | None
    available_for_purchase_at: datetime | None


class ProductPublicationResult(Entity):
    channel_listings: list[ProductChannelListing] | None


class VariantChannelListing(DTO):
    channel: ChannelReference
    price: Money | None


class VariantPriceResult(StockMutationResult):
    channel_listings: list[VariantChannelListing] | None


class PromotionSummary(NamedEntity):
    type: str | None
    start_date: datetime
    end_date: datetime | None


class PromotionRuleSummary(Entity):
    name: str | None
    catalogue_predicate: JsonValue
    order_predicate: JsonValue
    reward_value: Decimal | None
    reward_value_type: str | None
    reward_type: str | None
    channels: list[ChannelReference] | None
