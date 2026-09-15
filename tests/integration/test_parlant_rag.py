from fastmcp import Client, FastMCP

from agent_adapter_service.mcp.clients.rag import RagMcpClient, RagMcpConfig
from agent_adapter_service.rag.client import RagClient
from tests.agui.conftest import run_input
from tests.support.flows import storefront_run


async def test_http_run_to_real_knowledge_tool_and_local_fastmcp(flow):
    server = FastMCP("knowledge-integration")
    queries = []

    @server.tool
    def query_knowledge_hub(query: str) -> dict:
        queries.append(query)
        return {
            "answer": "Returns within 30 days.",
            "sources": [{"document_id": "returns-policy", "title": "Returns"}],
        }

    async with RagMcpClient(RagMcpConfig(), client=Client(server)) as mcp:
        flow.deps.rag = RagClient(mcp)

        async def process(session, agent, *, run_id):
            result = await flow.tools["search_knowledge"](
                await flow.context(session, agent),
                query="return policy",
            )
            assert result.metadata["sources"][0]["document_id"] == "returns-policy"
            flow.agui.events.add(session, data={"message": result.data["answer"]})

        flow.agui.processing.process.side_effect = process
        events = await storefront_run(flow.agui, run_input())
    assert queries == ["return policy"]
    assert events[-1]["type"] == "RUN_FINISHED"
    assert "30 days" in "".join(e.get("delta", "") for e in events)
