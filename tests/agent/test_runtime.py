import asyncio

import pytest

from agent_adapter_service.agent.config.loader import ConfigError
from agent_adapter_service.agent.contracts import RegisteredTool, StaticToolRegistry
from agent_adapter_service.agent.parlant.agent import ParlantAgentGateway
from agent_adapter_service.agent.runtime import AgentRuntime
from agent_adapter_service.core.exceptions import IntegrationError
from agent_adapter_service.core.settings import Settings


class FakeHost:
    def __init__(self, gateway, *, fail=False, never_ready=False):
        self.gateway = gateway
        self.ready = asyncio.Event()
        self.closed = False
        self.fail = fail
        self.never_ready = never_ready

    async def run(self, configure):
        try:
            await configure(self.gateway)
            if self.fail:
                raise RuntimeError("private-key")
            if not self.never_ready:
                self.ready.set()
            await asyncio.Event().wait()
        finally:
            self.closed = True

    async def wait_ready(self):
        await self.ready.wait()


def make_runtime(loader, sdk_server, **host_kwargs):
    gateway = ParlantAgentGateway(sdk_server, sdk_server.sdk, sdk_server.store)
    host = FakeHost(gateway, **host_kwargs)
    runtime = AgentRuntime(
        Settings(_env_file=None, parlant_startup_timeout_seconds=0.05),
        loader=loader,
        tool_registry=StaticToolRegistry({"search_products": RegisteredTool(object())}),
        host_factory=lambda settings: host,
    )
    return runtime, host


async def test_start_stop_idempotency(loader, sdk_server):
    runtime, host = make_runtime(loader, sdk_server)
    with pytest.raises(IntegrationError):
        runtime.get_agent()
    await asyncio.gather(runtime.start(), runtime.start())
    assert runtime.agent_id == "customer_service"
    assert runtime.get_agent().name == "Assistant"
    await asyncio.gather(runtime.stop(), runtime.stop())
    assert host.closed and runtime.host is None
    with pytest.raises(IntegrationError):
        runtime.get_agent()


@pytest.mark.parametrize(
    "kwargs, message", [({"fail": True}, "runtime failed"), ({"never_ready": True}, "timed out")]
)
async def test_start_failure_and_timeout_cleanup(loader, sdk_server, kwargs, message):
    runtime, host = make_runtime(loader, sdk_server, **kwargs)
    with pytest.raises(IntegrationError, match=message):
        await runtime.start()
    assert host.closed and runtime.host is None
    assert runtime._task is None


async def test_invalid_config_does_not_create_host(loader, sdk_server):
    runtime, host = make_runtime(loader, sdk_server)
    runtime.tool_registry = StaticToolRegistry()
    with pytest.raises(ConfigError):
        await runtime.start()
    assert runtime.host is None and not host.closed


async def test_cancel_start(loader, sdk_server):
    runtime, host = make_runtime(loader, sdk_server, never_ready=True)
    start = asyncio.create_task(runtime.start())
    while runtime.bootstrapper is None:
        await asyncio.sleep(0)
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start
    assert host.closed and runtime.host is None


async def test_dead_host(loader, sdk_server):
    runtime, _host = make_runtime(loader, sdk_server)
    await runtime.start()
    runtime._task.cancel()
    await asyncio.sleep(0)
    with pytest.raises(IntegrationError):
        runtime.get_agent()
    await runtime.stop()
