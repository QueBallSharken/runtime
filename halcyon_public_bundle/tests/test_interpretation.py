from halcyon_core.interpretation import evaluate_interpretation
from halcyon_core.interpretation.types import ConsequenceLevel, InterpretationStatus


def test_stable_interpretation_allows_response_and_proposals():
    artifact = evaluate_interpretation("what are our next options?")

    assert artifact.status == InterpretationStatus.STABLE
    assert artifact.allows_response() is True
    assert artifact.allows_proposals() is True
    assert artifact.binding_risk.has_any() is False
    assert artifact.commitment_risk.consequence_level == ConsequenceLevel.LOW


def test_unclear_referent_requires_clarification_not_confirmation():
    artifact = evaluate_interpretation("delete that")

    assert artifact.status == InterpretationStatus.NEEDS_CLARIFICATION
    assert artifact.binding_risk.unclear_referent is True
    assert artifact.commitment_risk.destructive_action_requested is True
    assert artifact.commitment_risk.consequence_level == ConsequenceLevel.DESTRUCTIVE
    assert artifact.clarification_prompt is not None
    assert artifact.confirmation_prompt is None
    assert artifact.allows_proposals() is False


def test_clear_destructive_request_requires_confirmation():
    artifact = evaluate_interpretation("delete all three temp files")

    assert artifact.status == InterpretationStatus.NEEDS_CONFIRMATION
    assert artifact.binding_risk.has_any() is False
    assert artifact.commitment_risk.destructive_action_requested is True
    assert artifact.commitment_risk.approval_required is True
    assert artifact.commitment_risk.consequence_level == ConsequenceLevel.DESTRUCTIVE
    assert artifact.confirmation_prompt is not None
    assert artifact.clarification_prompt is None
    assert artifact.allows_response() is True
    assert artifact.allows_proposals() is False


def test_clear_durable_memory_request_requires_confirmation():
    artifact = evaluate_interpretation("remember Brad prefers MVL before expansion")

    assert artifact.status == InterpretationStatus.NEEDS_CONFIRMATION
    assert artifact.commitment_risk.durable_memory_requested is True
    assert artifact.commitment_risk.consequence_level == ConsequenceLevel.MEDIUM
    assert "durable memory" in artifact.confirmation_prompt


def test_lexical_drift_requires_clarification_when_contextual():
    artifact = evaluate_interpretation("patch it", context_text="Current work includes specs and runtime code.")

    assert artifact.status == InterpretationStatus.NEEDS_CLARIFICATION
    assert artifact.binding_risk.unclear_referent is True
    assert artifact.binding_risk.lexical_drift is True
    assert artifact.candidate_bindings


def test_broad_scope_requires_clarification():
    artifact = evaluate_interpretation("clean this up")

    assert artifact.status == InterpretationStatus.NEEDS_CLARIFICATION
    assert artifact.binding_risk.scope_uncertainty is True
    assert artifact.clarification_prompt is not None


def test_artifact_to_dict_uses_enum_values():
    artifact = evaluate_interpretation("write notes to disk")
    data = artifact.to_dict()

    assert data["status"] == "needs_confirmation"
    assert data["commitment_risk"]["consequence_level"] == "high"
    assert isinstance(data["ambiguity_types"], list)
