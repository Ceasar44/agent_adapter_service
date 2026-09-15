"""Public errors at the storage boundary; never include SQL or parameters."""

from agent_adapter_service.core.exceptions import AppError


class PersistenceError(AppError):
    code = "persistence_unavailable"
    http_status = 503


class BindingConflictError(AppError):
    code = "binding_conflict"
    http_status = 409


class BindingNotFoundError(AppError):
    code = "binding_not_found"
    http_status = 404
