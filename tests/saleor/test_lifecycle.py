from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from agent_adapter_service.app import lifespan
from agent_adapter_service.app.dependencies import get_product_service, get_saleor_client
from agent_adapter_service.app.factory import create_app
from agent_adapter_service.core.exceptions import AppError
from agent_adapter_service.core.settings import Settings
from agent_adapter_service.saleor.services.product_service import ProductService


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    return Settings(
        _env_file=None,
        saleor_enabled=True,
        saleor_api_url="https://saleor.test",
        saleor_service_token="service",
    )


def test_default_services_di_and_owned_close(settings):
    app = create_app(settings)
    with TestClient(app):
        resources = app.state.resources
        client = get_saleor_client(resources)
        assert isinstance(get_product_service(resources), ProductService)
        assert not client._http.is_closed
    assert client._http.is_closed
    assert not resources.services


def test_partial_registration_failure_closes_http(settings, monkeypatch):
    clients = []

    def fail(client):
        clients.append(client)
        raise RuntimeError("registration failed")

    monkeypatch.setattr(lifespan, "create_services", fail)
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="registration failed"), TestClient(app):
        pass
    assert clients[0]._http.is_closed
    assert app.state.resources is None


def test_later_resource_failure_closes_saleor(settings):
    clients = []

    @asynccontextmanager
    async def fail(settings, resources):
        clients.append(resources.saleor_client)
        raise RuntimeError("later resource failed")
        yield

    app = create_app(settings, resource_factories={"rag_client": fail})
    with pytest.raises(RuntimeError, match="later resource failed"), TestClient(app):
        pass
    assert clients[0]._http.is_closed
    assert app.state.resources is None


def test_disabled_and_explicit_override(settings):
    disabled = settings.model_copy(update={"saleor_enabled": False})
    with TestClient(create_app(disabled)) as http:
        resources = http.app.state.resources
        with pytest.raises(AppError):
            get_saleor_client(resources)
    marker = object()

    @asynccontextmanager
    async def fake(settings, resources):
        yield marker

    app = create_app(settings, resource_factories={"saleor_client": fake})
    with TestClient(app):
        assert app.state.resources.saleor_client is marker
        assert "product" not in app.state.resources.services
