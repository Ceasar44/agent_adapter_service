"""Load five YAML files without evaluating YAML objects or exposing their contents."""

import asyncio
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agent_adapter_service.agent.config.models import CustomerServiceConfig
from agent_adapter_service.core.exceptions import AppError


class ConfigError(AppError):
    code = "agent_config_error"


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise yaml.MarkedYAMLError(
                problem="Duplicate or non-string key", problem_mark=key_node.start_mark
            )
        result[key] = loader.construct_object(value_node)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_yaml(path: Path) -> Any:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f":{mark.line + 1}:{mark.column + 1}" if mark else ""
        raise ConfigError(f"Invalid YAML: {path}{location}") from None
    except (OSError, UnicodeError):
        raise ConfigError(f"Cannot read configuration: {path}") from None


class AgentConfigLoader:
    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)

    def load(self) -> CustomerServiceConfig:
        data = {
            name: load_yaml(self.directory / f"{name}.yaml")
            for name in CustomerServiceConfig.model_fields
        }
        try:
            return CustomerServiceConfig.model_validate(data)
        except ValidationError as exc:
            locations = [".".join(map(str, error["loc"])) for error in exc.errors()]
            raise ConfigError(
                f"Invalid agent configuration in {self.directory}: " + ", ".join(locations),
                details={"locations": locations},
            ) from None

    async def aload(self) -> CustomerServiceConfig:
        return await asyncio.to_thread(self.load)
