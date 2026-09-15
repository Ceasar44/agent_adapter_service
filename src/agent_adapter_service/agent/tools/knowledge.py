import parlant.sdk as p

from agent_adapter_service.agent.tools.common import ToolDependencies, required, tool_boundary


def create_tools(deps: ToolDependencies):
    @p.tool
    @tool_boundary(deps)
    async def search_knowledge(
        context: p.ToolContext,
        query: str,
        collections: list[str] | None = None,
    ) -> p.ToolResult:
        """Search store knowledge and return an answer with source citations in metadata."""
        active = await deps.authorize(context, "search_knowledge")
        answer = await required(deps.rag).search_knowledge(
            query, collections=collections, trace_id=active.trace_id
        )
        return p.ToolResult(
            data={"answer": answer.answer, "truncated": answer.truncated},
            metadata=answer.model_dump(mode="json", exclude={"answer"}, exclude_none=True),
        )

    return (search_knowledge,)
