from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from agent_adapter_service.persistence.models.base import Base, TimestampMixin


class IdentityVisitorModel(TimestampMixin, Base):
    """Multiple trusted visitors may reference one canonical M02 identity binding."""

    __tablename__ = "identity_visitor"

    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    visitor_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    binding_id: Mapped[str] = mapped_column(ForeignKey("identity_binding.id"))
    anonymous_customer_id: Mapped[str | None] = mapped_column(String(255))
