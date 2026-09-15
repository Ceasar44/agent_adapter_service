from contextlib import asynccontextmanager
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from agent_adapter_service.app.dependencies import get_identity_service, get_resources, get_service
from agent_adapter_service.app.factory import create_app
from agent_adapter_service.app.lifespan import RESOURCE_ORDER
from agent_adapter_service.core.exceptions import AuthorizationError
from agent_adapter_service.core.settings import Settings


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return Settings(_env_file=None, app_env="test")


def factories(events, fail_at=None, fail_close=None):
    def make(name):
        @asynccontextmanager
        async def resource(settings, resources):
            events.append("open:" + name)
            if name == fail_at:
                raise RuntimeError("startup failed")
            if name == "saleor_client":
                assert resources.database is not None
            try:
                yield object()
            finally:
                events.append("close:" + name)
                if name == fail_close:
                    raise RuntimeError("shutdown failed")

        return resource

    return {name: make(name) for name, _ in RESOURCE_ORDER}


def test_health_routes_and_lifecycle(settings):
    events = []
    app = create_app(settings, resource_factories=factories(events))
    with TestClient(app) as client:
        resources = app.state.resources
        assert resources.ready
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}
        for path in ("/mcp",):
            response = client.post(path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "not_enabled"
            assert response.headers["x-request-id"] == response.json()["request_id"]
        assert client.post("/agui").status_code == 401
        assert client.post("/api/agent").status_code == 401
    names = [name for name, _ in RESOURCE_ORDER]
    assert events == ["open:" + n for n in names] + ["close:" + n for n in reversed(names)]
    assert app.state.resources is None
    assert resources.database is None
    assert not resources.ready


@pytest.mark.parametrize("failed", [name for name, _ in RESOURCE_ORDER])
def test_partial_startup_cleanup(settings, failed):
    events = []
    app = create_app(settings, resource_factories=factories(events, fail_at=failed))
    with pytest.raises(RuntimeError, match="startup failed"), TestClient(app):
        pass
    names = [name for name, _ in RESOURCE_ORDER]
    index = names.index(failed)
    assert events == (
        ["open:" + n for n in names[: index + 1]] + ["close:" + n for n in reversed(names[:index])]
    )
    assert app.state.resources is None


def test_cleanup_continues_after_close_error(settings):
    events = []
    app = create_app(settings, resource_factories=factories(events, fail_close="rag_client"))
    with pytest.raises(RuntimeError, match="shutdown failed"), TestClient(app):
        pass
    assert events[-1] == "close:database"
    assert app.state.resources is None


def test_saleor_default_factory(settings):
    settings = Settings(
        _env_file=None,
        saleor_enabled=True,
        saleor_api_url="http://localhost:8001/graphql/",
        saleor_service_token="test",
    )
    app = create_app(settings)
    with TestClient(app):
        resources = app.state.resources
        assert resources.saleor_client is not None
        assert {"channel", "product", "customer", "order", "inventory", "promotion"} <= (
            resources.services.keys()
        )
    assert resources.services == {}
    assert resources.saleor_client is None


def test_dependency_injection_and_errors(settings, caplog):
    app = create_app(settings)

    @app.get("/identity")
    async def identity(service: Annotated[object, Depends(get_identity_service)]):
        return {"service": service}

    @app.get("/denied")
    async def denied():
        raise AuthorizationError("Forbidden", details={"scope": "products:write"})

    @app.get("/unexpected")
    async def unexpected():
        raise RuntimeError("private-token")

    @app.get("/validate")
    async def validate(count: int):
        return count

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/identity").status_code == 503
        app.state.resources.services["identity"] = "fake"
        assert client.get("/identity").json() == {"service": "fake"}
        assert get_service(app.state.resources, "identity", str) == "fake"
        with pytest.raises(TypeError):
            get_service(app.state.resources, "identity", int)
        denied = client.get("/denied")
        assert denied.status_code == 403
        assert denied.json()["error"]["details"] == {"scope": "products:write"}
        for path, code in (
            ("/unexpected", 500),
            ("/validate?count=private-token", 422),
            ("/missing", 404),
        ):
            response = client.get(path)
            assert response.status_code == code
            assert "private-token" not in response.text
            assert response.headers["x-request-id"] == response.json()["request_id"]
        app.dependency_overrides[get_identity_service] = lambda: "override"
        assert client.get("/identity").json() == {"service": "override"}
    assert "private-token" not in caplog.text


def test_unstarted_app_not_ready(settings):
    app = create_app(settings)

    @app.get("/resources")
    async def resources(value: Annotated[object, Depends(get_resources)]):
        return {}

    client = TestClient(app)
    assert client.get("/health/ready").status_code == 503
    assert client.get("/resources").status_code == 503
