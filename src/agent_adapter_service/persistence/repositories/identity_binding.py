from sqlalchemy import or_, select, update

from agent_adapter_service.persistence.contracts import (
    IdentityBinding,
    IdentityBindingCreate,
    identifier_adapter,
)
from agent_adapter_service.persistence.errors import BindingConflictError, BindingNotFoundError
from agent_adapter_service.persistence.models.identity_binding import IdentityBindingModel as Model
from agent_adapter_service.persistence.repositories.base import ScopedRepository


class SqlIdentityBindingRepository(ScopedRepository):
    async def _find(self, field: str, value: str) -> IdentityBinding | None:
        async with self.database.session() as session:
            row = await session.scalar(
                select(Model).where(
                    *self.scope_conditions(Model),
                    getattr(Model, field) == value,
                )
            )
            return IdentityBinding.model_validate(row) if row is not None else None

    async def find_by_visitor_id(self, visitor_id: str) -> IdentityBinding | None:
        return await self._find("visitor_id", visitor_id)

    async def find_by_saleor_user_id(self, saleor_user_id: str) -> IdentityBinding | None:
        return await self._find("saleor_user_id", saleor_user_id)

    async def find_by_parlant_customer_id(self, parlant_customer_id: str) -> IdentityBinding | None:
        return await self._find("parlant_customer_id", parlant_customer_id)

    async def create(self, data: IdentityBindingCreate) -> IdentityBinding:
        async with self.database.session() as session:
            row = Model(**self.scope.model_dump(), **data.model_dump())
            session.add(row)
            await session.flush()
            result = IdentityBinding.model_validate(row)
        return result

    async def upsert(
        self, data: IdentityBindingCreate, *, expected_customer_id: str | None = None
    ) -> IdentityBinding:
        """Insert or reuse a binding; changing customer requires the expected old ID.

        Changing visitor/user ownership remains an explicit bind_saleor_user operation.
        """
        key = "visitor_id" if data.visitor_id is not None else "saleor_user_id"
        async with self.database.session() as session:
            await self.insert_if_absent(
                session,
                Model,
                {**self.scope.model_dump(), **data.model_dump()},
                ["tenant_id", "store_id", key],
            )
            row = await session.scalar(
                select(Model).where(
                    *self.scope_conditions(Model),
                    getattr(Model, key) == getattr(data, key),
                )
            )
            if (
                row is None
                or row.visitor_id != data.visitor_id
                or row.saleor_user_id != data.saleor_user_id
            ):
                raise BindingConflictError("Identity is already bound differently")
            if row.parlant_customer_id != data.parlant_customer_id:
                if expected_customer_id is None:
                    raise BindingConflictError("Changing identity requires expected_customer_id")
                row = (
                    await session.scalars(
                        update(Model)
                        .where(
                            *self.scope_conditions(Model),
                            Model.id == row.id,
                            Model.parlant_customer_id == expected_customer_id,
                        )
                        .values(parlant_customer_id=data.parlant_customer_id)
                        .returning(Model)
                    )
                ).one_or_none()
                if row is None:
                    raise BindingConflictError("Identity binding changed concurrently")
            result = IdentityBinding.model_validate(row)
        return result

    async def update(
        self, binding_id: str, *, expected_customer_id: str, parlant_customer_id: str
    ) -> IdentityBinding:
        # Validate new IDs through the same public schema used by create.
        identifier_adapter.validate_python(parlant_customer_id)
        async with self.database.session() as session:
            row = (
                await session.scalars(
                    update(Model)
                    .where(
                        *self.scope_conditions(Model),
                        Model.id == binding_id,
                        Model.parlant_customer_id == expected_customer_id,
                    )
                    .values(parlant_customer_id=parlant_customer_id)
                    .returning(Model)
                )
            ).one_or_none()
            if row is None:
                exists = await session.scalar(
                    select(Model.id).where(
                        *self.scope_conditions(Model),
                        Model.id == binding_id,
                    )
                )
                if exists is None:
                    raise BindingNotFoundError("Identity binding was not found")
                raise BindingConflictError("Identity binding changed concurrently")
            result = IdentityBinding.model_validate(row)
        return result

    async def bind_saleor_user(
        self,
        visitor_id: str,
        saleor_user_id: str,
        *,
        expected_customer_id: str,
        parlant_customer_id: str | None = None,
    ) -> IdentityBinding:
        data = IdentityBindingCreate(
            visitor_id=visitor_id,
            saleor_user_id=saleor_user_id,
            parlant_customer_id=(
                parlant_customer_id if parlant_customer_id is not None else expected_customer_id
            ),
        )
        async with self.database.session() as session:
            row = (
                await session.scalars(
                    update(Model)
                    .where(
                        *self.scope_conditions(Model),
                        Model.visitor_id == visitor_id,
                        Model.parlant_customer_id == expected_customer_id,
                        or_(Model.saleor_user_id.is_(None), Model.saleor_user_id == saleor_user_id),
                    )
                    .values(
                        saleor_user_id=saleor_user_id, parlant_customer_id=data.parlant_customer_id
                    )
                    .returning(Model)
                )
            ).one_or_none()
            if row is None:
                exists = await session.scalar(
                    select(Model.id).where(
                        *self.scope_conditions(Model),
                        Model.visitor_id == visitor_id,
                    )
                )
                if exists is None:
                    raise BindingNotFoundError("Visitor binding was not found")
                raise BindingConflictError("Visitor binding changed or belongs to another user")
            result = IdentityBinding.model_validate(row)
        return result
