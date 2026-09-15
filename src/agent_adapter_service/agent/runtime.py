"""Own the serving task; expose resources only after configuration and readiness succeed."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Protocol

from agent_adapter_service.agent.bootstrap import AgentBootstrapper, validate_registry
from agent_adapter_service.agent.config.loader import AgentConfigLoader
from agent_adapter_service.agent.contracts import (
    AgentRecord,
    ConfigurationGateway,
    StaticToolRegistry,
    ToolRegistry,
)
from agent_adapter_service.core.exceptions import AppError, IntegrationError
from agent_adapter_service.core.settings import Settings


class RuntimeHost(Protocol):
    async def run(self, configure: Callable[[ConfigurationGateway], Awaitable[None]]) -> None: ...
    async def wait_ready(self) -> None: ...


class AgentRuntime:
    def __init__(
        self,
        settings: Settings,
        *,
        loader: AgentConfigLoader | None = None,
        tool_registry: ToolRegistry | None = None,
        host_factory: Callable[[Settings], RuntimeHost] | None = None,
    ) -> None:
        self.settings = settings
        self.loader = loader or AgentConfigLoader(settings.parlant_config_dir)
        self.tool_registry = tool_registry or StaticToolRegistry()
        self._host_factory = host_factory
        self.host: RuntimeHost | None = None
        self._task: asyncio.Task | None = None
        self._agent: AgentRecord | None = None
        self._ready = False
        self._lock = asyncio.Lock()
        self.bootstrapper: AgentBootstrapper | None = None

    async def start(self) -> None:
        async with self._lock:
            if self._task is not None:
                self.get_agent()
                return
            config = await self.loader.aload()
            validate_registry(config, self.tool_registry)
            if config.agent.id != self.settings.default_agent_id:
                from agent_adapter_service.agent.config.loader import ConfigError

                raise ConfigError("agent.yaml [id] must match DEFAULT_AGENT_ID")
            if self._host_factory is None:
                from agent_adapter_service.agent.parlant.host import ParlantHost

                factory = ParlantHost
            else:
                factory = self._host_factory
            readiness = None
            configured = asyncio.Event()

            async def configure(gateway: ConfigurationGateway) -> None:
                self.bootstrapper = AgentBootstrapper(self.loader, gateway, self.tool_registry)
                self._agent = await self.bootstrapper.bootstrap(config)
                configured.set()

            try:
                host = factory(self.settings)
                self.host = host

                async def serve() -> None:
                    try:
                        await host.run(configure)
                    except (AppError, asyncio.CancelledError):
                        raise
                    except BaseException:  # noqa: BLE001 - contain SDK sys.exit in the task
                        # SDK startup may call sys.exit; contain it inside the serving task.
                        raise IntegrationError("Parlant runtime failed") from None

                self._task = asyncio.create_task(serve(), name="parlant-runtime")

                async def wait_ready() -> None:
                    await configured.wait()
                    await host.wait_ready()

                readiness = asyncio.create_task(wait_ready())
                done, _ = await asyncio.wait(
                    (self._task, readiness),
                    timeout=self.settings.parlant_startup_timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._task in done:
                    await self._task
                    raise IntegrationError("Parlant stopped during startup")
                if readiness not in done:
                    raise IntegrationError("Parlant startup timed out")
                await readiness
                self._ready = True
            except BaseException as exc:
                await self._stop()
                if isinstance(exc, (AppError, asyncio.CancelledError)):
                    raise
                raise IntegrationError("Parlant startup failed") from None
            finally:
                if readiness is not None:
                    readiness.cancel()
                    with suppress(asyncio.CancelledError):
                        await readiness

    def get_agent(self) -> AgentRecord:
        if not self._ready or self._agent is None or self._task is None or self._task.done():
            raise IntegrationError("Parlant runtime is not ready", http_status=503)
        return self._agent

    @property
    def agent_id(self) -> str:
        return self.get_agent().id

    async def _stop(self) -> None:
        task, self._task = self._task, None
        self._agent = None
        self._ready = False
        self.bootstrapper = None
        self.host = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()
