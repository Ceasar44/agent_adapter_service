import pytest

from agent_adapter_service.agent.config.loader import ConfigError, load_yaml
from agent_adapter_service.agent.config.models import CustomerServiceConfig
from agent_adapter_service.agent.config.validator import AgentConfigValidator


async def test_load_five_files_and_validate(loader, config):
    assert loader.load() == config
    assert await loader.aload() == config
    AgentConfigValidator().validate(config, {"search_products"})


@pytest.mark.parametrize(
    "content", ["bad: [", "id: one\nid: two", "!!python/object/apply:os.system ['bad']"]
)
def test_yaml_errors_have_file_and_line(tmp_path, content):
    path = tmp_path / "agent.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=r"agent.yaml:\d+:\d+"):
        load_yaml(path)


def test_model_errors_hide_values_and_locate_file(loader):
    (loader.directory / "agent.yaml").write_text("id: secret@invalid\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="agent.id") as error:
        loader.load()
    assert "secret@invalid" not in str(error.value)


def test_missing_file(loader):
    (loader.directory / "tools.yaml").unlink()
    with pytest.raises(ConfigError, match="tools.yaml"):
        loader.load()


@pytest.mark.parametrize(
    "change, location",
    [
        (lambda d: d["guidelines"][0].update(tools=["unknown"]), "guidelines.yaml \\[search\\]"),
        (lambda d: d["tools"][0].update(enabled=False), "guidelines.yaml"),
        (lambda d: d["tools"][0].update(risk="high"), "tools.yaml"),
        (lambda d: d["tools"][0].update(name="place_order"), "tools.yaml"),
        (lambda d: d["tools"][0].update(confirmation="deny"), "tools.yaml"),
        (lambda d: d["guidelines"].append(d["guidelines"][0]), "duplicate ID"),
        (
            lambda d: d["journeys"][0]["transitions"][0].update(target="missing"),
            "invalid transition",
        ),
        (
            lambda d: d["journeys"][0]["states"].append({"id": "lost", "action": "Wait"}),
            "reachable",
        ),
        (lambda d: d["journeys"][0]["states"][0].update(id="START"), "reserved"),
        (lambda d: d["journeys"][0]["transitions"].pop(0), "reachable"),
    ],
)
def test_semantic_errors(config, change, location):
    data = config.model_dump(mode="json")
    change(data)
    with pytest.raises(ConfigError, match=location):
        AgentConfigValidator().validate(
            CustomerServiceConfig.model_validate(data), {"search_products"}
        )


def test_unknown_registry_tool(config):
    with pytest.raises(ConfigError, match="not registered"):
        AgentConfigValidator().validate(config, set())
