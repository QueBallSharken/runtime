import json

from fastapi.testclient import TestClient

import pytest

from halcyon_core.api.server import build_parser, create_app, startup_summary, validate_bind
from halcyon_core.config import RuntimeConfig
from halcyon_core.events import SQLiteEventLedger
from halcyon_core.governance.mutation_context import mutation_context
from halcyon_core.memory.proposals import SQLiteMemoryProposalQueue, propose_memory
from halcyon_core.memory.store import SQLiteMemoryStore
from halcyon_core.model.client import LocalModelClient, ModelClientError


def fake_client(content, captured=None):
    def transport(url, payload):
        if captured is not None:
            captured["called"] = True
            captured["payload"] = payload
        return {"choices": [{"message": {"content": content}}]}

    return LocalModelClient("http://local.test/v1", "fake", transport=transport)


def tool_proposal_reply(tool_name: str = "echo") -> str:
    return json.dumps({
        "reply": "ok",
        "memory_proposals": [],
        "tool_proposals": [{
            "action_type": tool_name,
            "target": tool_name,
            "payload": {"message": "hi"},
            "mutation_class": "external",
            "reason": "test",
        }],
    })


def test_api_health_returns_ok(tmp_path):
    app = create_app(RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite")), model_client=fake_client("hi"))
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["runtime"] == "halcyon_core"
    assert data["runtime_version"] == "v0.1"
    assert data["spec_version"] == "mvl-v0.1"
    assert data["governance_mode"] == "deny_by_default"
    assert data["runtime_posture"] == "healthy"


def test_api_health_returns_observability_metadata(tmp_path):
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    queue = SQLiteMemoryProposalQueue(config.proposal_database_path())
    with mutation_context(actor="test", operation="setup"):
        queue.submit(propose_memory("pending", "test"))
    queue.close()
    app = create_app(
        config,
        model_client=fake_client(json.dumps({"reply": "ok", "memory_proposals": [], "tool_proposals": []})),
    )
    client = TestClient(app)
    client.post("/chat", json={"message": "hello"})

    data = client.get("/health").json()

    assert data["pending_proposals"] == 1
    assert data["latest_event_index"] > 0
    assert data["database_path"].endswith("halcyon.sqlite")


def test_server_runner_requires_allow_external_for_non_localhost():
    validate_bind("127.0.0.1", allow_external=False)
    validate_bind("0.0.0.0", allow_external=True)
    with pytest.raises(SystemExit):
        validate_bind("0.0.0.0", allow_external=False)


def test_server_startup_summary_declares_boundary(tmp_path):
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))

    local = startup_summary(config, host="127.0.0.1", port=8765)
    external = startup_summary(config, host="0.0.0.0", port=8765, allow_external=True)

    assert "Halcyon API running" in local
    assert "Host: 127.0.0.1" in local
    assert "Port: 8765" in local
    assert "Mode: deny_by_default" in local
    assert "Exposure: LOCAL ONLY" in local
    assert "Exposure: EXTERNAL ENABLED" in external
    assert "Warning: external network exposure explicitly enabled" in external


def test_server_parser_defaults_to_localhost():
    args = build_parser().parse_args([])

    assert args.host == "127.0.0.1"
    assert args.port == 8765
    assert args.allow_external is False


def test_api_chat_returns_explicit_turn_shape(tmp_path):
    app = create_app(
        RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite")),
        model_client=fake_client(json.dumps({"reply": "api online", "memory_proposals": [], "tool_proposals": []})),
    )
    client = TestClient(app)

    response = client.post("/chat", json={"message": "hello api"})
    data = response.json()

    assert response.status_code == 200
    assert data["status"] == "stable"
    assert data["turn"]["reply"] == "api online"
    assert data["interpretation"]["status"] == "stable"
    assert data["memory_proposals"] == []
    assert data["tool_results"] == []
    assert data["final_claim"]["artifact_type"] == "final_claim"


def test_api_chat_model_error_returns_structured_payload(tmp_path):
    def failing_transport(url, payload):
        raise ModelClientError("model endpoint returned HTTP 400", status_code=400, body="bad request")

    app = create_app(
        RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite")),
        model_client=LocalModelClient("http://local.test/v1", "fake", transport=failing_transport),
    )
    client = TestClient(app)

    response = client.post("/chat", json={"message": "hello api"})
    data = response.json()

    assert response.status_code == 502
    assert data["detail"]["reason_code"] == "model_endpoint_error"
    assert data["detail"]["message"] == "model endpoint failed"
    assert data["detail"]["evidence"][0]["status_code"] == 400
    assert data["detail"]["evidence"][0]["body"] == "bad request"


def test_api_chat_turn_matches_cli_runtime_turn_shape(tmp_path):
    app = create_app(
        RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite")),
        model_client=fake_client(json.dumps({"reply": "shape", "memory_proposals": [], "tool_proposals": []})),
    )
    client = TestClient(app)

    data = client.post("/chat", json={"message": "shape check"}).json()

    turn = data["turn"]
    assert set(turn) >= {
        "reply",
        "user_event",
        "model_event",
        "claim_event",
        "final_claim",
        "memory_proposals",
        "tool_decisions",
        "tool_results",
        "context_memory_count",
        "model_warnings",
        "interpretation",
    }
    assert data["final_claim"] == turn["final_claim"]
    assert data["memory_proposals"] == turn["memory_proposals"]
    assert data["tool_results"] == turn["tool_results"]


def test_api_chat_reuses_kernel_health_state_across_turns(tmp_path):
    app = create_app(
        RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite")),
        model_client=fake_client(tool_proposal_reply()),
    )
    client = TestClient(app)

    first = client.post("/chat", json={"message": "echo hi"}).json()
    second = client.post("/chat", json={"message": "echo hi again"}).json()

    assert first["tool_results"][0]["reason_code"] == "abac_denied_by_default"
    assert second["tool_results"][0]["reason_code"] == "health_restricted"
    assert app.state.runtime_state.health.consecutive_refusals == 2
    assert client.get("/health").json()["runtime_posture"] == "mild_stress"


def test_api_chat_needs_confirmation_does_not_mutate_or_execute(tmp_path):
    captured = {}
    db = tmp_path / "halcyon.sqlite"
    app = create_app(
        RuntimeConfig(database_path=str(db)),
        model_client=fake_client(json.dumps({"reply": "should not run", "memory_proposals": [{"content":"bad","reason":"bad"}], "tool_proposals": [{"action_type":"write_file","target":"x","payload":{}}]}), captured=captured),
    )
    client = TestClient(app)

    response = client.post("/chat", json={"message": "delete all three temp files"})
    data = response.json()

    assert response.status_code == 200
    assert data["status"] == "needs_confirmation"
    assert data["turn"]["model_event"] is None
    assert data["memory_proposals"] == []
    assert data["tool_results"] == []
    assert captured == {}

    store = SQLiteMemoryStore(RuntimeConfig(database_path=str(db)).memory_database_path())
    try:
        assert store.recent() == []
    finally:
        store.close()


def test_api_memory_proposal_list_approve_reject(tmp_path):
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    queue = SQLiteMemoryProposalQueue(config.proposal_database_path())
    with mutation_context(actor="test", operation="setup"):
        first = queue.submit(propose_memory("api proposal", "test", tags=("api",)))
        second = queue.submit(propose_memory("reject me", "test"))
    queue.close()

    app = create_app(config, model_client=fake_client("hi"))
    client = TestClient(app)

    listed = client.get("/memory/proposals").json()["proposals"]
    assert [proposal["proposal_id"] for proposal in listed] == [first.proposal_id, second.proposal_id]

    approved = client.post(
        f"/memory/proposals/{first.proposal_id}/approve",
        json={"decided_by": "brad", "reason": "api approval"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    rejected = client.post(
        f"/memory/proposals/{second.proposal_id}/reject",
        json={"decided_by": "brad", "reason": "api rejection"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    ledger = SQLiteEventLedger(config.events_database_path())
    try:
        approval_event = next(
            event
            for event in ledger.iter_events_by_name("memory.proposal.approved")
            if event.payload["proposal_id"] == first.proposal_id
        )
        durable_event = next(
            event
            for event in ledger.iter_events_by_name("memory.durable.created")
            if event.payload["proposal_id"] == first.proposal_id
        )
        rejection_event = next(
            event
            for event in ledger.iter_events_by_name("memory.proposal.rejected")
            if event.payload["proposal_id"] == second.proposal_id
        )
        rejected_durable_events = [
            event
            for event in ledger.iter_events_by_name("memory.durable.created")
            if event.payload["proposal_id"] == second.proposal_id
        ]
    finally:
        ledger.close()

    assert approval_event.index < durable_event.index
    assert durable_event.parent_index == approval_event.index
    assert approval_event.payload["decided_by"] == "brad"
    assert approval_event.payload["decision_reason"] == "api approval"
    assert approval_event.payload["reason_codes"] == ["memory_proposal_approved"]
    assert durable_event.payload["memory_id"] == approved.json()["memory_id"]
    assert durable_event.payload["source_events"] == [approval_event.index]
    assert durable_event.payload["reason_codes"] == ["approved_memory_written"]
    assert rejection_event.payload["decided_by"] == "brad"
    assert rejection_event.payload["decision_reason"] == "api rejection"
    assert rejection_event.payload["reason_codes"] == ["memory_proposal_rejected"]
    assert rejected_durable_events == []


def test_api_maintenance_pulse_has_trace_fields(tmp_path):
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(
        config,
        model_client=fake_client(json.dumps({"reply": "ok", "memory_proposals": [], "tool_proposals": []})),
    )
    client = TestClient(app)
    client.post("/chat", json={"message": "hello"})

    response = client.get("/maintenance/pulse")
    pulse = response.json()["pulse"]

    assert response.status_code == 200
    assert pulse["reason_codes"] == ["pulse_snapshot"]
    assert pulse["evidence"]
    assert pulse["source_events"]


def test_api_maintenance_report_preserves_reason_codes_and_evidence(tmp_path):
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(
        config,
        model_client=fake_client(json.dumps({"reply": "ok", "memory_proposals": [{"content":"candidate","reason":"test"}], "tool_proposals": []})),
    )
    client = TestClient(app)
    client.post("/chat", json={"message": "what should be saved?"})

    response = client.get("/maintenance/report")
    report = response.json()["report"]

    assert response.status_code == 200
    assert report["reason_codes"] == ["maintenance_report"]
    assert report["evidence"] == ["findings=1"]
    assert report["findings"][0]["reason_codes"] == ["pending_proposal_backlog"]
    assert report["findings"][0]["evidence"] == ["pending_proposals=1"]
