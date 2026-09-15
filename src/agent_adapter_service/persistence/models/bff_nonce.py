"""Shared replay protection; expiry includes the verifier's clock allowance."""

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from agent_adapter_service.persistence.models.base import Base


class BffNonceRow(Base):
    __tablename__ = "bff_nonces"

    issuer: Mapped[str] = mapped_column(String(255), primary_key=True)
    audience: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    jti: Mapped[str] = mapped_column(String(128), primary_key=True)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)
