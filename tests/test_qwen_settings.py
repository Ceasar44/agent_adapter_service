import pytest
from pydantic import ValidationError
from pydantic_settings.exceptions import SettingsError

from agent_adapter_service.core.settings import Settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.mark.parametrize(
    "values, expected",
    [
        ({"parlant_enabled": True, "nlp_provider": "qwen"}, "DASHSCOPE_API_KEY"),
        ({"nlp_provider": "unknown"}, "nlp_provider"),
        ({"qwen_model": "unsupported"}, "qwen_model"),
        ({"qwen_region": "unknown"}, "qwen_region"),
        ({"qwen_base_url": "not-a-url"}, "qwen_base_url"),
    ],
)
def test_invalid_configuration(values, expected):
    with pytest.raises(ValidationError, match=expected):
        Settings(**values)


def test_dotenv_and_secret_handling(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "PARLANT_ENABLED=true\nNLP_PROVIDER=qwen\nDASHSCOPE_API_KEY=private-qwen-key\n"
        "QWEN_MODEL=qwen-max\nQWEN_REGION=international\n"
        "QWEN_BASE_URL=https://example.com/compatible-mode/v1\n",
        encoding="utf-8",
    )
    settings = Settings()
    assert settings.openai_api_key is None
    assert settings.qwen_model == "qwen-max"
    assert settings.qwen_region == "international"
    assert "private-qwen-key" not in repr(settings)
    assert "private-qwen-key" not in settings.model_dump_json()
    monkeypatch.setenv("QWEN_MODEL", "qwen-plus")
    assert Settings().qwen_model == "qwen-plus"
    with pytest.raises(ValidationError, match="DASHSCOPE_API_KEY"):
        Settings(dashscope_api_key=" ")
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(nlp_provider="openai")


def test_yaml_configuration(tmp_path):
    path = tmp_path / "app.yaml"
    path.write_text(
        "nlp_provider: qwen\nqwen_model: qwen-max\nqwen_region: domestic\n", encoding="utf-8"
    )
    settings = Settings(app_config_file=path, parlant_enabled=True, dashscope_api_key="test-key")
    assert settings.nlp_provider == "qwen"
    assert settings.qwen_model == "qwen-max"
    path.write_text("dashscope_api_key: secret\n", encoding="utf-8")
    with pytest.raises(SettingsError):
        Settings(app_config_file=path)
