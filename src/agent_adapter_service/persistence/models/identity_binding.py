from uuid import uuid4

from sqlalchemy import CheckConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agent_adapter_service.persistence.models.base import Base, TimestampMixin


class IdentityBindingModel(TimestampMixin, Base):
    __tablename__ = "identity_binding"
    __table_args__ = (
        UniqueConstraint("tenant_id", "store_id", "visitor_id", name="uq_identity_visitor"),
        UniqueConstraint("tenant_id", "store_id", "saleor_user_id", name="uq_identity_saleor"),
        UniqueConstraint(
            "tenant_id", "store_id", "parlant_customer_id", name="uq_identity_customer"
        ),
        CheckConstraint(
            "visitor_id IS NOT NULL OR saleor_user_id IS NOT NULL", name="identity_present"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255))
    store_id: Mapped[str] = mapped_column(String(255))
    visitor_id: Mapped[str | None] = mapped_column(String(255))
    saleor_user_id: Mapped[str | None] = mapped_column(String(255))
    parlant_customer_id: Mapped[str] = mapped_column(String(255))
