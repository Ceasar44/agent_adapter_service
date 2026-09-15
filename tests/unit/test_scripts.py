import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from graphql import build_schema, graphql_sync, get_introspection_query

from agent_adapter_service.agent.config.generator import (
    CommandLlmProvider,
    generate_candidate,
    validate_generated_config,
)
from agent_adapter_service.agent.config.loader import AgentConfigLoader, ConfigError
from agent_adapter_service.core.settings import Settings
from scripts._common import catalog_registry, tool_metadata
from scripts.sync_saleor_schema import sync_schema
from scripts.verify_tools import check_names, verify

APPROVED = Path("configs/agents/customer_service")


@pytest.fixture
def metadata():
    return tool_metadata(catalog_registry())


async def test_generated_candidate_validates_and_never_overwrites(tmp_path, metadata):
    baseline = AgentConfigLoader(APPROVED).load()
    original = {p.name: p.read_bytes() for p in APPROVED.glob("*.yaml")}
    provider = AsyncMock()
    provider.generate.return_value = baseline.model_dump_json()
    output = tmp_path / "candidate"
    result = await generate_candidate(
        provider,
        business_description="Shopping",
        model="chosen-model",
        tools=metadata,
        approved_directory=APPROVED,
        output_directory=output,
    )
    assert AgentConfigLoader(output).load() == result == baseline
    assert provider.generate.call_args.args[0]["model"] == "chosen-model"
    assert {p.name: p.read_bytes() for p in APPROVED.glob("*.yaml")} == original
    for forbidden in [APPROVED, APPROVED / "candidate", APPROVED.parent, output]:
        with pytest.raises(ConfigError):
            await generate_candidate(
                provider,
                business_description="x",
                model="m",
                tools=metadata,
                approved_directory=APPROVED,
                output_directory=forbidden,
            )
    assert provider.generate.await_count == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data["tools"].append({"name": "update_product"}),
        lambda data: data["tools"][-1].update(confirmation="auto", risk="low"),
        lambda data: data["agent"].update(id="foreign"),
        lambda data: data["journeys"][0]["transitions"][0].update(target="missing"),
        lambda data: data["tools"][0].update(confirmation="confirm"),
    ],
)
def test_generated_unsafe_or_invalid_config_is_rejected(metadata, change):
    data = AgentConfigLoader(APPROVED).load().model_dump(mode="json")
    change(data)
    with pytest.raises(ConfigError):
        validate_generated_config(json.dumps(data), metadata, agent_id="customer_service")


async def test_invalid_generation_leaves_no_candidate(tmp_path, metadata):
    provider = AsyncMock()
    provider.generate.return_value = "not JSON secret"
    output = tmp_path / "candidate"
    with pytest.raises(ConfigError):
        await generate_candidate(
            provider,
            business_description="x",
            model="m",
            tools=metadata,
            approved_directory=APPROVED,
            output_directory=output,
        )
    assert not output.exists()


async def test_llm_command_json_contract_and_failure(tmp_path):
    script = tmp_path / "provider.py"
    script.write_text(
        "import json,sys\nx=json.load(sys.stdin)\nprint(json.dumps(x))", encoding="utf-8"
    )
    provider = CommandLlmProvider([sys.executable, str(script)])
    request = {"model": "chosen", "tools": tool_metadata(catalog_registry())}
    assert json.loads(await provider.generate(request)) == request
    script.write_text("raise SystemExit(2)", encoding="utf-8")
    with pytest.raises(ConfigError):
        await provider.generate({})
    script.write_text("import time\ntime.sleep(30)", encoding="utf-8")
    with pytest.raises(TimeoutError):
        await CommandLlmProvider([sys.executable, str(script)], timeout=0.05).generate({})


async def test_schema_sync_validates_before_replacing_file(tmp_path):
    schema = build_schema("type Query { product: String }")
    data = graphql_sync(schema, get_introspection_query()).data
    payload = {"data": data}
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    output = tmp_path / "schema.graphql"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await sync_schema("https://saleor.test/graphql/", "test-secret", output, client=client)
        before = output.read_bytes()
        assert "product: String" in output.read_text()
        assert calls[0].headers["Authorization"] == "Bearer test-secret"
        payload = {"errors": [{"message": "secret"}]}
        with pytest.raises(ValueError):
            await sync_schema("https://saleor.test/graphql/", None, output, client=client)
        assert output.read_bytes() == before
        assert len(list(tmp_path.iterdir())) == 1


async def test_catalog_check_and_missing_dependency():
    result = await verify(Settings(_env_file=None))
    assert len(result["catalogs"]["parlant"]) == 14
    assert len(result["catalogs"]["admin"]) == 18
    assert result["checks"]["remote_dependencies"] == "not_checked"
    with pytest.raises(ValueError, match="require Saleor"):
        await verify(Settings(_env_file=None, saleor_enabled=False), online=True)
    with pytest.raises(ValueError, match="conflicts"):
        check_names({"customer": ["same"], "admin": ["same"]})


@pytest.mark.parametrize(
    "script", ["bootstrap_agent", "generate_agent_config", "sync_saleor_schema", "verify_tools"]
)
def test_script_help_and_failure_exit_codes(script):
    help_result = subprocess.run(
        [sys.executable, f"scripts/{script}.py", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert help_result.returncode == 0 and "usage:" in help_result.stdout
    invalid = subprocess.run(
        [sys.executable, f"scripts/{script}.py", "--invalid-option"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert invalid.returncode != 0
