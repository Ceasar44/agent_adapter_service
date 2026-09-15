from ..auth import AdminContext, require_admin
from ..client import SaleorGateway
from ..errors import SaleorValidationError
from ..models import Page, StockMutationResult, StockSummary
from ..saleor_client.input_types import StockInput, StockFilterInput
from .common import dto, identifier, payload, unique_ids, limit, page


class InventoryService:
    """Warehouse quantities with bounded, paginated admin stock reads."""

    def __init__(self, client: SaleorGateway) -> None:
        self.client = client

    async def get_stock(
        self,
        context: AdminContext,
        *,
        first: int = 20,
        after: str | None = None,
        filter: StockFilterInput | None = None,
    ) -> Page[StockSummary]:
        require_admin(context)
        limit(first)
        result = await self.client.call(
            self.client.api.stock_list, context=context, first=first, after=after, filter_=filter
        )
        return page(StockSummary, result.stocks)

    @staticmethod
    def _validate(stocks: list[StockInput]) -> None:
        if not stocks or len(stocks) > 100:
            raise SaleorValidationError("Supply between 1 and 100 stock entries")
        unique_ids([stock.warehouse for stock in stocks])
        if any(stock.quantity < 0 for stock in stocks):
            raise SaleorValidationError("Stock quantity must be nonnegative")

    async def create_stock(
        self,
        context: AdminContext,
        variant_id: str,
        stocks: list[StockInput],
    ) -> StockMutationResult:
        require_admin(context)
        self._validate(stocks)
        result = await self.client.call(
            self.client.api.variant_stocks_create,
            context=context,
            variantId=identifier(variant_id),
            stocks=stocks,
        )
        return dto(StockMutationResult, payload(result.productVariantStocksCreate).productVariant)

    async def update_stock(
        self,
        context: AdminContext,
        variant_id: str,
        stocks: list[StockInput],
    ) -> StockMutationResult:
        require_admin(context)
        self._validate(stocks)
        result = await self.client.call(
            self.client.api.variant_stocks_update,
            context=context,
            variantId=identifier(variant_id),
            stocks=stocks,
        )
        return dto(StockMutationResult, payload(result.productVariantStocksUpdate).productVariant)
