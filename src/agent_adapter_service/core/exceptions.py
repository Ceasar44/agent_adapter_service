"""Expected errors must contain public, JSON-safe information only."""

from pydantic import JsonValue


class AppError(Exception):
    code = "application_error"
    http_status = 400

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        details: dict[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or type(self).code
        self.http_status = http_status if http_status is not None else type(self).http_status
        self.details = details or {}


class IntegrationError(AppError):
    code = "integration_error"
    http_status = 502


class IdentityError(AppError):
    code = "identity_error"
    http_status = 401


class ConversationError(AppError):
    code = "conversation_error"
    http_status = 409


class AuthorizationError(AppError):
    code = "authorization_error"
    http_status = 403
