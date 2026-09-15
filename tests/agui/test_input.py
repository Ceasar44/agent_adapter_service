import pytest
from ag_ui.core import RunAgentInput

from agent_adapter_service.agui.input_adapter import AguiInputAdapter, extract_storefront_identity
from agent_adapter_service.core.exceptions import AppError, AuthorizationError
from tests.agui.conftest import run_input


def test_trust_boundary():
    with pytest.raises(AuthorizationError):
        extract_storefront_identity(
            {"visitor_id": "v", "status": "authenticated", "saleor_user_id": "admin"}
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("threadId", "../secret"),
        ("runId", ""),
        ("forwardedProps", {"identity": {"saleor_user_id": "admin"}}),
        ("state", [1]),
        ("messages", [{"id": "x", "role": "user", "content": "  "}]),
        ("messages", [{"id": "x", "role": "user", "content": "a"}] * 2),
    ],
)
def test_invalid_input(field, value):
    data = run_input().model_dump(by_alias=True)
    data[field] = value
    with pytest.raises(AppError):
        AguiInputAdapter().adapt(RunAgentInput.model_validate(data))


def test_state_size_and_request_size():
    with pytest.raises(AppError):
        AguiInputAdapter(max_state_bytes=20).adapt(run_input(state={"large": "x" * 30}))
    with pytest.raises(AppError):
        AguiInputAdapter(max_request_bytes=20).adapt(run_input())
