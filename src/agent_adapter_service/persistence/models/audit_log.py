from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import Mapped, mapped_column, validates

from agent_adapter_service.persistence.models.base import Base, UTCDateTime, utc_now
from agent_adapter_service.persistence.sanitization import sanitize_arguments


class AuditLogModel(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_scope_timestamp", "tenant_id", "store_id", "timestamp"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255))
    store_id: Mapped[str] = mapped_column(String(255))
    actor: Mapped[str] = mapped_column(String(255))
    surface: Mapped[str] = mapped_column(String(50))
    tool_name: Mapped[str] = mapped_column(String(255))
    arguments_summary: Mapped[dict] = mapped_column(JSON)
    result_status: Mapped[str] = mapped_column(String(50))
    trace_id: Mapped[str | None] = mapped_column(String(255), index=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    @validates("arguments_summary")
    def sanitize_summary(self, key: str, value: dict) -> dict:
        return sanitize_arguments(value)
