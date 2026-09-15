import asyncio

import pytest

from agent_adapter_service.frontend.state.context_builder import FrontendContextBuilder
from agent_adapter_service.frontend.state.manager import FrontendStateManager
from agent_adapter_service.frontend.state.models import FrontendStateError, StateVersionConflict
from agent_adapter_service.frontend.state.reducer import FrontendStateReducer
from agent_adapter_service.persistence.contracts import StoreScope


def manager(store="s"):
    return FrontendStateManager(StoreScope(tenant_id="tenant", store_id=store))


async def test_snapshot_delta_clear_and_context():
    states = manager()
    first = await states.update("t", {"version": 1, "product": {"id": "p", "color": "black"}})
    second = await states.update(
        "t",
        {
            "base_version": 1,
            "version": 2,
            "changes": {"product": {"color": None, "size": "L"}},
        },
    )
    assert first.product.color == "black"
    assert second.product.id == "p" and second.product.color is None
    assert second.product.size == "L"
    assert second.updated_at >= first.updated_at
    context = FrontendContextBuilder().build(second)
    assert context["state"]["product"] == {"id": "p", "size": "L"}
    assert "user" not in context["state"] and "updated_at" not in context["state"]
    replaced = await states.update("t", {"version": 3})
    assert replaced.product.id is None


@pytest.mark.parametrize(
    "bad",
    [
        {"token": "secret"},
        {"product": {"dom": {"secret": "x"}}},
        {"user": {"email": "private@example.com"}},
        {"cart": {"item_count": -1}},
        {"cart": {"item_count": True}},
        {"route": {"pathname": "/?token=secret"}},
        {"checkout": {"payment_token": "secret"}},
        {"product": {"size": "x" * 201}},
        {"route": {"pathname": "//evil.example"}},
    ],
)
async def test_invalid_snapshot_is_atomic_and_errors_hide_input(bad):
    states = manager()
    await states.update("t", {"version": 1})
    with pytest.raises(FrontendStateError) as exc:
        await states.update("t", {"version": 2, **bad})
    assert "secret" not in str(exc.value)
    assert (await states.get("t")).version == 1


async def test_concurrent_revisions_and_isolation():
    a, b = manager(), manager("other")
    await a.update("t", {"version": 1})
    delta = {"base_version": 1, "version": 2, "changes": {"cart": {"item_count": 2}}}
    results = await asyncio.gather(
        a.update("t", delta), a.update("t", delta), return_exceptions=True
    )
    assert sum(isinstance(r, StateVersionConflict) for r in results) == 1
    assert await a.get("another") is None
    assert await b.get("t") is None
    with pytest.raises(StateVersionConflict):
        await a.update("missing", delta)
    await a.clear()
    assert await a.get("t") is None


@pytest.mark.parametrize(
    "changes", [{"version": {}}, {"dom": {}}, {"cart": None}, {"cart": {"secret": "x"}}]
)
async def test_reject_invalid_delta(changes):
    states = manager()
    await states.update("t", {"version": 1})
    with pytest.raises(FrontendStateError):
        await states.update("t", {"base_version": 1, "version": 2, "changes": changes})
    assert (await states.get("t")).version == 1


def test_size_and_version_limits():
    reducer = FrontendStateReducer(max_bytes=1000)
    with pytest.raises(FrontendStateError):
        reducer.apply_snapshot(None, {"version": 1, "dom": "汉" * 1000})
    with pytest.raises(StateVersionConflict):
        reducer.apply_snapshot(None, {"version": 3})
    with pytest.raises(FrontendStateError):
        reducer.apply_snapshot(None, {"version": True})
