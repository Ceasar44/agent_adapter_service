from pydantic import JsonValue

from agent_adapter_service.frontend.state.models import FrontendState


class FrontendContextBuilder:
    def build(self, state: FrontendState) -> dict[str, JsonValue]:
        """Allowlisted UX hints, never authentication or authoritative Saleor data."""
        # Revalidate even model_construct/model_copy inputs before exposing context.
        clean = FrontendState.model_validate(state.model_dump())
        return {
            "source": "frontend_ui",
            "version": clean.version,
            "state": clean.model_dump(
                mode="json",
                exclude={"version", "updated_at", "user"},
                exclude_none=True,
                exclude_defaults=True,
            ),
        }
