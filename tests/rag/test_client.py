import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from agent_adapter_service.core.exceptions import IntegrationError
from agent_adapter_service.rag.client import RagClient


def caller(payload=None, text=None):
    return SimpleNamespace(
        call_tool=AsyncMock(
            return_value=SimpleNamespace(
                structured_content=payload,
                data=None,
                is_error=False,
                content=[] if text is None else [SimpleNamespace(type="text", text=text)],
            )
        )
    )


async def test_search_preserves_sources_and_forwards_arguments():
    mcp = caller(
        {
            "answer": "Yes",
            "sources": [
                {
                    "document_id": "d1",
                    "title": "Coat",
                    "url": "https://example.com/coat",
                    "chunk_refs": ["c1"],
                    "page": 2,
                    "metadata": {"language": "zh"},
                }
            ],
            "score": 0.8,
            "collection": "products",
            "metadata": {"provider": "test"},
        }
    )
    answer = await RagClient(mcp).search_knowledge(
        "winter",
        ["products"],
        {"language": "zh"},
        trace_id="run-1",
    )
    mcp.call_tool.assert_awaited_once_with(
        "query_knowledge_hub",
        {
            "query": "winter",
            "collections": ["products"],
            "filters": {"language": "zh"},
        },
        trace_id="run-1",
    )
    assert answer.sources[0].metadata == {"page": 2, "language": "zh"}
    assert answer.sources[0].chunk_refs == ["c1"]
    assert answer.score == 0.8
    assert not answer.truncated


@pytest.mark.parametrize(
    "payload,text",
    [
        ({"result": {"answer": "hello"}}, None),
        (None, '{"answer":"hello"}'),
        (None, "hello"),
    ],
)
async def test_response_envelopes(payload, text):
    answer = await RagClient(caller(payload, text)).search_knowledge("query")
    assert answer.answer == "hello"


async def test_summary_and_collections():
    mcp = caller({"summary": "A document", "sources": [{"id": "d1"}]})
    answer = await RagClient(mcp).get_document_summary("d1", "manuals")
    assert answer.answer == "A document"
    assert answer.sources[0].document_id == "d1"
    mcp.call_tool.assert_awaited_once_with(
        "get_document_summary",
        {
            "document_id": "d1",
            "collection": "manuals",
        },
        trace_id=None,
    )
    result = await RagClient(caller({"result": ["manuals", {"id": "products"}]})).list_collections()
    assert [item.id for item in result] == ["manuals", "products"]


async def test_utf8_payload_budget_preserves_small_citation():
    answer = await RagClient(
        caller(
            {
                "answer": "汉字" * 20000,
                "sources": [{"document_id": "d1", "chunk_refs": ["c1"]}],
                "metadata": {"huge": "x" * 10000},
            }
        ),
        max_payload_bytes=1024,
    ).search_knowledge("query")
    assert len(json.dumps(answer.model_dump(), ensure_ascii=False).encode()) <= 1024
    assert answer.sources[0].document_id == "d1"
    assert answer.truncated


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": "secret"},
        {"answer": 1},
        {"answer": "ok", "sources": ["bad"]},
        {"answer": "ok", "score": float("nan")},
    ],
)
async def test_invalid_remote_result_is_sanitized(payload):
    with pytest.raises(IntegrationError) as exc:
        await RagClient(caller(payload)).search_knowledge("query")
    assert exc.value.code == "rag_invalid_response"
    assert "secret" not in str(exc.value)


async def test_bad_inputs_do_not_reach_remote():
    mcp = caller()
    with pytest.raises(ValidationError):
        await RagClient(mcp).search_knowledge(" ")
    mcp.call_tool.assert_not_awaited()


async def test_collection_payload_is_bounded():
    result = await RagClient(
        caller([{"id": str(i)} for i in range(1000)]), max_payload_bytes=1024
    ).list_collections()
    assert len(json.dumps([item.model_dump() for item in result]).encode()) <= 1024
