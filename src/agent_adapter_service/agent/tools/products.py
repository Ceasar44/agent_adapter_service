import parlant.sdk as p

from agent_adapter_service.agent.tools.common import (
    ToolDependencies,
    compact,
    page_size,
    required,
    tool_boundary,
)
from agent_adapter_service.core.exceptions import AppError


def create_tools(deps: ToolDependencies):
    @p.tool
    @tool_boundary(deps)
    async def search_products(
        context: p.ToolContext,
        query: str,
        first: int = 10,
        after: str | None = None,
    ) -> p.ToolResult:
        """Search published products in the current storefront channel; paginate with after."""
        active = await deps.authorize(context, "search_products")
        result = await required(deps.products).search_products(
            query, await deps.channel(active), first=page_size(first), after=after
        )
        return p.ToolResult(compact(result))

    @p.tool
    @tool_boundary(deps)
    async def get_product(
        context: p.ToolContext,
        product_id: str | None = None,
        slug: str | None = None,
    ) -> p.ToolResult:
        """Read a product by ID or slug, or use the currently viewed product if both omitted."""
        active = await deps.authorize(context, "get_product")
        if product_id is None and slug is None:
            state = await deps.state(active)
            product_id = state.product.id if state else None
        result = await required(deps.products).get_product(
            await deps.channel(active), id=product_id, slug=slug
        )
        return p.ToolResult(compact(result))

    @p.tool
    @tool_boundary(deps)
    async def get_product_variants(
        context: p.ToolContext,
        product_id: str | None = None,
        first: int = 10,
        after: str | None = None,
    ) -> p.ToolResult:
        """Read variants with prices, availability and attributes for an explicit or viewed product."""
        active = await deps.authorize(context, "get_product_variants")
        if product_id is None:
            state = await deps.state(active)
            product_id = state.product.id if state else None
        if not product_id:
            raise AppError("Product is required")
        result = await required(deps.products).get_variants(
            product_id,
            await deps.channel(active),
            first=page_size(first),
            after=after,
            attribute_limit=20,
        )
        return p.ToolResult(compact(result))

    return (search_products, get_product, get_product_variants)
