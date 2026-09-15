from ..auth import AdminContext, CustomerContext, require_admin
from ..client import SaleorGateway
from ..errors import SaleorPermissionError, SaleorValidationError
from ..models import CustomerSummary
from ..saleor_client.input_types import CustomerInput, UserCreateInput
from .common import dto, identifier, input_fields, payload

CUSTOMER_FIELDS = {"firstName", "lastName", "email", "isActive", "languageCode"}


class CustomerService:
    def __init__(self, client: SaleorGateway) -> None:
        self.client = client

    async def get_customer(self, context: CustomerContext) -> CustomerSummary:
        if not isinstance(context, CustomerContext):
            raise SaleorPermissionError("Customer context required")
        customer_id = context.customer_id()
        if context.token is not None:
            result = await self.client.call(self.client.api.me, context=context)
            customer = dto(CustomerSummary, result.me)
        else:
            result = await self.client.call(
                self.client.api.customer_by_id, id=customer_id, context=context
            )
            customer = dto(CustomerSummary, result.user)
        if customer.id != customer_id or not customer.is_active:
            raise SaleorPermissionError("Customer identity is unavailable")
        return customer

    async def resolve_saleor_customer(self, context: CustomerContext) -> CustomerSummary:
        return await self.get_customer(context)

    async def get_admin_customer(self, context: AdminContext, id: str) -> CustomerSummary:
        require_admin(context)
        result = await self.client.call(
            self.client.api.customer_by_id, id=identifier(id), context=context
        )
        return dto(CustomerSummary, result.user)

    async def create_customer(
        self, context: AdminContext, input: UserCreateInput
    ) -> CustomerSummary:
        require_admin(context)
        input_fields(input, CUSTOMER_FIELDS)
        if not input.email or not input.email.strip():
            raise SaleorValidationError("Customer email is required")
        result = await self.client.call(
            self.client.api.customer_create, input=input, context=context
        )
        return dto(CustomerSummary, payload(result.customerCreate).user)

    async def update_customer(
        self,
        context: AdminContext,
        id: str,
        input: CustomerInput,
    ) -> CustomerSummary:
        require_admin(context)
        input_fields(input, CUSTOMER_FIELDS)
        result = await self.client.call(
            self.client.api.customer_update, id=identifier(id), input=input, context=context
        )
        return dto(CustomerSummary, payload(result.customerUpdate).user)
