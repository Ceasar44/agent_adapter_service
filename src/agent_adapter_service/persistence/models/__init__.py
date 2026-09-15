"""Import all models so Base.metadata is complete."""

from agent_adapter_service.persistence.models.audit_log import AuditLogModel
from agent_adapter_service.persistence.models.base import Base
from agent_adapter_service.persistence.models.bff_nonce import BffNonceRow
from agent_adapter_service.persistence.models.conversation_binding import ConversationBindingModel
from agent_adapter_service.persistence.models.identity_binding import IdentityBindingModel
from agent_adapter_service.persistence.models.identity_visitor import IdentityVisitorModel

__all__ = [
    "AuditLogModel",
    "Base",
    "BffNonceRow",
    "ConversationBindingModel",
    "IdentityBindingModel",
    "IdentityVisitorModel",
]
