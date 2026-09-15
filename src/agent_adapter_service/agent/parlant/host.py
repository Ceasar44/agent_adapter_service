"""Parlant 3.3.2 embedding. All SDK imports and lifecycle peculiarities stay here."""

import os
from collections.abc import Awaitable, Callable
from typing import Any

from agent_adapter_service.agent.contracts import ConfigurationGateway
from agent_adapter_service.agent.parlant.agent import ParlantAgentGateway
from agent_adapter_service.agent.parlant.common import GatewayContext
from agent_adapter_service.agent.parlant.customers import ParlantCustomerGateway
from agent_adapter_service.agent.parlant.events import ParlantEventGateway
from agent_adapter_service.agent.parlant.frontend_events import ParlantFrontendEventGateway
from agent_adapter_service.agent.parlant.messages import ParlantMessageGateway
from agent_adapter_service.agent.parlant.processing import ParlantProcessingGateway
from agent_adapter_service.agent.parlant.sessions import ParlantSessionGateway
from agent_adapter_service.core.exceptions import IntegrationError
from agent_adapter_service.core.settings import Settings


class ParlantHost:
    _running = False

    def __init__(self, settings: Settings) -> None:
        try:
            from parlant import sdk

            if settings.nlp_provider == "qwen":
                from parlant.adapters.nlp.qwen_service import QwenService as ProviderService
            else:
                from parlant.adapters.nlp.openai_service import OpenAIService as ProviderService
        except ImportError:
            raise IntegrationError(
                "Install agent-adapter-service[integrations] to enable Parlant"
            ) from None
        self.sdk = sdk
        self.settings = settings

        def nlp_service(container: Any) -> Any:
            from parlant.core.loggers import Logger
            from parlant.core.meter import Meter
            from parlant.core.tracer import Tracer

            return ProviderService(
                logger=container[Logger], tracer=container[Tracer], meter=container[Meter]
            )

        self.server = sdk.Server(
            host="127.0.0.1",
            port=settings.parlant_port,
            tool_service_port=settings.parlant_tool_service_port,
            nlp_service=nlp_service,
            customer_store="local",
            session_store="local",
        )

    async def run(self, configure: Callable[[ConfigurationGateway], Awaitable[None]]) -> None:
        from parlant.core.agents import AgentStore
        from parlant.core.app_modules.customers import CustomerMetadataUpdateParams
        from parlant.core.app_modules.sessions import Moderation
        from parlant.core.application import Application
        from parlant.core.async_utils import Timeout
        from parlant.core.sessions import EventKind, EventSource
        from parlant.core.engines.types import Context, Engine
        from parlant.core.emissions import EventEmitterFactory
        from parlant.core.tracer import Tracer

        if ParlantHost._running:
            raise IntegrationError("Only one embedded Parlant host is supported per process")
        if self.settings.nlp_provider == "qwen":
            key_name, secret = "DASHSCOPE_API_KEY", self.settings.dashscope_api_key
            environment = {
                "QWEN_MODEL": self.settings.qwen_model,
                "QWEN_REGION": self.settings.qwen_region,
            }
            if self.settings.qwen_base_url is not None:
                environment["QWEN_BASE_URL"] = str(self.settings.qwen_base_url)
        else:
            key_name, secret = "OPENAI_API_KEY", self.settings.openai_api_key
            environment = {}
        if secret is None or not secret.get_secret_value().strip():
            raise IntegrationError(f"{key_name} is required for the Parlant host")
        environment[key_name] = secret.get_secret_value()
        previous = {name: os.environ.get(name) for name in environment}
        if any(
            previous[name] is not None and previous[name] != value
            for name, value in environment.items()
        ):
            raise IntegrationError(
                "Parlant provider settings conflict with the process environment"
            )
        ParlantHost._running = True
        # This SDK reads keys from os.environ when constructing generators, including at run time.
        os.environ.update(environment)
        try:
            async with self.server as server:
                context = GatewayContext(
                    server.container[Application], EventSource, EventKind, Moderation, Timeout
                )
                self.customers = ParlantCustomerGateway(context, CustomerMetadataUpdateParams)
                self.sessions = ParlantSessionGateway(context)
                self.messages = ParlantMessageGateway(context)
                self.events = ParlantEventGateway(context)
                self.frontend_events = ParlantFrontendEventGateway(context)
                self.processing = ParlantProcessingGateway(
                    server.container[Engine],
                    server.container[EventEmitterFactory],
                    server.container[Tracer],
                    Context,
                )
                await configure(ParlantAgentGateway(server, self.sdk, server.container[AgentStore]))
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            ParlantHost._running = False
        # Server.__aexit__ runs the serving loop until cancelled.

    async def wait_ready(self) -> None:
        await self.server.ready.wait()
