import pytest
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from agent_adapter_service.core.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_configuration_precedence(monkeypatch, tmp_path):
    config = tmp_path / "app.yaml"
    config.write_text("request_timeout_seconds: 10\napp_name: yaml-app\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "APP_CONFIG_FILE=app.yaml\nREQUEST_TIMEOUT_SECONDS=20\n", encoding="utf-8"
    )
    assert Settings().request_timeout_seconds == 20
    monkeypatch.setenv("REQUEST_TIMEOUT_SECONDS", "40")
    assert Settings().request_timeout_seconds == 40
    assert Settings(request_timeout_seconds=50).request_timeout_seconds == 50
    assert Settings().app_name == "yaml-app"
    assert Settings(_env_file=None, app_config_file=config).app_name == "yaml-app"


@pytest.mark.parametrize(
    "values, expected",
    [
        ({"saleor_enabled": True}, "SALEOR_API_URL, SALEOR_SERVICE_TOKEN"),
        ({"database_enabled": True}, "DATABASE_URL"),
        ({"rag_enabled": True}, "RAG_MCP_URL"),
        ({"parlant_enabled": True}, "OPENAI_API_KEY"),
        ({"request_timeout_seconds": 0}, "request_timeout_seconds"),
        ({"request_timeout_seconds": float("inf")}, "request_timeout_seconds"),
        ({"frontend_state_max_bytes": -1}, "frontend_state_max_bytes"),
        ({"saleor_api_url": "not-a-url"}, "saleor_api_url"),
        ({"database_url": "postgresql://localhost/db"}, "DATABASE_URL"),
        ({"app_env": "typo"}, "app_env"),
    ],
)
def test_invalid_configuration(values, expected):
    with pytest.raises(ValidationError, match=expected):
        Settings(**values)


def test_secrets_and_enabled_configuration():
    settings = Settings(
        saleor_enabled=True,
        saleor_api_url="https://example.com/graphql/",
        saleor_service_token="private-value",
        database_enabled=True,
        database_url="sqlite+aiosqlite:///:memory:",
        rag_enabled=True,
        rag_mcp_url="https://example.com/mcp",
        parlant_enabled=True,
        openai_api_key="private-value",
    )
    assert "private-value" not in repr(settings)
    assert "private-value" not in settings.model_dump_json()
    with pytest.raises(ValidationError, match="SALEOR_SERVICE_TOKEN"):
        Settings(
            saleor_enabled=True, saleor_api_url="https://example.com", saleor_service_token=" "
        )


@pytest.mark.parametrize(
    "content", ["[one, two]", "bad: [", "unknown: 1", "openai_api_key: secret"]
)
def test_reject_invalid_or_sensitive_yaml(tmp_path, content):
    path = tmp_path / "bad.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SettingsError):
        Settings(app_config_file=path)


def test_missing_explicit_yaml_and_cache(monkeypatch):
    with pytest.raises(SettingsError, match="APP_CONFIG_FILE"):
        Settings(app_config_file="missing.yaml")
    first = get_settings()
    monkeypatch.setenv("APP_NAME", "changed")
    assert get_settings() is first
    get_settings.cache_clear()
    assert get_settings().app_name == "changed"
