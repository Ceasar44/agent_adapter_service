import base64
import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent_adapter_service.core.exceptions import AppError
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.security.storefront_bff import StorefrontBffVerifier

VECTOR = json.loads(Path(__file__).with_name("bff_vector.json").read_text(encoding="utf-8"))


def signed(claims=None, *, body=None, path="/api/agent", kid="active"):
    payload = base64.urlsafe_b64encode(json.dumps(
        claims or VECTOR["claims"], separators=(",", ":")
    ).encode()).decode().rstrip("=")
    body = VECTOR["body"].encode() if body is None else body
    message = "\n".join(("v1", kid, "POST", path, hashlib.sha256(body).hexdigest(), payload))
    signature = base64.urlsafe_b64encode(hmac.digest(
        base64.b64decode(VECTOR["key"]), message.encode(), "sha256"
    )).decode().rstrip("=")
    return f"v1.{kid}.{payload}.{signature}"


def verifier(nonces=None):
    return StorefrontBffVerifier(
        issuer="storefront", audience="adapter", scope=StoreScope(tenant_id="tenant", store_id="store"),
        keys={"active": base64.b64decode(VECTOR["key"])}, nonces=nonces or AsyncMock(),
        clock=lambda: 1800000000,
    )


async def test_node_vector():
    check = verifier()
    result = await check.verify(VECTOR["authorization"], method="POST", path=VECTOR["path"],
                                body=VECTOR["body"].encode())
    assert result.context.identity.visitor_id == "visitor"
    assert result.context.approvals == ()
    check.nonces.consume_once.assert_awaited_once()


@pytest.mark.parametrize("change,status", [
    ({"aud": "other"}, 401), ({"iss": "other"}, 401),
    ({"iat": 1800000010}, 401), ({"exp": 1799999995}, 401),
    ({"exp": 1800000061}, 401), ({"iat": True}, 401),
    ({"scope": {"tenant_id": "other", "store_id": "store"}}, 403),
    ({"approvals": []}, 401),
    ({"identity": {"visitor_id": "visitor", "status": "authenticated"}}, 401),
    ({"identity": {"visitor_id": "visitor", "status": "anonymous", "saleor_user_id": "u"}}, 401),
])
async def test_reject_claims(change, status):
    check = verifier()
    with pytest.raises(AppError) as error:
        await check.verify(signed(VECTOR["claims"] | change), method="POST", path=VECTOR["path"],
                           body=VECTOR["body"].encode())
    assert error.value.http_status == status
    check.nonces.consume_once.assert_not_awaited()


@pytest.mark.parametrize("changes", [
    {"body": b"{}"}, {"path": "/agui"}, {"method": "GET"}, {"query": b"a=1"},
    {"authorization": "bad"}, {"authorization": "x" * 8193},
    {"authorization": signed(kid="unknown")},
])
async def test_request_binding(changes):
    args = dict(authorization=VECTOR["authorization"], method="POST", path=VECTOR["path"],
                body=VECTOR["body"].encode()) | changes
    with pytest.raises(AppError) as error:
        await verifier().verify(**args)
    assert error.value.http_status == 401


@pytest.mark.parametrize("identity", [
    {"visitor_id": "visitor", "status": "anonymous"},
    {"visitor_id": "visitor", "status": "unavailable"},
    {"visitor_id": "visitor", "status": "authenticated", "saleor_user_id": "user"},
])
async def test_identity_states_and_previous_key(identity):
    check = verifier()
    check.keys["previous"] = check.keys["active"]
    result = await check.verify(
        signed(VECTOR["claims"] | {"identity": identity}, kid="previous"), method="POST",
        path=VECTOR["path"], body=VECTOR["body"].encode(),
    )
    assert result.context.identity.status.value == identity["status"]
    assert result.context.approvals == ()


async def test_expiry_boundary_and_bad_key():
    check = verifier()
    check.clock = lambda: 1800000065
    with pytest.raises(AppError) as error:
        await check.verify(VECTOR["authorization"], method="POST", path=VECTOR["path"],
                           body=VECTOR["body"].encode())
    assert error.value.http_status == 401
    check.clock = lambda: 1800000000
    check.keys["active"] = b"wrong-key" * 4
    with pytest.raises(AppError) as error:
        await check.verify(VECTOR["authorization"], method="POST", path=VECTOR["path"],
                           body=VECTOR["body"].encode())
    assert error.value.http_status == 401
