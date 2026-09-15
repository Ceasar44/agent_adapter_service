"""Provider selection and SDK environment lifecycle without external API calls."""

import asyncio
import os
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from agent_adapter_service.agent.parlant.host import ParlantHost
from agent_adapter_service.core.exceptions import IntegrationError
from agent_adapter_service.core.settings import Settings

sdk = pytest.importorskip("parlant.sdk")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.mark.parametrize("provider", ["openai", "qwen"])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_provider_lifecycle(monkeypatch, provider, outcome):
    key_name = "DASHSCOPE_API_KEY" if provider == "qwen" else "OPENAI_API_KEY"
    settings = Settings(
        _env_file=None,
        nlp_provider=provider,
        **{key_name.lower(): "test-key"},
        qwen_model="qwen-max",
        qwen_region="international",
        qwen_base_url="https://example.com/compatible-mode/v1",
    )
    entered = asyncio.Event()
    captured = {}

    class Server:
        def __init__(self, **kwargs):
            self.factory = kwargs["nlp_service"]
            self.container = defaultdict(MagicMock)

        async def __aenter__(self):
            assert os.environ[key_name] == "test-key"
            captured["service"] = self.factory(self.container)
            entered.set()
            if outcome == "failure":
                raise RuntimeError("startup failed")
            if outcome == "cancel":
                await asyncio.Event().wait()
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(sdk, "Server", Server)
    host = ParlantHost(settings)
    task = asyncio.create_task(host.run(AsyncMock()))
    await asyncio.wait_for(entered.wait(), timeout=5)
    if outcome == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif outcome == "failure":
        with pytest.raises(RuntimeError, match="startup failed"):
            await task
    else:
        await task
    assert type(captured["service"]).__name__ == (
        "QwenService" if provider == "qwen" else "OpenAIService"
    )
    if provider == "qwen":
        assert captured["service"].model_name == "qwen-max"
    assert key_name not in os.environ
    assert "QWEN_MODEL" not in os.environ
    assert "QWEN_REGION" not in os.environ
    assert "QWEN_BASE_URL" not in os.environ
    assert not ParlantHost._running


async def test_conflicting_environment_and_existing_key(monkeypatch):
    monkeypatch.setattr(sdk, "Server", MagicMock())
    settings = Settings(_env_file=None, nlp_provider="qwen", dashscope_api_key="configured-key")
    host = ParlantHost(settings)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "different-key")
    with pytest.raises(IntegrationError, match="conflict"):
        await host.run(AsyncMock())
    assert os.environ["DASHSCOPE_API_KEY"] == "different-key"
    assert "QWEN_REGION" not in os.environ
    assert not ParlantHost._running

    monkeypatch.setenv("DASHSCOPE_API_KEY", "configured-key")
    host.server.__aenter__ = AsyncMock(side_effect=RuntimeError("startup failed"))
    with pytest.raises(RuntimeError):
        await host.run(AsyncMock())
    assert os.environ["DASHSCOPE_API_KEY"] == "configured-key"
    assert "QWEN_MODEL" not in os.environ
    assert not ParlantHost._running


@pytest.mark.parametrize("model", ["qwen-plus", "qwen-max", "qwen2.5-72b-instruct"])
@pytest.mark.parametrize(
    "region, endpoint",
    [
        ("domestic", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("international", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    ],
)
async def test_qwen_chat_and_embedding_clients(monkeypatch, model, region, endpoint):
    from parlant.adapters.nlp import qwen_service

    class Answer(BaseModel):
        answer: str

    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-test-key")
    monkeypatch.setenv("QWEN_MODEL", model)
    monkeypatch.setenv("QWEN_REGION", region)
    client = MagicMock()
    monkeypatch.setattr(qwen_service, "AsyncClient", client)
    monkeypatch.setattr(qwen_service, "QwenEstimatingTokenizer", MagicMock())
    service = qwen_service.QwenService(logger=MagicMock(), tracer=MagicMock(), meter=MagicMock())
    generator = await service.get_schematic_generator(Answer)
    embedder = await service.get_embedder()
    assert generator.model_name == model
    assert embedder.model_name == "text-embedding-v4"
    assert client.call_count == 2
    for call in client.call_args_list:
        assert call.kwargs == {"base_url": endpoint, "api_key": "qwen-test-key"}
    monkeypatch.setenv("QWEN_BASE_URL", "https://example.com/compatible-mode/v1")
    await service.get_embedder()
    assert client.call_args.kwargs["base_url"] == "https://example.com/compatible-mode/v1"
