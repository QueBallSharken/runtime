from halcyon_core.affect import (
    AffectState,
    ArousalLevel,
    ImportanceKind,
    SalienceLevel,
    SalienceSource,
    Valence,
    evaluate_salience,
)
from halcyon_core.governance.mutation_context import mutation_context
from halcyon_core.memory.proposals import MemoryProposalQueue, propose_memory
from halcyon_core.memory.store import SQLiteMemoryStore
from halcyon_core.tools.executor import ToolExecutor
from halcyon_core.tools.registry import ToolRegistry
from halcyon_core.tools.types import MutationClass, ToolCall, ToolSpec, ToolResultStatus


def test_default_salience_is_low():
    signal = evaluate_salience("hello there")

    assert signal.level == SalienceLevel.LOW
    assert signal.importance_kind == ImportanceKind.NONE
    assert signal.reason_codes == ()


def test_user_declared_importance_is_high():
    signal = evaluate_salience("important: remember this preference")

    assert signal.level == SalienceLevel.HIGH
    assert signal.importance_kind == ImportanceKind.USER_DECLARED
    assert "user_declared_importance" in signal.reason_codes


def test_model_memory_proposal_is_system_observed_medium():
    signal = evaluate_salience(
        source=SalienceSource.MEMORY_PROPOSAL,
        model_memory_proposal=True,
    )

    assert signal.level == SalienceLevel.MEDIUM
    assert signal.importance_kind == ImportanceKind.SYSTEM_OBSERVED
    assert signal.reason_codes == ("model_memory_proposal",)


def test_destructive_or_private_marker_is_critical():
    signal = evaluate_salience("private legal note", destructive_action_requested=True)

    assert signal.level == SalienceLevel.CRITICAL
    assert "destructive_action_requested" in signal.reason_codes
    assert "sensitivity_marker" in signal.reason_codes


def test_salience_serializes_enum_values_and_reason_codes():
    signal = evaluate_salience(
        "urgent",
        source=SalienceSource.GOVERNANCE,
        metadata={"event": 10},
    )
    data = signal.to_dict()

    assert data["level"] == "critical"
    assert data["source"] == "governance"
    assert data["importance_kind"] == "user_declared"
    assert data["reason_codes"] == ["urgent_marker"]
    assert data["metadata"] == {"event": 10}


def test_high_salience_does_not_change_tool_permission():
    signal = evaluate_salience("urgent", destructive_action_requested=True)
    registry = ToolRegistry((ToolSpec("write_file", "Write a file", MutationClass.FILESYSTEM),))

    result = ToolExecutor(registry).evaluate(ToolCall("write_file", payload={"path": "x.txt"}))

    assert signal.level == SalienceLevel.CRITICAL
    assert result.status == ToolResultStatus.REFUSED
    assert result.reason_code == "abac_denied_by_default"


def test_high_salience_does_not_approve_memory(tmp_path):
    signal = evaluate_salience("important: remember this", durable_memory_requested=True)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    queue = MemoryProposalQueue()

    with mutation_context(actor="test", operation="setup"):
        proposal = queue.submit(propose_memory("high salience proposal", "test", metadata={"salience": signal.to_dict()}))

    assert signal.level == SalienceLevel.HIGH
    assert queue.pending()[0].proposal_id == proposal.proposal_id
    assert store.recent() == []
    store.close()


def test_affect_state_is_bounded_and_serializable():
    state = AffectState(valence="mixed", arousal="high", labels=("Focus", "Review"))

    assert state.valence == Valence.MIXED
    assert state.arousal == ArousalLevel.HIGH
    assert state.labels == ("focus", "review")
    assert state.to_dict()["valence"] == "mixed"
    assert state.to_dict()["arousal"] == "high"


def test_interpretation_confirmation_is_system_observed_high():
    signal = evaluate_salience(source=SalienceSource.INTERPRETATION, interpretation_status="needs_confirmation")

    assert signal.level == SalienceLevel.HIGH
    assert signal.importance_kind == ImportanceKind.SYSTEM_OBSERVED
    assert signal.reason_codes == ("interpretation_needs_confirmation",)
