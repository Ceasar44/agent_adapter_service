"""Normalize tool data, not MCP protocol; return bounded business DTOs."""

import json
from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import JsonValue, ValidationError

from agent_adapter_service.core.exceptions import IntegrationError
from agent_adapter_service.rag.models import DocumentSource, KnowledgeAnswer, KnowledgeCollection
from agent_adapter_service.rag.tools import (
    GET_DOCUMENT_SUMMARY,
    LIST_COLLECTIONS,
    QUERY_KNOWLEDGE_HUB,
    SearchArguments,
    SummaryArguments,
)


class ToolCaller(Protocol):
    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        trace_id: str | None = None,
    ) -> Any: ...


def invalid_response() -> IntegrationError:
    return IntegrationError("Invalid RAG response", code="rag_invalid_response")


class RagClient:
    def __init__(self, mcp: ToolCaller, *, max_payload_bytes: int = 32768) -> None:
        if max_payload_bytes < 1024:
            raise ValueError("max_payload_bytes must be at least 1024")
        self.mcp = mcp
        self.max_payload_bytes = max_payload_bytes

    async def _call(self, name: str, arguments: dict, trace_id: str | None) -> Any:
        result = await self.mcp.call_tool(name, arguments, trace_id=trace_id)
        if getattr(result, "is_error", False):
            raise IntegrationError("RAG tool failed", code="rag_tool_error")
        payload = getattr(result, "structured_content", None)
        if payload is None:
            payload = getattr(result, "data", None)
        if payload is None:
            texts = [
                block.text
                for block in getattr(result, "content", [])
                if getattr(block, "type", None) == "text"
            ]
            if not texts:
                raise invalid_response()
            text = "\n".join(texts)
            try:
                payload = json.loads(text)
            except (ValueError, RecursionError):
                # Plain text answers are valid, collection lists must still be structured.
                payload = text
        if isinstance(payload, dict) and set(payload) == {"result"}:
            payload = payload["result"]
        return payload

    def _bounded(self, value: Any, depth: int = 0) -> Any:
        """Bound field sizes and nesting before constructing public models."""
        if depth > 8:
            return None
        if isinstance(value, str):
            return value[: self.max_payload_bytes]
        if isinstance(value, list):
            return [self._bounded(item, depth + 1) for item in value[:100]]
        if isinstance(value, dict):
            return {
                str(key)[:256]: self._bounded(item, depth + 1)
                for key, item in list(value.items())[:100]
            }
        return value

    @staticmethod
    def _size(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))

    def _answer(self, payload: Any) -> KnowledgeAnswer:
        try:
            original_size = self._size(payload)
            bounded = self._bounded(payload)
            was_bounded = bounded != payload
            payload = bounded
            if isinstance(payload, str):
                payload = {"answer": payload}
            if not isinstance(payload, dict):
                raise TypeError("Expected answer")
            payload = dict(payload)
            if "answer" not in payload and "summary" in payload:
                payload["answer"] = payload.pop("summary")
            sources = []
            for source in payload.get("sources", []):
                if not isinstance(source, dict):
                    raise TypeError("Invalid source")
                source = dict(source)
                if "document_id" not in source and "id" in source:
                    source["document_id"] = source.pop("id")
                # Preserve provider-specific citation fields in metadata.
                extras = {k: v for k, v in source.items() if k not in DocumentSource.model_fields}
                source["metadata"] = {**extras, **source.get("metadata", {})}
                sources.append(DocumentSource.model_validate(source))
            payload["sources"] = sources
            answer = KnowledgeAnswer.model_validate(payload)
            answer.truncated = (
                answer.truncated or was_bounded or original_size > self.max_payload_bytes
            )
            while self._size(answer.model_dump(mode="json")) > self.max_payload_bytes:
                answer.truncated = True
                if answer.metadata or any(source.metadata for source in answer.sources):
                    answer.metadata = {}
                    for source in answer.sources:
                        source.metadata = {}
                elif len(answer.answer) > 256:
                    answer.answer = answer.answer[: len(answer.answer) // 2]
                elif answer.sources:
                    answer.sources.pop()
                elif answer.collection:
                    answer.collection = None
                else:
                    answer.answer = answer.answer[: len(answer.answer) // 2]
            return answer
        except (ValueError, TypeError, RecursionError, ValidationError):
            raise invalid_response() from None

    async def search_knowledge(
        self,
        query: str,
        collections: list[str] | None = None,
        filters: dict[str, JsonValue] | None = None,
        *,
        trace_id: str | None = None,
    ) -> KnowledgeAnswer:
        args = SearchArguments(query=query, collections=collections, filters=filters)
        return self._answer(
            await self._call(
                QUERY_KNOWLEDGE_HUB,
                args.model_dump(exclude_none=True),
                trace_id,
            )
        )

    async def get_document_summary(
        self,
        document_id: str,
        collection: str | None = None,
        *,
        trace_id: str | None = None,
    ) -> KnowledgeAnswer:
        args = SummaryArguments(document_id=document_id, collection=collection)
        return self._answer(
            await self._call(
                GET_DOCUMENT_SUMMARY,
                args.model_dump(exclude_none=True),
                trace_id,
            )
        )

    async def list_collections(self, *, trace_id: str | None = None) -> list[KnowledgeCollection]:
        payload = await self._call(LIST_COLLECTIONS, {}, trace_id)
        if isinstance(payload, dict):
            payload = payload.get("collections")
        if not isinstance(payload, list):
            raise invalid_response()
        try:
            result = []
            for item in payload[:100]:
                if isinstance(item, str):
                    item = {"id": item, "name": item}
                collection = KnowledgeCollection.model_validate(self._bounded(item))
                candidate = [entry.model_dump(mode="json") for entry in [*result, collection]]
                if self._size(candidate) > self.max_payload_bytes:
                    break
                result.append(collection)
            return result
        except (ValueError, TypeError, RecursionError):
            raise invalid_response() from None
