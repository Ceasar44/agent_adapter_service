"""Opaque server-to-server credentials verified by FastMCP's auth middleware."""

import hashlib
import hmac
from collections.abc import Mapping

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from pydantic import SecretStr

from agent_adapter_service.core.exceptions import AuthorizationError
from agent_adapter_service.security.admin_mcp_policy import AdminMcpPrincipal


class AdminMcpAuth(TokenVerifier):
    def __init__(self, credentials: Mapping[str, tuple[SecretStr, AdminMcpPrincipal]]) -> None:
        super().__init__()
        self._credentials = []
        seen = set()
        for secret, principal in credentials.values():
            raw = secret.get_secret_value()
            digest = hashlib.sha256(raw.encode()).digest()
            if len(raw) < 32 or digest in seen:
                raise ValueError("Admin tokens must be unique and at least 32 characters")
            seen.add(digest)
            self._credentials.append((digest, principal))
        if not self._credentials:
            raise ValueError("At least one admin credential is required")

    async def verify_token(self, token: str) -> AccessToken | None:
        digest = hashlib.sha256(token.encode()).digest()
        for expected, principal in self._credentials:
            if hmac.compare_digest(expected, digest):
                return AccessToken(
                    token=token, client_id=principal.client_id, scopes=sorted(principal.scopes)
                )
        return None

    async def get_principal(self, context: object = None) -> AdminMcpPrincipal:
        # Ignore caller metadata/arguments. Revalidate the actual authenticated credential.
        access = get_access_token()
        if access is not None:
            digest = hashlib.sha256(access.token.encode()).digest()
            for expected, principal in self._credentials:
                if hmac.compare_digest(expected, digest):
                    return principal
        raise AuthorizationError("Admin authentication required")
