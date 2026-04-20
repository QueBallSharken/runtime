import json

from halcyon_core.governance.abac import ActionPackage, GateDecision
from halcyon_core.governance.mutation_context import mutation_context
from halcyon_core.kernel import RuntimeKernel, build_kernel_for_path
from halcyon_core.memory.store import make_memory
from halcyon_core.model.client import LocalModelClient


def fake_client(content, captured=None):
    def transport(url, payload):
        if captured is not None:
            captured["url"] = url
            captured["payload"] = payload
        return {"choices": [{"message": {"content": content}}]}

    return LocalModelClient("http://local.test/v1", "openai/gpt-oss-20b", transport=transport)


def test_kernel_mvl_returns_reply_and_appends_core_events(tmp_path):
    kernel = build_kernel_for_path(
        tmp_path / "halcyon.sqlite",
        model_client=fake_client(json.dumps({"reply": "MVL online", "memory_proposals": [], "tool_proposals": []})),
    )

    turn = kernel.handle_message("ping")

    assert turn.reply == "MVL online"
    assert turn.user_event.name == "user.message.received"
    assert turn.model_event.name == "model.response.received"
    assert turn.claim_event.name == "runtime.claim.emitted"
    assert turn.final_claim.classification == "strong_segment_governance"
    assert turn.final_claim.condition_summary["C5"] == "partial"
    assert kernel.state.health.last_claim == "strong_segment_governance"
    assert [event.name for event in kernel.events.events] == [
        "user.message.received",
        "governance.cbac.evaluated",
        "interpretation.evaluated",
        "model.response.received",
        "runtime.claim.emitted",
    ]
    assert turn.interpretation.status.value == "stable"
    assert kernel.events.events[0].payload["salience"]["level"] == "low"
    assert kernel.events.events[2].payload["salience"]["source"] == "interpretation"
    kernel.close()


def test_kernel_includes_memory_context_in_model_prompt(tmp_path):
    captured = {}
    kernel = build_kernel_for_path(
        tmp_path / "halcyon.sqlite",
        model_client=fake_client("plain response", captured=captured),
    )
    with mutation_context(actor="test", operation="kernel_memory_seed"):
        kernel.memory.add(make_memory("Brad prefers MVL before expansion.", tags=("preference",), pinned=True))

    turn = kernel.handle_message("what next?", memory_tags=("preference",))

    system_content = captured["payload"]["messages"][0]["content"]
    assert "Relevant memory" in system_content
    assert "Brad prefers MVL before expansion." in system_content
    assert turn.context_memory_count == 1
    assert turn.model_warnings == ("model output was plain text",)
    kernel.close()


def test_kernel_captures_memory_proposals_without_durable_write(tmp_path):
    content = json.dumps(
        {
            "reply": "Noted as a proposal.",
            "memory_proposals": [
                {"content": "Brad likes local-first design.", "reason": "stated preference", "tags": ["preference"]}
            ],
            "tool_proposals": [],
        }
    )
    kernel = build_kernel_for_path(tmp_path / "halcyon.sqlite", model_client=fake_client(content))

    turn = kernel.handle_message("what should we track?")

    assert len(turn.memory_proposals) == 1
    assert turn.memory_proposals[0].content == "Brad likes local-first design."
    assert kernel.proposals.pending()[0].proposal_id == turn.memory_proposals[0].proposal_id
    assert kernel.memory.recent() == []
    proposal_event = next(event for event in kernel.events.events if event.name == "memory.proposal.created")
    assert proposal_event.payload["salience"]["level"] == "medium"
    assert proposal_event.payload["salience"]["importance_kind"] == "system_observed"
    assert "memory.proposal.created" in [event.name for event in kernel.events.events]
    kernel.close()


def test_kernel_records_unknown_tool_proposals_without_execution(tmp_path):
    content = json.dumps(
        {
            "reply": "I can propose that, not execute it.",
            "memory_proposals": [],
            "tool_proposals": [
                {
                    "action_type": "write_file",
                    "target": "x.txt",
                    "payload": {"content": "hi"},
                    "reason": "user asked",
                    "mutation_class": "filesystem",
                }
            ],
        }
    )
    kernel = build_kernel_for_path(tmp_path / "halcyon.sqlite", model_client=fake_client(content))

    turn = kernel.handle_message("what tool path would be safest?")

    assert turn.tool_decisions == ()
    assert len(turn.tool_results) == 1
    assert turn.tool_results[0].status.value == "unknown_tool"
    assert turn.tool_results[0].reason_code == "unknown_tool"
    tool_event = next(event for event in kernel.events.events if event.name == "tool.proposal.evaluated")
    assert tool_event.payload["salience"]["level"] == "medium"
    assert tool_event.payload["salience"]["importance_kind"] == "system_observed"
    assert "tool.proposal.evaluated" in [event.name for event in kernel.events.events]
    kernel.close()


def test_kernel_persists_event_sequence_across_restart(tmp_path):
    db_path = tmp_path / "halcyon.sqlite"
    first = build_kernel_for_path(
        db_path,
        model_client=fake_client(json.dumps({"reply": "one", "memory_proposals": [], "tool_proposals": []})),
    )
    first_turn = first.handle_message("one")
    first.close()

    second = build_kernel_for_path(
        db_path,
        model_client=fake_client(json.dumps({"reply": "two", "memory_proposals": [], "tool_proposals": []})),
    )
    second_turn = second.handle_message("two")

    assert first_turn.user_event.index == 1
    assert second_turn.user_event.index == 6
    second.close()


def test_runtime_turn_to_dict_is_serializable_shape(tmp_path):
    kernel = build_kernel_for_path(tmp_path / "halcyon.sqlite", model_client=fake_client("hello"))
    turn = kernel.handle_message("hi")
    data = turn.to_dict()

    assert data["reply"] == "hello"
    assert data["final_claim"]["artifact_type"] == "final_claim"
    assert data["model_warnings"] == ["model output was plain text"]
    kernel.close()



def test_kernel_clarifies_unclear_binding_before_model_call(tmp_path):
    captured = {}
    kernel = build_kernel_for_path(
        tmp_path / "halcyon.sqlite",
        model_client=fake_client(json.dumps({"reply": "should not run", "memory_proposals": [], "tool_proposals": []}), captured=captured),
    )

    turn = kernel.handle_message("delete that")

    assert "clarification" in turn.reply.lower()
    assert turn.model_event is None
    assert turn.interpretation.status.value == "needs_clarification"
    assert turn.memory_proposals == ()
    assert turn.tool_decisions == ()
    assert captured == {}
    assert "interpretation.clarification_requested" in [event.name for event in kernel.events.events]
    kernel.close()


def test_kernel_confirms_clear_consequential_request_before_model_call(tmp_path):
    captured = {}
    kernel = build_kernel_for_path(
        tmp_path / "halcyon.sqlite",
        model_client=fake_client(json.dumps({"reply": "should not run", "memory_proposals": [], "tool_proposals": []}), captured=captured),
    )

    turn = kernel.handle_message("delete all three temp files")

    assert "confirm" in turn.reply.lower()
    assert turn.model_event is None
    assert turn.interpretation.status.value == "needs_confirmation"
    assert turn.memory_proposals == ()
    assert turn.tool_decisions == ()
    assert captured == {}
    assert "interpretation.confirmation_requested" in [event.name for event in kernel.events.events]
    kernel.close()


def test_kernel_default_proposal_queue_persists_across_restart(tmp_path):
    db_path = tmp_path / "halcyon.sqlite"
    content = json.dumps(
        {
            "reply": "proposal captured",
            "memory_proposals": [
                {"content": "persistent proposal", "reason": "test", "tags": ["persist"]}
            ],
            "tool_proposals": [],
        }
    )
    first = build_kernel_for_path(db_path, model_client=fake_client(content))
    turn = first.handle_message("what should be queued?")
    proposal_id = turn.memory_proposals[0].proposal_id
    first.close()

    second = build_kernel_for_path(
        db_path,
        model_client=fake_client(json.dumps({"reply": "ok", "memory_proposals": [], "tool_proposals": []})),
    )
    pending = second.proposals.pending()
    assert [proposal.proposal_id for proposal in pending] == [proposal_id]
    second.close()


def test_final_claim_no_longer_universal_observability_only_with_evidence(tmp_path, monkeypatch):
    import halcyon_core.kernel
    monkeypatch.setattr(halcyon_core.kernel, "evaluate_health", lambda *a, **k: {"C5": "pass"})

    kernel = build_kernel_for_path(tmp_path / "halcyon.sqlite", model_client=fake_client(""))
    claim = kernel._final_claim({"C2": "pass", "C4": "pass"}, ())
    
    assert claim.classification == "strong_segment_governance"
    kernel.close()


def test_final_claim_uses_runtime_sbac_evidence_instead_of_empty_default(tmp_path):
    kernel = build_kernel_for_path(tmp_path / "halcyon.sqlite", model_client=fake_client(""))
    claim = kernel._final_claim({"C2": "pass", "C4": "pass"}, ())

    assert claim.condition_summary["C5"] == "partial"
    assert "sbac_uninstrumented" not in claim.reasons
    assert any("sbac_heuristic_runtime_telemetry" in reason for reason in claim.reasons)
    kernel.close()


def test_final_claim_propagates_authority_condition_from_tool_decision(tmp_path):
    kernel = build_kernel_for_path(tmp_path / "halcyon.sqlite", model_client=fake_client(""))
    action = ActionPackage("write_file", "notes.txt", {"content": "changed"})
    authority_failure = GateDecision(
        status="halt",
        reason="approval digest does not match action digest",
        action_digest=action.digest(),
        condition_summary={"C1": "pass", "C3": "fail", "C4": "partial", "C6": "fail"},
        approval_digest="sha384:stale",
        refusal_capacity=True,
    )

    claim = kernel._final_claim({"C2": "pass", "C4": "pass"}, (authority_failure,))

    assert claim.condition_summary["C6"] == "fail"
    assert claim.classification == "observability_only"
    assert claim.binary_override_triggered is True
    assert claim.f_family_rollups["F6"] == 0
    kernel.close()
