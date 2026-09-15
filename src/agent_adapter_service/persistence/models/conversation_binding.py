from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from agent_adapter_service.persistence.models.base import Base, TimestampMixin


class ConversationBindingModel(TimestampMixin, Base):
    __tablename__ = "conversation_binding"

    agui_thread_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    store_id: Mapped[str] = mapped_column(String(255))
    parlant_session_id: Mapped[str] = mapped_column(String(255), unique=True)
    parlant_customer_id: Mapped[str] = mapped_column(String(255))
    parlant_agent_id: Mapped[str] = mapped_column(String(255))
