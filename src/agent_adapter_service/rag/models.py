from pydantic import BaseModel, ConfigDict, Field, JsonValue


class RagModel(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)


class DocumentSource(RagModel):
    document_id: str | None = None
    title: str | None = None
    url: str | None = None
    chunk_refs: list[str] = Field(default_factory=list)
    score: float | None = Field(default=None, allow_inf_nan=False)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class KnowledgeAnswer(RagModel):
    answer: str
    sources: list[DocumentSource] = Field(default_factory=list)
    collection: str | None = None
    score: float | None = Field(default=None, allow_inf_nan=False)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    truncated: bool = False


class KnowledgeCollection(RagModel):
    id: str
    name: str | None = None
    description: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
