"""Priority: constructor > environment > .env > YAML > defaults."""

import base64
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError

YAML_FIELDS = {
    "agui_enabled", "agui_tenant_id", "agui_store_id", "bff_issuer", "bff_audience",
    "bff_max_age_seconds", "bff_clock_skew_seconds",
    "app_name",
    "default_agent_id",
    "request_timeout_seconds",
    "frontend_state_max_bytes",
    "database_enabled",
    "database_create_schema",
    "saleor_enabled",
    "rag_enabled",
    "rag_config_file",
    "parlant_enabled",
    "nlp_provider",
    "qwen_model",
    "qwen_region",
    "qwen_base_url",
    "admin_mcp_enabled",
    "admin_mcp_config_file",
    "parlant_config_dir",
    "parlant_port",
    "parlant_tool_service_port",
    "parlant_startup_timeout_seconds",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        hide_input_in_errors=True,
    )
    app_config_file: Path | None = None
    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = Field(default="agent_adapter_service", min_length=1)
    default_agent_id: str = Field(default="customer_service", min_length=1)
    request_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    frontend_state_max_bytes: int = Field(default=65536, gt=0)
    database_enabled: bool = False
    agui_enabled: bool = False
    agui_tenant_id: str | None = None
    agui_store_id: str | None = None
    bff_issuer: str | None = Field(default=None, min_length=1, max_length=255)
    bff_audience: str | None = Field(default=None, min_length=1, max_length=255)
    bff_keys: dict[str, SecretStr] = Field(default_factory=dict)
    bff_max_age_seconds: int = Field(default=60, ge=1, le=60)
    bff_clock_skew_seconds: int = Field(default=5, ge=0, le=5)
    database_create_schema: bool = False
    saleor_enabled: bool = False
    rag_enabled: bool = False
    rag_config_file: Path = Path("configs/mcp/rag.yaml")
    parlant_enabled: bool = False
    admin_mcp_enabled: bool = False
    admin_mcp_config_file: Path = Path("configs/mcp/admin_server.yaml")
    parlant_config_dir: Path = Path("configs/agents/customer_service")
    parlant_port: int = Field(default=8800, ge=1, le=65535)
    parlant_tool_service_port: int = Field(default=8818, ge=1, le=65535)
    parlant_startup_timeout_seconds: float = Field(default=300, gt=0, allow_inf_nan=False)
    database_url: SecretStr | None = None
    saleor_api_url: HttpUrl | None = None
    saleor_service_token: SecretStr | None = None
    rag_mcp_url: HttpUrl | None = None
    rag_mcp_token: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    nlp_provider: Literal["openai", "qwen"] = "openai"
    dashscope_api_key: SecretStr | None = None
    # These are the model names supported by the pinned Parlant Qwen adapter.
    qwen_model: Literal["qwen-plus", "qwen-max", "qwen2.5-72b-instruct"] = "qwen-plus"
    qwen_region: Literal["domestic", "international"] = "domestic"
    qwen_base_url: HttpUrl | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[Any, ...]:
        def app_yaml() -> dict[str, Any]:
            selected = next(
                (
                    source()["app_config_file"]
                    for source in (init_settings, env_settings, dotenv_settings)
                    if source().get("app_config_file") is not None
                ),
                None,
            )
            path = Path(selected) if selected is not None else Path("configs/app.yaml")
            if selected is None and not path.exists():
                return {}
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise SettingsError("Cannot read APP_CONFIG_FILE as valid YAML") from exc
            if data is None:
                return {}
            if not isinstance(data, dict) or set(data) - YAML_FIELDS:
                raise SettingsError("App YAML must contain only supported non-sensitive settings")
            return data

        return init_settings, env_settings, dotenv_settings, app_yaml, file_secret_settings

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return value
        parts = urlsplit(value.get_secret_value())
        if parts.scheme not in {"sqlite+aiosqlite", "postgresql+asyncpg"} or not parts.path:
            raise ValueError("DATABASE_URL must use sqlite+aiosqlite or postgresql+asyncpg")
        if parts.scheme == "postgresql+asyncpg" and not parts.hostname:
            raise ValueError("DATABASE_URL requires a PostgreSQL host")
        return value

    @model_validator(mode="after")
    def validate_enabled_integrations(self) -> Self:
        for kid, secret in self.bff_keys.items():
            try:
                valid = re.fullmatch(r"[A-Za-z0-9_-]{1,64}", kid)
                key = base64.b64decode(secret.get_secret_value(), validate=True)
                if not valid or len(key) < 32:
                    raise ValueError()
            except ValueError:
                raise ValueError("BFF_KEYS requires valid key IDs and base64 keys of 32+ bytes") from None
        if self.agui_enabled:
            from agent_adapter_service.persistence.contracts import StoreScope

            if not (self.database_enabled and self.parlant_enabled and self.bff_issuer
                    and self.bff_audience and self.bff_keys):
                raise ValueError("AG-UI requires database, Parlant, BFF issuer/audience/keys")
            StoreScope(tenant_id=self.agui_tenant_id, store_id=self.agui_store_id)
        if self.parlant_port == self.parlant_tool_service_port:
            raise ValueError("Parlant server and tool service ports must differ")
        if self.app_env == "production" and self.database_create_schema:
            raise ValueError("DATABASE_CREATE_SCHEMA is only allowed in development/test")
        required = {
            "database": ("database_url",),
            "saleor": ("saleor_api_url", "saleor_service_token"),
            "rag": ("rag_mcp_url",),
            "parlant": (
                "dashscope_api_key" if self.nlp_provider == "qwen" else "openai_api_key",
            ),
        }
        missing = []
        for integration, fields in required.items():
            if not getattr(self, f"{integration}_enabled"):
                continue
            for name in fields:
                value = getattr(self, name)
                if isinstance(value, SecretStr):
                    value = value.get_secret_value().strip()
                if not value:
                    missing.append(name.upper())
        if missing:
            raise ValueError("Enabled integrations require: " + ", ".join(missing))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
