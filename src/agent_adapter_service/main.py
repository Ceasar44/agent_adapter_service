"""ASGI entry point."""

from agent_adapter_service.app.factory import create_app

app = create_app()
