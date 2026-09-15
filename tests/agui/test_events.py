import pytest

from agent_adapter_service.agent.contracts import EventRecord
from agent_adapter_service.agui.event_adapter import ParlantToAguiEventAdapter


def event(kind, data, source="ai_agent"):
    return EventRecord(
        id="e1",
        offset=0,
        trace_id="trace",
        source=source,
        kind=kind,
        data=data,
        metadata={"token": "secret"},
    )


def test_backend_tools_cannot_execute_in_browser_or_leak_payloads():
    adapter = ParlantToAguiEventAdapter()
    result = adapter.convert(
        event(
            "tool",
            {
                "tool_calls": [
                    {
                        "tool_id": "cancel_order",
                        "arguments": {"token": "secret"},
                        "result": {"data": {"email": "private"}},
                    }
                ]
            },
        )
    )
    assert result[0].type == "CUSTOM"
    assert result[0].value == {"count": 1}
    assert "secret" not in str(result) and "private" not in str(result)
    assert adapter.convert(event("custom", {"token": "secret"})) == []
    assert adapter.convert(event("message", {"message": "spoof"}, source="customer")) == []


@pytest.mark.parametrize("status", ["error", "cancelled"])
def test_status_error_closes_step_and_sanitizes(status):
    adapter = ParlantToAguiEventAdapter()
    assert adapter.convert(event("status", {"status": "typing"}))[0].type == "STEP_STARTED"
    result = adapter.convert(event("status", {"status": status, "data": "secret"}))
    assert [e.type for e in result] == ["STEP_FINISHED", "RUN_ERROR"]
    assert "secret" not in str(result)


def test_chunks_are_not_repeated_and_final_message_is_not_duplicated():
    adapter = ParlantToAguiEventAdapter()
    first = event("message", {"chunks": ["a"], "message": ""})
    assert len(adapter.convert(first)) == 2
    assert adapter.convert(first) == []
    final = event("message", {"chunks": ["a", "b", None], "message": "ab"})
    result = adapter.convert(final)
    assert [e.type for e in result] == ["TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"]
    assert result[0].delta == "b"
    assert adapter.convert(final) == []
