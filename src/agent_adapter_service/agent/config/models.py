"""Configuration contracts, independent of the Parlant SDK."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[Text, StringConstraints(pattern=r"^[a-zA-Z0-9_-]+$")]


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class AgentConfig(ConfigModel):
    id: Identifier
    name: Text
    description: Text
    language: Text = "en"
    behavior: tuple[Text, ...] = ()
    max_engine_iterations: int = Field(default=3, ge=1, le=20)


class GuidelineConfig(ConfigModel):
    id: Identifier
    condition: Text
    action: Text
    tools: tuple[Identifier, ...] = ()
    priority: int = 0


class JourneyStateConfig(ConfigModel):
    id: Identifier
    action: Text
    tools: tuple[Identifier, ...] = ()


class JourneyTransitionConfig(ConfigModel):
    source: Identifier
    target: Identifier
    condition: Text | None = None


class JourneyConfig(ConfigModel):
    id: Identifier
    title: Text
    description: Text
    trigger: Text
    states: tuple[JourneyStateConfig, ...] = Field(min_length=1)
    transitions: tuple[JourneyTransitionConfig, ...] = Field(min_length=1)


class GlossaryConfig(ConfigModel):
    id: Identifier
    name: Text
    description: Text
    synonyms: tuple[Text, ...] = ()


class ToolConfig(ConfigModel):
    name: Identifier
    enabled: bool = True
    risk: Literal["low", "medium", "high"] = "low"
    confirmation: Literal["auto", "confirm", "deny"] = "auto"


class CustomerServiceConfig(ConfigModel):
    agent: AgentConfig
    guidelines: tuple[GuidelineConfig, ...] = ()
    journeys: tuple[JourneyConfig, ...] = ()
    glossary: tuple[GlossaryConfig, ...] = ()
    tools: tuple[ToolConfig, ...] = ()
