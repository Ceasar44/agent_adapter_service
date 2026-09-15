from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from ..auth import AdminContext, require_admin
from ..client import SaleorGateway
from ..errors import SaleorValidationError
from ..models import PromotionRuleSummary, PromotionSummary
from ..saleor_client.input_types import (
    PromotionCreateInput,
    PromotionRuleCreateInput,
    PromotionRuleUpdateInput,
    PromotionUpdateInput,
)
from .common import amount, dto, identifier, payload, unique_ids


class PromotionService:
    """Write-only operations. Partial schedules are finally validated by Saleor."""

    def __init__(self, client: SaleorGateway) -> None:
        self.client = client

    async def disable_promotion(self, context: AdminContext, id: str) -> PromotionSummary:
        require_admin(context)
        result = await self.client.call(
            self.client.api.promotion_by_id, context=context, id=identifier(id)
        )
        current = dto(PromotionSummary, result.promotion)
        now = datetime.now(UTC)
        if current.end_date is not None and current.end_date <= now:
            return current
        # Saleor requires end > start even for a promotion scheduled in the future.
        start = min(current.start_date, now - timedelta(seconds=1))
        return await self.update_promotion(
            context, id, PromotionUpdateInput(startDate=start.isoformat(), endDate=now.isoformat())
        )

    @staticmethod
    def _dates(input: PromotionCreateInput | PromotionUpdateInput) -> None:
        dates = {}
        for field in ("startDate", "endDate"):
            raw = getattr(input, field)
            if raw is None:
                continue
            try:
                value = raw if isinstance(raw, datetime) else datetime.fromisoformat(raw)
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError
            except (TypeError, ValueError):
                raise SaleorValidationError("Promotion dates must include a timezone") from None
            dates[field] = value
        if len(dates) == 2 and dates["endDate"] <= dates["startDate"]:
            raise SaleorValidationError("Promotion end must be after start")
        if "name" in input.model_fields_set and (not input.name or not input.name.strip()):
            raise SaleorValidationError("Promotion name must not be empty")

    @staticmethod
    def _rule(input: BaseModel) -> None:
        raw = input.rewardValue
        if raw is not None:
            value = amount(raw)
            if input.rewardValueType == "PERCENTAGE" and value > 100:
                raise SaleorValidationError("Percentage reward must not exceed 100")
        for field in (
            "channels",
            "addChannels",
            "removeChannels",
            "gifts",
            "addGifts",
            "removeGifts",
        ):
            ids = getattr(input, field, None)
            if ids is not None:
                unique_ids(ids)
        for add, remove in (("addChannels", "removeChannels"), ("addGifts", "removeGifts")):
            if set(getattr(input, add, None) or []) & set(getattr(input, remove, None) or []):
                raise SaleorValidationError("Cannot add and remove the same item")

    async def create_promotion(
        self,
        context: AdminContext,
        input: PromotionCreateInput,
    ) -> PromotionSummary:
        require_admin(context)
        self._dates(input)
        for rule in input.rules or []:
            self._rule(rule)
        result = await self.client.call(
            self.client.api.promotion_create, context=context, input=input
        )
        return dto(PromotionSummary, payload(result.promotionCreate).promotion)

    async def update_promotion(
        self,
        context: AdminContext,
        id: str,
        input: PromotionUpdateInput,
    ) -> PromotionSummary:
        require_admin(context)
        if not input.model_fields_set:
            raise SaleorValidationError("Promotion update must not be empty")
        self._dates(input)
        result = await self.client.call(
            self.client.api.promotion_update, context=context, id=identifier(id), input=input
        )
        return dto(PromotionSummary, payload(result.promotionUpdate).promotion)

    async def create_rule(
        self,
        context: AdminContext,
        input: PromotionRuleCreateInput,
    ) -> PromotionRuleSummary:
        require_admin(context)
        identifier(input.promotion)
        self._rule(input)
        result = await self.client.call(
            self.client.api.promotion_rule_create, context=context, input=input
        )
        return dto(PromotionRuleSummary, payload(result.promotionRuleCreate).promotionRule)

    async def update_rule(
        self,
        context: AdminContext,
        id: str,
        input: PromotionRuleUpdateInput,
    ) -> PromotionRuleSummary:
        require_admin(context)
        if not input.model_fields_set:
            raise SaleorValidationError("Promotion rule update must not be empty")
        self._rule(input)
        result = await self.client.call(
            self.client.api.promotion_rule_update, context=context, id=identifier(id), input=input
        )
        return dto(PromotionRuleSummary, payload(result.promotionRuleUpdate).promotionRule)
