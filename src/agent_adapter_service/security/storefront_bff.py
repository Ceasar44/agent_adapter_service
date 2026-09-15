"""Versioned HMAC authentication of exact BFF request bytes."""

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agent_adapter_service.agui.schemas import TrustedStorefrontContext
from agent_adapter_service.core.exceptions import AuthorizationError
from agent_adapter_service.customer_identity.models import Identifier, StorefrontIdentity
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.persistence.repositories.bff_nonce import SqlBffNonceRepository

KID_PATTERN = r"[A-Za-z0-9_-]{1,64}"
PATHS = {"/api/agent", "/agui", "/api/agent/tool-results"}


class BffIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    visitor_id: Identifier
    status: Literal["anonymous", "authenticated", "unavailable"]
    saleor_user_id: Identifier | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "authenticated":
            if self.saleor_user_id is None:
                raise ValueError("User ID required")
        elif "saleor_user_id" in self.model_fields_set:
            raise ValueError("User ID is only allowed for authenticated identities")
        return self


class BffClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    iss: Annotated[str, Field(min_length=1, max_length=255)]
    aud: Annotated[str, Field(min_length=1, max_length=255)]
    iat: Annotated[int, Field(ge=0, le=253402300799)]
    exp: Annotated[int, Field(ge=0, le=253402300799)]
    jti: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")]
    scope: StoreScope
    identity: BffIdentity


@dataclass(frozen=True)
class VerifiedBffRequest:
    context: TrustedStorefrontContext
    claims: BffClaims


def decode_segment(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid base64url")
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value:
        raise ValueError("Noncanonical base64url")
    return decoded


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class StorefrontBffVerifier:
    def __init__(self, *, issuer: str, audience: str, scope: StoreScope,
                 keys: dict[str, bytes], nonces: SqlBffNonceRepository,
                 max_age: int = 60, clock_skew: int = 5, clock=time.time):
        self.issuer, self.audience, self.scope = issuer, audience, scope
        self.keys, self.nonces = keys, nonces
        self.max_age, self.clock_skew, self.clock = max_age, clock_skew, clock

    async def verify(self, authorization: str, *, method: str, path: str,
                     body: bytes, query: bytes = b"") -> VerifiedBffRequest:
        try:
            if len(authorization) > 8192 or method != "POST" or path not in PATHS or query:
                raise ValueError("Invalid request")
            version, kid, payload, signature = authorization.split(".")
            if version != "v1" or not re.fullmatch(KID_PATTERN, kid) or kid not in self.keys:
                raise ValueError("Invalid key")
            signing_input = "\n".join((version, kid, method, path,
                                       hashlib.sha256(body).hexdigest(), payload)).encode()
            expected = hmac.digest(self.keys[kid], signing_input, "sha256")
            if not hmac.compare_digest(expected, decode_segment(signature)):
                raise ValueError("Invalid signature")
            claims = BffClaims.model_validate(json.loads(
                decode_segment(payload).decode("utf-8"), object_pairs_hook=unique_object
            ))
            now = self.clock()
            if (claims.iss != self.issuer or claims.aud != self.audience
                    or not 0 < claims.exp - claims.iat <= self.max_age
                    or claims.iat > now + self.clock_skew
                    or now >= claims.exp + self.clock_skew):
                raise ValueError("Invalid claims")
        except (ValueError, UnicodeError, ValidationError, RecursionError):
            raise AuthorizationError("Invalid BFF authentication", http_status=401) from None
        if claims.scope != self.scope:
            raise AuthorizationError("BFF store scope is not allowed")
        await self.nonces.consume_once(
            issuer=claims.iss, audience=claims.aud, tenant_id=claims.scope.tenant_id,
            store_id=claims.scope.store_id, jti=claims.jti,
            expires_at=claims.exp + self.clock_skew,
        )
        identity = StorefrontIdentity.model_validate(claims.identity.model_dump())
        return VerifiedBffRequest(TrustedStorefrontContext(claims.scope, identity), claims)
