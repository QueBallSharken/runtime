import pytest

from halcyon_core.governance.abac import ActionPackage, approval_for, evaluate_action, refuse_by_default
from halcyon_core.governance.cbac import TransformRecord, create_envelope, evaluate_handoff
from halcyon_core.governance.invariants import CanonicalInvariant, default_invariant
from halcyon_core.governance.mutation_context import MutationContextRequired, mutation_context, require_mutation_context
from halcyon_core.governance.policy import decide_external_mutation, external_mutation_allowed
from halcyon_core.governance.health import SBACEvidence, evaluate_health
from halcyon_core.memory.proposals import MemoryProposalQueue, propose_memory


def test_invariant_digest_is_canonical_and_stable():
    left = CanonicalInvariant(
        invariant_id="inv",
        version="1",
        statement="External writes require approval.",
        scope=("file", "network"),
        metadata={"b": 2, "a": 1},
    )
    right = CanonicalInvariant(
        invariant_id="inv",
        version="1",
        statement="External writes require approval.",
        scope=("file", "network"),
        metadata={"a": 1, "b": 2},
    )
    assert left.digest() == right.digest()
    assert left.digest().startswith("sha384:")


def test_default_invariant_declares_deny_by_default_external_mutation():
    invariant = default_invariant()
    assert invariant.enforcement == "deny_by_default"
    assert "external_mutation" in invariant.scope


def test_cbac_envelope_verifies_matching_invariant_digest():
    invariant = default_invariant()
    envelope = create_envelope(
        "kernel",
        "model_adapter",
        invariant,
        identity_evidence="process_bound",
    )
    assert envelope.verify(invariant.digest()) is True
    evaluation = evaluate_handoff(envelope, invariant.digest())
    assert evaluation.condition_summary == {"C2": "pass", "C4": "pass"}
    assert evaluation.claim_cap == "strong_segment_governance"


def test_cbac_mismatched_digest_fails_continuity():
    invariant = default_invariant()
    envelope = create_envelope("kernel", "tool_router", invariant, identity_evidence="process_bound")
    evaluation = evaluate_handoff(envelope, "sha384:not-the-same")
    assert evaluation.condition_summary["C2"] == "fail"
    assert evaluation.claim_cap == "observability_only"


def test_cbac_lossy_transform_fails_even_if_digest_matches():
    invariant = default_invariant()
    envelope = create_envelope(
        "kernel",
        "summarizer",
        invariant,
        transform_chain=(TransformRecord(name="summary", kind="summary", lossy=True),),
        identity_evidence="process_bound",
    )
    evaluation = evaluate_handoff(envelope, invariant.digest())
    assert evaluation.condition_summary["C2"] == "fail"
    assert "lossy transform" in evaluation.reasons[0]


def test_cbac_opaque_transform_caps_path_completeness():
    invariant = default_invariant()
    envelope = create_envelope(
        "kernel",
        "remote_adapter",
        invariant,
        transform_chain=(TransformRecord(name="remote", kind="opaque", visibility="opaque"),),
        identity_evidence="process_bound",
    )
    evaluation = evaluate_handoff(envelope, invariant.digest())
    assert evaluation.condition_summary["C4"] == "partial"
    assert evaluation.claim_cap == "partial_governance"


def test_runtime_metadata_identity_caps_cbac_continuity_to_partial():
    invariant = default_invariant()
    envelope = create_envelope("kernel", "model_adapter", invariant)
    evaluation = evaluate_handoff(envelope, invariant.digest())
    assert evaluation.condition_summary["C2"] == "partial"
    assert evaluation.claim_cap == "partial_governance"


def test_abac_refuses_external_mutation_by_default():
    action = ActionPackage("write_file", "notes.txt", {"content": "hello"})
    decision = refuse_by_default(action)
    assert decision.status == "refuse"
    assert decision.refusal_capacity is True
    assert decision.condition_summary["C1"] == "pass"
    assert decision.condition_summary["C6"] == "pass"
    assert decision.action_digest == action.digest()


def test_abac_halts_when_refusal_capacity_is_absent():
    action = ActionPackage("send_email", "person@example.com", {"body": "hi"})
    decision = evaluate_action(action, refusal_capacity=False)
    assert decision.status == "halt"
    assert decision.condition_summary["C1"] == "fail"
    assert decision.condition_summary["C6"] == "fail"


def test_abac_halts_on_approval_digest_mismatch():
    approved = ActionPackage("write_file", "a.txt", {"content": "original"})
    changed = ActionPackage("write_file", "a.txt", {"content": "changed"})
    approval = approval_for(approved, approved_by="brad", approval_basis="test approval")
    decision = evaluate_action(changed, approval=approval)
    assert decision.status == "halt"
    assert decision.condition_summary["C3"] == "fail"
    assert decision.condition_summary["C6"] == "fail"


def test_abac_approves_when_action_identity_and_commit_topology_are_declared():
    action = ActionPackage(
        "write_file",
        "notes.txt",
        {"content": "hello"},
        invariant_digest=default_invariant().digest(),
        irreversible_primitive="atomic_replace",
        commit_threshold="externally_observable",
    )
    approval = approval_for(action, approved_by="brad", approval_basis="explicit test approval")
    decision = evaluate_action(action, approval=approval)
    assert decision.status == "approve"
    assert decision.condition_summary == {"C1": "pass", "C3": "pass", "C4": "pass", "C6": "pass"}


def test_abac_downgrades_when_commit_topology_is_incomplete():
    action = ActionPackage("write_file", "notes.txt", {"content": "hello"})
    approval = approval_for(action, approved_by="brad", approval_basis="explicit test approval")
    decision = evaluate_action(action, approval=approval)
    assert decision.status == "downgrade"
    assert decision.condition_summary["C3"] == "partial"
    assert decision.condition_summary["C4"] == "partial"
    assert decision.condition_summary["C6"] == "partial"


def test_policy_delegates_to_abac_gate():
    action = ActionPackage("write_file", "notes.txt", {"content": "hello"})
    decision = decide_external_mutation(action)
    assert external_mutation_allowed(False) is False
    assert external_mutation_allowed(True) is True
    assert decision.status == "refuse"


def test_sbac_mvp_defaults_to_unknown_timing():
    assert evaluate_health() == {"C5": "unknown"}


def test_sbac_strong_timing_requires_activation_level_evidence():
    evidence = SBACEvidence(
        detector_trust="activation_level",
        timing_relation="before_commitment",
        commitment_model_declared=True,
    )
    assert evaluate_health(evidence) == {"C5": "pass"}


def test_sbac_after_commitment_fails():
    evidence = SBACEvidence(
        detector_trust="activation_level",
        timing_relation="after_commitment",
        commitment_model_declared=True,
    )
    assert evaluate_health(evidence) == {"C5": "fail"}


# ---------------------------------------------------------------------------
# Mutation context: malformed token, stale token, nested call chains
# ---------------------------------------------------------------------------

def test_malformed_context_token_empty_actor_raises():
    with pytest.raises(ValueError, match="actor"):
        with mutation_context(actor="", operation="some_operation"):
            pass


def test_malformed_context_token_empty_operation_raises():
    with pytest.raises(ValueError, match="operation"):
        with mutation_context(actor="test", operation=""):
            pass


def test_stale_context_token_mutation_after_exit_raises():
    queue = MemoryProposalQueue()
    with mutation_context(actor="test", operation="setup"):
        proposal = queue.submit(propose_memory("stale test", "test"))

    with mutation_context(actor="test", operation="stale_context_test"):
        pass  # context exits here

    with pytest.raises(MutationContextRequired):
        queue.reject(proposal.proposal_id, decided_by="test")

    assert queue.get(proposal.proposal_id).status == "pending"


def test_nested_indirect_call_without_context_is_denied():
    """Guard fires even when the call is indirect — no ambient authority leaks."""
    queue = MemoryProposalQueue()
    with mutation_context(actor="test", operation="setup"):
        proposal = queue.submit(propose_memory("indirect test", "test"))

    def indirect_reject():
        queue.reject(proposal.proposal_id, decided_by="indirect_caller")

    with pytest.raises(MutationContextRequired):
        indirect_reject()

    assert queue.get(proposal.proposal_id).status == "pending"


def test_nested_indirect_call_inherits_active_context():
    """Context propagates correctly through the call stack when active."""
    queue = MemoryProposalQueue()
    with mutation_context(actor="test", operation="setup"):
        proposal = queue.submit(propose_memory("inherited context test", "test"))

    def indirect_reject():
        queue.reject(proposal.proposal_id, decided_by="indirect_caller")

    with mutation_context(actor="test", operation="indirect_context_propagation_test"):
        indirect_reject()

    assert queue.get(proposal.proposal_id).status == "rejected"
