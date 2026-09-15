from ..auth import AdminContext, ServiceContext, require_admin
from ..client import SaleorGateway
from ..errors import SaleorValidationError
from ..models import ChannelSummary
from .common import dto, identifier


class ChannelService:
    def __init__(self, client: SaleorGateway) -> None:
        self.client = client

    async def list_channels(self, context: AdminContext) -> list[ChannelSummary]:
        require_admin(context)
        result = await self.client.call(self.client.api.channel_list, context=context)
        if result.channels is None:
            raise SaleorValidationError("Channels are unavailable")
        return [dto(ChannelSummary, item) for item in result.channels]

    async def get_channel(
        self,
        *,
        id: str | None = None,
        slug: str | None = None,
    ) -> ChannelSummary:
        """Internal discovery using service credentials, not a customer-facing tool."""
        if (id is None) == (slug is None):
            raise SaleorValidationError("Supply exactly one channel ID or slug")
        if id is not None:
            result = await self.client.call(
                self.client.api.channel_by_id, id=identifier(id), context=ServiceContext()
            )
        else:
            result = await self.client.call(
                self.client.api.channel_by_slug, slug=identifier(slug), context=ServiceContext()
            )
        return dto(ChannelSummary, result.channel)

    async def require_active(
        self,
        *,
        id: str | None = None,
        slug: str | None = None,
        currency: str | None = None,
    ) -> ChannelSummary:
        channel = await self.get_channel(id=id, slug=slug)
        if not channel.is_active:
            raise SaleorValidationError("Channel is inactive")
        if currency is not None and channel.currency_code != currency:
            raise SaleorValidationError("Currency does not match channel")
        return channel
