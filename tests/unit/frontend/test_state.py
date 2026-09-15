import pytest

from agent_adapter_service.frontend.state.models import FrontendStateError, StateVersionConflict
from agent_adapter_service.frontend.state.reducer import FrontendStateReducer


def test_unknown_fields_and_stale_delta_cannot_change_snapshot():
    reducer = FrontendStateReducer()
    initial = reducer.apply_snapshot(None, {"version": 1, "product": {"id": "p1"}})
    with pytest.raises(FrontendStateError):
        reducer.apply_delta(
            initial, {"base_version": 1, "version": 2, "changes": {"auth_token": "private"}}
        )
    changed = reducer.apply_delta(
        initial, {"base_version": 1, "version": 2, "changes": {"product": {"variant_id": "v1"}}}
    )
    assert initial.product.variant_id is None
    assert changed.product.id == "p1" and changed.product.variant_id == "v1"
    with pytest.raises(StateVersionConflict):
        reducer.apply_delta(changed, {"base_version": 1, "version": 3, "changes": {}})
