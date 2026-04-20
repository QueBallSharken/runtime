import json

import pytest
from fastapi.testclient import TestClient

from halcyon_core.api.server import create_app
from halcyon_core.config import RuntimeConfig
from halcyon_core.events import RuntimeEvent, SQLiteEventLedger
from halcyon_core.governance.event_schema import (
    MUTATION_EVENT_SCHEMA_VERSION,
    MutationEventSchemaError,
    validate_mutation_event,
    validate_mutation_events,
)
from halcyon_core.governance.mutation_context import mutation_context
from halcyon_core.memory.proposals import SQLiteMemoryProposalQueue, propose_memory
from halcyon_core.model.client import LocalModelClient


def fake_client(content: str):
    return LocalModelClient(
        "http://local.test/v1",
        "fake",
        transport=lambda url, payload: {"choices": [{"message": {"content": content}}]},
    )


def tool_proposal_reply() -> str:
    return json.dumps({
        "reply": "I will echo that.",
        "memory_proposals": [],
        "tool_proposals": [{
            "action_type": "echo",
            "target": "echo",
            "payload": {"message": "schema"},
            "mutation_class": "external",
            "reason": "user requested echo",
        }],
    })


def mutation_payload(**overrides):
    payload = {
        "schema_version": MUTATION_EVENT_SCHEMA_VERSION,
        "reason_codes": ("test_reason",),
        "evidence": {"test": True},
        "source_events": (1,),
    }
    payload.update(overrides)
    return payload


def test_api_mutation_events_pass_schema_lint(tmp_path):
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    queue = SQLiteMemoryProposalQueue(config.proposal_database_path())
    with mutation_context(actor="test", operation="setup"):
        approved_memory = queue.submit(propose_memory("remember this", "test"))
        rejected_memory = queue.submit(propose_memory("forget this", "test"))
    queue.close()

    app = create_app(config, model_client=fake_client(tool_proposal_reply()))
    client = TestClient(app)

    turn = client.post("/chat", json={"message": "echo schema"}).json()
    call_digest = turn["tool_results"][0]["call_digest"]
    assert client.post(
        f"/tools/proposals/{call_digest}/approve",
        json={"approved_by": "brad", "approval_basis": "schema test"},
    ).status_code == 200

    override_turn = client.post("/chat", json={"message": "please help me with this greeting"}).json()
    override_digest = override_turn["tool_results"][0]["call_digest"]
    assert client.post(
        f"/tools/proposals/{override_digest}/approve",
        json={"approved_by": "brad", "approval_basis": "intent_override_accepted"},
    ).status_code == 200
    assert client.post(
        f"/memory/proposals/{approved_memory.proposal_id}/approve",
        json={"decided_by": "brad", "reason": "schema test"},
    ).status_code == 200
    assert client.post(
        f"/memory/proposals/{rejected_memory.proposal_id}/reject",
        json={"decided_by": "brad", "reason": "schema test"},
    ).status_code == 200

    ledger = SQLiteEventLedger(config.events_database_path())
    try:
        events = list(ledger.iter_events())
    finally:
        ledger.close()

    validate_mutation_events(events)
    schema_events = [
        event for event in events
        if event.name in {
            "tool.intent_override.accepted",
            "tool.approval.granted",
            "tool.execution.completed",
            "memory.proposal.approved",
            "memory.durable.created",
            "memory.proposal.rejected",
        }
    ]
    assert {event.payload["schema_version"] for event in schema_events} == {
        MUTATION_EVENT_SCHEMA_VERSION
    }


def test_mutation_schema_lint_fails_without_reason_codes():
    event = RuntimeEvent(
        index=1,
        name="memory.proposal.approved",
        payload=mutation_payload(reason_codes=None),
    )

    with pytest.raises(MutationEventSchemaError, match="reason_codes"):
        validate_mutation_event(event)


def test_mutation_schema_lint_fails_without_execution_parent():
    event = RuntimeEvent(
        index=1,
        name="tool.execution.completed",
        parent_index=None,
        payload=mutation_payload(),
    )

    with pytest.raises(MutationEventSchemaError, match="parent_index"):
        validate_mutation_event(event)


def test_mutation_schema_lint_ignores_non_mutation_events():
    event = RuntimeEvent(index=1, name="turn.started", payload={})

    validate_mutation_event(event)
