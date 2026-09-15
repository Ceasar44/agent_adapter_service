import asyncio

from tests.agui.conftest import collect, run_input


async def test_binding_concurrent_resolution_returns_one_session(agui):
    await collect(agui.runner, run_input(), agui.trusted)
    identity = await agui.runner.identities.resolve(agui.trusted.identity)
    contexts = await asyncio.gather(
        *(
            agui.runner.conversations.resolve(thread_id="t1", run_id=f"r{i}", identity=identity)
            for i in range(5)
        )
    )
    assert len({context.binding.parlant_session_id for context in contexts}) == 1
    assert len(agui.harness.sessions.records) == 1
