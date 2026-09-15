"""Remote tool contracts. Adjust here when deploying a different RAG server schema."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue

QUERY_KNOWLEDGE_HUB = "query_knowledge_hub"
LIST_COLLECTIONS = "list_collections"
GET_DOCUMENT_SUMMARY = "get_document_summary"


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=16000)
    collections: list[str] | None = None
    filters: dict[str, JsonValue] | None = None


class SummaryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    document_id: str = Field(min_length=1, max_length=1024)
    collection: str | None = None
