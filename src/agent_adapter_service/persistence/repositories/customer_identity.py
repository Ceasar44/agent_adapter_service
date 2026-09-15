"""M04 domain adapter; M02 canonical identities retain their uniqueness constraints."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_adapter_service.customer_identity.models import CustomerIdentityBinding
from agent_adapter_service.customer_identity.repository import IdentityBindingConflict
from agent_adapter_service.persistence.errors import BindingConflictError
from agent_adapter_service.persistence.models.identity_binding import (
    IdentityBindingModel as Binding,
)
from agent_adapter_service.persistence.models.identity_visitor import (
    IdentityVisitorModel as Visitor,
)
from agent_adapter_service.persistence.repositories.base import ScopedRepository


class SqlCustomerIdentityRepository(ScopedRepository):
    def _record(self, row: Binding, visitor: Visitor | None = None) -> CustomerIdentityBinding:
        return CustomerIdentityBinding(
            visitor_id=visitor.visitor_id if visitor else row.visitor_id,
            saleor_user_id=row.saleor_user_id,
            parlant_customer_id=row.parlant_customer_id,
            anonymous_customer_id=(
                visitor.anonymous_customer_id
                if visitor
                else row.parlant_customer_id
                if row.saleor_user_id is None
                else None
            ),
        )

    async def _canonical(self, session: AsyncSession, field: str, value: str) -> Binding | None:
        return await session.scalar(
            select(Binding).where(*self.scope_conditions(Binding), getattr(Binding, field) == value)
        )

    async def _visitor(self, session: AsyncSession, visitor_id: str) -> Visitor | None:
        return await session.scalar(
            select(Visitor).where(*self.scope_conditions(Visitor), Visitor.visitor_id == visitor_id)
        )

    async def _target(self, session: AsyncSession, visitor: Visitor) -> Binding:
        row = await self._canonical(session, "id", visitor.binding_id)
        if row is None:
            raise IdentityBindingConflict("Visitor has no identity in this store")
        return row

    async def find_by_visitor(self, visitor_id: str) -> CustomerIdentityBinding | None:
        async with self.database.session() as session:
            visitor = await self._visitor(session, visitor_id)
            if visitor:
                return self._record(await self._target(session, visitor), visitor)
            # Existing M02 bindings are adopted on the first write.
            row = await self._canonical(session, "visitor_id", visitor_id)
            return self._record(row) if row else None

    async def find_by_saleor_user(self, saleor_user_id: str) -> CustomerIdentityBinding | None:
        async with self.database.session() as session:
            row = await self._canonical(session, "saleor_user_id", saleor_user_id)
            return self._record(row) if row else None

    async def _attach(self, session: AsyncSession, visitor_id: str, row: Binding) -> Visitor:
        await self.insert_if_absent(
            session,
            Visitor,
            {
                **self.scope.model_dump(),
                "visitor_id": visitor_id,
                "binding_id": row.id,
                "anonymous_customer_id": row.parlant_customer_id
                if row.saleor_user_id is None
                else None,
            },
            ["tenant_id", "store_id", "visitor_id"],
        )
        visitor = await self._visitor(session, visitor_id)
        assert visitor is not None
        return visitor

    async def create(self, binding: CustomerIdentityBinding) -> CustomerIdentityBinding:
        # Only allocate a visitor identity here; authenticated creation is a subsequent CAS bind.
        if binding.visitor_id is None or binding.saleor_user_id is not None:
            raise ValueError("Create an anonymous visitor binding before binding a user")
        try:
            async with self.database.session() as session:
                # Insert first to serialize competing writes on SQLite as well as PostgreSQL.
                await self.insert_if_absent(
                    session,
                    Binding,
                    {
                        **self.scope.model_dump(),
                        "visitor_id": binding.visitor_id,
                        "saleor_user_id": None,
                        "parlant_customer_id": binding.parlant_customer_id,
                    },
                    ["tenant_id", "store_id", "visitor_id"],
                )
                row = await self._canonical(session, "visitor_id", binding.visitor_id)
                assert row is not None
                visitor = await self._attach(session, binding.visitor_id, row)
                return self._record(await self._target(session, visitor), visitor)
        except BindingConflictError:
            raise IdentityBindingConflict("Identity allocation conflicted") from None

    async def bind_saleor_user(
        self, visitor_id: str, saleor_user_id: str, *, expected_customer_id: str
    ) -> CustomerIdentityBinding:
        CustomerIdentityBinding(
            visitor_id=visitor_id,
            saleor_user_id=saleor_user_id,
            parlant_customer_id=expected_customer_id,
        )
        # A user created concurrently by another visitor may win the unique user constraint.
        for attempt in range(3):
            try:
                async with self.database.session() as session:
                    # Obtain a write lock before reading the mutable visitor association.
                    await session.execute(
                        update(Visitor)
                        .where(*self.scope_conditions(Visitor), Visitor.visitor_id == visitor_id)
                        .values(binding_id=Visitor.binding_id)
                    )
                    visitor = await self._visitor(session, visitor_id)
                    if visitor is None:
                        legacy = await self._canonical(session, "visitor_id", visitor_id)
                        if legacy is None:
                            raise IdentityBindingConflict("Visitor identity was not found")
                        visitor = await self._attach(session, visitor_id, legacy)
                    row = await self._target(session, visitor)
                    if row.saleor_user_id == saleor_user_id:
                        return self._record(row, visitor)
                    if (
                        row.saleor_user_id is not None
                        or row.parlant_customer_id != expected_customer_id
                    ):
                        raise IdentityBindingConflict("Visitor belongs to a different identity")
                    target = await self._canonical(session, "saleor_user_id", saleor_user_id)
                    if target is None:
                        row.saleor_user_id = saleor_user_id
                        target = row
                    visitor.binding_id = target.id
                    await session.flush()
                    return self._record(target, visitor)
            except BindingConflictError:
                if attempt == 2:
                    raise IdentityBindingConflict("Identity changed concurrently") from None
        raise AssertionError("Unreachable")
