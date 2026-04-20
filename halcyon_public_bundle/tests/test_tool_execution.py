"""End-to-end governed tool execution: Proposal → Approval → Execution → Evidence."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from halcyon_core.api.server import create_app
from halcyon_core.config import RuntimeConfig
from halcyon_core.events import SQLiteEventLedger
from halcyon_core.governance.abac import approval_for
from halcyon_core.governance.invariants import default_invariant
from halcyon_core.governance.mutation_context import mutation_context
from halcyon_core.model.client import LocalModelClient
from halcyon_core.tools.executor import ToolExecutor
from halcyon_core.tools.registry import ToolRegistry
from halcyon_core.tools.types import MutationClass, ToolCall, ToolResultStatus, ToolSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fake_client(content: str):
    return LocalModelClient(
        "http://local.test/v1",
        "fake",
        transport=lambda url, payload: {"choices": [{"message": {"content": content}}]},
    )


def tool_proposal_reply(tool_name: str = "echo", message: str = "hello world") -> str:
    return json.dumps({
        "reply": "I will echo that for you.",
        "memory_proposals": [],
        "tool_proposals": [{
            "action_type": tool_name,
            "target": tool_name,
            "payload": {"message": message},
            "mutation_class": "external",
            "reason": "user requested echo",
        }],
    })


# ---------------------------------------------------------------------------
# Unit: executor executes approved callable
# ---------------------------------------------------------------------------

def test_approved_tool_with_callable_is_executed():
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "Echo", MutationClass.EXTERNAL, commit_threshold="reversible"))
    registry.register_callable("echo", lambda message="", **_: {"echoed": message})
    executor = ToolExecutor(registry, invariant=default_invariant())

    call = ToolCall("echo", payload={"message": "ping"})
    action = executor.action_for_call(call)
    approval = approval_for(action, approved_by="brad", approval_basis="test")

    with mutation_context(actor="test", operation="tool_executor_unit_test"):
        result = executor.evaluate(call, approval=approval)

    assert result.status == ToolResultStatus.EXECUTED
    assert result.reason_code == "executed"
    assert result.output == {"echoed": "ping"}
    assert result.gate_decision.status == "approve"


def test_approved_callable_without_mutation_context_is_refused():
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "Echo", MutationClass.EXTERNAL, commit_threshold="reversible"))
    registry.register_callable("echo", lambda message="", **_: {"echoed": message})
    executor = ToolExecutor(registry, invariant=default_invariant())

    call = ToolCall("echo", payload={"message": "ping"})
    action = executor.action_for_call(call)
    approval = approval_for(action, approved_by="brad", approval_basis="test")

    result = executor.evaluate(call, approval=approval)

    assert result.status == ToolResultStatus.REFUSED
    assert result.reason_code == "mutation_context_required"
    assert result.output == {}


def test_tool_without_approval_is_refused():
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "Echo", MutationClass.EXTERNAL, commit_threshold="reversible"))
    registry.register_callable("echo", lambda message="", **_: {"echoed": message})
    executor = ToolExecutor(registry, invariant=default_invariant())

    call = ToolCall("echo", payload={"message": "ping"})
    result = executor.evaluate(call)  # no approval

    assert result.status == ToolResultStatus.REFUSED
    assert result.reason_code == "abac_denied_by_default"
    assert result.gate_decision.status == "refuse"


def test_approval_digest_mismatch_halts_execution():
    """Hard stop: approval issued for a different action cannot execute this one."""
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "Echo", MutationClass.EXTERNAL, commit_threshold="reversible"))
    registry.register_callable("echo", lambda message="", **_: {"echoed": message})
    executor = ToolExecutor(registry, invariant=default_invariant())

    call = ToolCall("echo", payload={"message": "ping"})
    different_call = ToolCall("echo", payload={"message": "different"})
    wrong_action = executor.action_for_call(different_call)
    approval = approval_for(wrong_action, approved_by="brad", approval_basis="test")

    result = executor.evaluate(call, approval=approval)

    assert result.status == ToolResultStatus.REFUSED
    assert result.reason_code == "approval_digest_mismatch"
    assert result.gate_decision.status == "halt"


# ---------------------------------------------------------------------------
# API: full closed loop
# ---------------------------------------------------------------------------

def test_api_tool_proposal_refused_by_default(tmp_path):
    """POST /chat with a tool proposal → proposal is refused, evidence in turn."""
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(config, model_client=fake_client(tool_proposal_reply()))
    client = TestClient(app)

    response = client.post("/chat", json={"message": "echo hello world"})
    data = response.json()

    assert response.status_code == 200
    assert len(data["tool_results"]) == 1
    assert data["tool_results"][0]["status"] == "refused"
    assert data["tool_results"][0]["reason_code"] == "abac_denied_by_default"


def test_api_tool_proposals_list_shows_pending(tmp_path):
    """GET /tools/proposals returns refused tool calls awaiting approval."""
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(config, model_client=fake_client(tool_proposal_reply()))
    client = TestClient(app)

    client.post("/chat", json={"message": "echo hello"})
    proposals = client.get("/tools/proposals").json()["proposals"]

    assert len(proposals) == 1
    assert proposals[0]["tool_name"] == "echo"
    assert proposals[0]["status"] == "refused"
    assert proposals[0]["reason_code"] == "abac_denied_by_default"
    assert proposals[0]["call_digest"].startswith("sha384:")


def test_api_tool_proposal_approve_executes_and_stores_evidence(tmp_path):
    """POST /tools/proposals/{call_digest}/approve → executed, evidence event written."""
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(config, model_client=fake_client(tool_proposal_reply(message="ping")))
    client = TestClient(app)

    # Step 1: chat creates the proposal (refused by default)
    turn = client.post("/chat", json={"message": "echo ping"}).json()
    call_digest = turn["tool_results"][0]["call_digest"]

    # Step 2: approve it
    approval = client.post(
        f"/tools/proposals/{call_digest}/approve",
        json={"approved_by": "brad", "approval_basis": "user_direct_consent"},
    )
    data = approval.json()

    assert approval.status_code == 200
    assert data["status"] == "executed"
    assert data["call_digest"] == call_digest
    assert data["tool_name"] == "echo"
    assert data["approved_by"] == "brad"
    assert data["output"] == {"echoed": "ping"}
    assert data["gate_decision"]["status"] == "approve"


def test_api_tool_approval_event_precedes_execution_and_links_digest(tmp_path):
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(config, model_client=fake_client(tool_proposal_reply(message="ordered")))
    client = TestClient(app)

    turn = client.post("/chat", json={"message": "echo ordered"}).json()
    call_digest = turn["tool_results"][0]["call_digest"]

    response = client.post(
        f"/tools/proposals/{call_digest}/approve",
        json={"approved_by": "brad", "approval_basis": "user_direct_consent"},
    )

    assert response.status_code == 200

    ledger = SQLiteEventLedger(config.events_database_path())
    try:
        proposal_event = next(
            event
            for event in ledger.iter_events_by_name("tool.proposal.evaluated")
            if event.payload["tool_result"]["call_digest"] == call_digest
        )
        binding_evaluated_event = next(
            event
            for event in ledger.iter_events_by_name("tool.intent_binding.evaluated")
            if event.payload["call_digest"] == call_digest
        )
        approval_event = next(
            event
            for event in ledger.iter_events_by_name("tool.approval.granted")
            if event.payload["call_digest"] == call_digest
        )
        execution_event = next(
            event
            for event in ledger.iter_events_by_name("tool.execution.completed")
            if event.payload["call_digest"] == call_digest
        )
    finally:
        ledger.close()

    assert proposal_event.index < binding_evaluated_event.index < approval_event.index < execution_event.index
    assert binding_evaluated_event.parent_index == proposal_event.index
    assert approval_event.parent_index == binding_evaluated_event.index
    assert execution_event.parent_index == approval_event.index
    assert approval_event.payload["proposal_event_index"] == proposal_event.index
    assert execution_event.payload["proposal_event_index"] == proposal_event.index
    assert execution_event.payload["approval_digest"] == approval_event.payload["approval_digest"]
    assert execution_event.payload["action_digest"] == approval_event.payload["action_digest"]


def test_api_tool_proposal_approve_removes_from_pending(tmp_path):
    """After approval, the proposal no longer appears in /tools/proposals."""
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(config, model_client=fake_client(tool_proposal_reply()))
    client = TestClient(app)

    turn = client.post("/chat", json={"message": "echo"}).json()
    call_digest = turn["tool_results"][0]["call_digest"]

    assert len(client.get("/tools/proposals").json()["proposals"]) == 1

    client.post(
        f"/tools/proposals/{call_digest}/approve",
        json={"approved_by": "brad", "approval_basis": "test"},
    )

    assert len(client.get("/tools/proposals").json()["proposals"]) == 0



def test_api_tool_proposal_approval_requires_bound_intent(tmp_path):
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(config, model_client=fake_client(tool_proposal_reply(message="invented")))
    client = TestClient(app)

    turn = client.post("/chat", json={"message": "please help me with this greeting"}).json()
    call_digest = turn["tool_results"][0]["call_digest"]
    proposals = client.get("/tools/proposals").json()["proposals"]

    assert proposals[0]["intent_binding"]["status"] == "unbound"

    approval = client.post(
        f"/tools/proposals/{call_digest}/approve",
        json={"approved_by": "brad", "approval_basis": "user_direct_consent"},
    )

    assert approval.status_code == 403
    detail = approval.json()["detail"]
    assert detail["reason_code"] == "tool_intent_unbound"
    assert detail["intent_binding"]["status"] == "unbound"


def test_api_tool_proposal_intent_override_is_recorded_before_approval(tmp_path):
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(config, model_client=fake_client(tool_proposal_reply(message="override")))
    client = TestClient(app)

    turn = client.post("/chat", json={"message": "please help me with this greeting"}).json()
    call_digest = turn["tool_results"][0]["call_digest"]

    approval = client.post(
        f"/tools/proposals/{call_digest}/approve",
        json={"approved_by": "brad", "approval_basis": "intent_override_accepted"},
    )

    assert approval.status_code == 200
    assert approval.json()["status"] == "executed"

    ledger = SQLiteEventLedger(config.events_database_path())
    try:
        override_event = next(
            event
            for event in ledger.iter_events_by_name("tool.intent_override.accepted")
            if event.payload["call_digest"] == call_digest
        )
        binding_evaluated_event = next(
            event
            for event in ledger.iter_events_by_name("tool.intent_binding.evaluated")
            if event.payload["call_digest"] == call_digest
        )
        approval_event = next(
            event
            for event in ledger.iter_events_by_name("tool.approval.granted")
            if event.payload["call_digest"] == call_digest
        )
        execution_event = next(
            event
            for event in ledger.iter_events_by_name("tool.execution.completed")
            if event.payload["call_digest"] == call_digest
        )
    finally:
        ledger.close()

    assert override_event.index < binding_evaluated_event.index < approval_event.index < execution_event.index
    assert binding_evaluated_event.parent_index == override_event.index
    assert approval_event.parent_index == binding_evaluated_event.index
    assert approval_event.payload["intent_binding"]["status"] == "unbound"
    assert override_event.index in approval_event.payload["source_events"]

def test_api_tool_proposal_approve_unknown_digest_returns_404(tmp_path):
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(config, model_client=fake_client("hi"))
    client = TestClient(app)

    response = client.post(
        "/tools/proposals/sha384:nonexistent/approve",
        json={"approved_by": "brad", "approval_basis": "test"},
    )

    assert response.status_code == 404


def test_api_tool_execution_route_has_no_direct_execute_path(tmp_path):
    """No /tools/execute endpoint — execution only happens through approval."""
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(config, model_client=fake_client("hi"))
    client = TestClient(app)

    paths = client.get("/openapi.json").json()["paths"]

    assert "/tools/execute" not in paths
    assert all("execute" not in path for path in paths)
    assert "/tools/proposals" in paths


def test_intent_binding_evaluated_event_emitted_on_bound_pass(tmp_path):
    """tool.intent_binding.evaluated is emitted on the pass path, not just on override."""
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(config, model_client=fake_client(tool_proposal_reply(message="ping")))
    client = TestClient(app)

    turn = client.post("/chat", json={"message": "echo ping"}).json()
    call_digest = turn["tool_results"][0]["call_digest"]

    client.post(
        f"/tools/proposals/{call_digest}/approve",
        json={"approved_by": "brad", "approval_basis": "user_direct_consent"},
    )

    ledger = SQLiteEventLedger(config.events_database_path())
    try:
        binding_event = next(
            event
            for event in ledger.iter_events_by_name("tool.intent_binding.evaluated")
            if event.payload["call_digest"] == call_digest
        )
    finally:
        ledger.close()

    assert binding_event.payload["binding_status"] == "bound"
    assert binding_event.payload["override_applied"] is False
    assert binding_event.payload["schema_version"] == "mutation-event-v1"


def test_payload_surface_check_flags_mutation_verb_not_in_intent(tmp_path):
    """Payload containing a mutation verb absent from user intent → needs_confirmation."""
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))

    def malicious_payload_reply():
        return json.dumps({
            "reply": "Sure.",
            "memory_proposals": [],
            "tool_proposals": [{
                "action_type": "echo",
                "target": "echo",
                "payload": {"message": "delete everything"},
                "mutation_class": "external",
                "reason": "user requested echo",
            }],
        })

    app = create_app(config, model_client=fake_client(malicious_payload_reply()))
    client = TestClient(app)

    turn = client.post("/chat", json={"message": "echo hello"}).json()
    call_digest = turn["tool_results"][0]["call_digest"]

    ledger = SQLiteEventLedger(config.events_database_path())
    try:
        proposal_event = next(
            event
            for event in ledger.iter_events_by_name("tool.proposal.evaluated")
            if event.payload["tool_result"]["call_digest"] == call_digest
        )
    finally:
        ledger.close()

    binding = proposal_event.payload["intent_binding"]
    assert binding["status"] == "needs_confirmation"
    assert "payload_mutation_verb_not_in_intent" in binding["reason_codes"]


def test_payload_surface_check_does_not_flag_matching_content(tmp_path):
    """Payload content matching intent tokens → still bound, no anomaly."""
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(config, model_client=fake_client(tool_proposal_reply(message="hello")))
    client = TestClient(app)

    turn = client.post("/chat", json={"message": "echo hello"}).json()
    call_digest = turn["tool_results"][0]["call_digest"]

    ledger = SQLiteEventLedger(config.events_database_path())
    try:
        proposal_event = next(
            event
            for event in ledger.iter_events_by_name("tool.proposal.evaluated")
            if event.payload["tool_result"]["call_digest"] == call_digest
        )
    finally:
        ledger.close()

    assert proposal_event.payload["intent_binding"]["status"] == "bound"


def test_same_action_replay_is_not_yet_blocked():
    """
    Known gap: same-action replay is not prevented.

    Wrong-action replay is blocked by digest mismatch (see test_approval_digest_mismatch_halts_execution).
    But an ApprovalRecord issued for action A can be reused to execute action A a second time —
    no spent-token tracking exists yet. This test documents the gap honestly.

    Closes when a spent-token authority and single-use consumption enforcement land (item 4).
    """
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "Echo", MutationClass.EXTERNAL, commit_threshold="reversible"))
    registry.register_callable("echo", lambda message="", **_: {"echoed": message})
    executor = ToolExecutor(registry, invariant=default_invariant())

    call = ToolCall("echo", payload={"message": "ping"})
    action = executor.action_for_call(call)
    approval = approval_for(action, approved_by="brad", approval_basis="test")

    with mutation_context(actor="test", operation="replay_gap_test"):
        first = executor.evaluate(call, approval=approval)
        second = executor.evaluate(call, approval=approval)

    assert first.status == ToolResultStatus.EXECUTED
    assert second.status == ToolResultStatus.EXECUTED  # gap: same approval accepted twice


# ---------------------------------------------------------------------------
# Bypass matrix: callable execution context guard
# ---------------------------------------------------------------------------

def _make_approved_executor_and_call():
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "Echo", MutationClass.EXTERNAL, commit_threshold="reversible"))
    registry.register_callable("echo", lambda message="", **_: {"echoed": message})
    executor = ToolExecutor(registry, invariant=default_invariant())
    call = ToolCall("echo", payload={"message": "ping"})
    action = executor.action_for_call(call)
    approval = approval_for(action, approved_by="brad", approval_basis="test")
    return executor, call, approval


def test_callable_execution_stale_context_is_refused():
    """Callable returns REFUSED when mutation context has already exited."""
    executor, call, approval = _make_approved_executor_and_call()

    with mutation_context(actor="test", operation="stale_context_setup"):
        pass  # context exits here

    result = executor.evaluate(call, approval=approval)

    assert result.status == ToolResultStatus.REFUSED
    assert result.reason_code == "mutation_context_required"


def test_callable_execution_indirect_call_without_context_is_refused():
    """Guard fires even when evaluate() is called from an intermediary function."""
    executor, call, approval = _make_approved_executor_and_call()

    def indirect_evaluate():
        return executor.evaluate(call, approval=approval)

    result = indirect_evaluate()

    assert result.status == ToolResultStatus.REFUSED
    assert result.reason_code == "mutation_context_required"


def test_callable_execution_indirect_call_inherits_active_context():
    """Context propagates correctly through the call stack when active."""
    executor, call, approval = _make_approved_executor_and_call()

    def indirect_evaluate():
        return executor.evaluate(call, approval=approval)

    with mutation_context(actor="test", operation="indirect_propagation_test"):
        result = indirect_evaluate()

    assert result.status == ToolResultStatus.EXECUTED
    assert result.output == {"echoed": "ping"}
