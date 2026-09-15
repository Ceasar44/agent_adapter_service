from dataclasses import replace

from agent_adapter_service.customer_identity.models import StorefrontIdentity
from tests.agui.conftest import run_input
from tests.support.flows import storefront_run


async def test_login_keeps_session_and_other_visitor_cannot_reuse_thread(agui):
    first = await storefront_run(agui, run_input())
    repository = agui.runner.conversations.bindings.repository
    before = await repository.find_by_thread("t1")
    trusted = replace(
        agui.trusted,
        identity=StorefrontIdentity(
            visitor_id="v1",
            status="authenticated",
            saleor_user_id="user-1",
        ),
    )
    second = await storefront_run(agui, run_input("m2", run_id="r2"), trusted=trusted)
    after = await repository.find_by_thread("t1")
    assert first[-1]["type"] == second[-1]["type"] == "RUN_FINISHED"
    assert before.parlant_session_id == after.parlant_session_id
    identity = await agui.harness.repository.find_by_saleor_user("user-1")
    assert after.parlant_customer_id == identity.parlant_customer_id
    assert (
        agui.harness.sessions.records[after.parlant_session_id].customer_id
        == identity.parlant_customer_id
    )
    foreign = replace(
        agui.trusted, identity=StorefrontIdentity(visitor_id="v2", status="anonymous")
    )
    denied = await storefront_run(agui, run_input("m3", run_id="r3"), trusted=foreign)
    assert denied[-1]["type"] == "RUN_ERROR"
    own = await storefront_run(agui, run_input("m4", run_id="r4", thread_id="t2"), trusted=foreign)
    assert own[-1]["type"] == "RUN_FINISHED"
    other = await repository.find_by_thread("t2")
    assert other.parlant_session_id != after.parlant_session_id
    assert other.parlant_customer_id != after.parlant_customer_id
