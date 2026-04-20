import pytest

from halcyon_core.affect import SalienceLevel, SalienceSource, evaluate_salience
from halcyon_core.events import EventBus
from halcyon_core.governance.mutation_context import mutation_context
from halcyon_core.maintenance import (
    create_consolidation_candidate,
    create_dream_artifact,
    create_maintenance_report,
    create_pulse_snapshot,
    find_repairs,
)
from halcyon_core.maintenance.consolidate import ConsolidationCandidate
from halcyon_core.maintenance.dream import DreamArtifact
from halcyon_core.memory.proposals import MemoryProposalQueue, propose_memory
from halcyon_core.memory.store import SQLiteMemoryStore


def test_pulse_snapshot_serializes_traceable_fields():
    events = EventBus()
    first = events.append("user.message.received", text="hi")

    pulse = create_pulse_snapshot(
        events=tuple(events.events),
        pending_proposal_count=2,
        last_claim="observability_only",
    )
    data = pulse.to_dict()

    assert data["event_count"] == 1
    assert data["pending_proposal_count"] == 2
    assert data["last_claim"] == "observability_only"
    assert data["reason_codes"] == ["pulse_snapshot"]
    assert data["source_events"] == [first.index]
    assert data["evidence"]


def test_pulse_counts_pending_proposals():
    queue = MemoryProposalQueue()
    with mutation_context(actor="test", operation="setup"):
        queue.submit(propose_memory("candidate", "test"))

    pulse = create_pulse_snapshot(pending_proposal_count=len(queue.pending()))

    assert pulse.pending_proposal_count == 1
    assert "pending_proposals=1" in pulse.evidence


def test_repair_detects_pending_proposal_backlog():
    findings = find_repairs(pending_proposal_count=2, proposal_backlog_threshold=1)

    assert findings[0].finding_type == "pending_proposal_backlog"
    assert findings[0].reason_codes == ("pending_proposal_backlog",)
    assert findings[0].evidence == ("pending_proposals=2",)


def test_repair_detects_unknown_tool_events():
    events = EventBus()
    event = events.append(
        "tool.proposal.evaluated",
        tool_result={"status": "unknown_tool", "reason_code": "unknown_tool"},
    )

    findings = find_repairs(events=tuple(events.events), pending_proposal_count=0)

    unknown = next(finding for finding in findings if finding.finding_type == "unknown_tool_proposals")
    assert unknown.source_events == (event.index,)
    assert unknown.reason_codes == ("unknown_tool_proposals",)


def test_repair_detects_high_salience_without_resolution():
    events = EventBus()
    signal = evaluate_salience("important", source=SalienceSource.USER_MESSAGE)
    event = events.append("user.message.received", text="important", salience=signal.to_dict())

    findings = find_repairs(events=tuple(events.events), pending_proposal_count=0)

    finding = next(finding for finding in findings if finding.finding_type == "high_salience_without_resolution")
    assert finding.source_events == (event.index,)
    assert finding.severity == "high"


def test_high_salience_resolution_suppresses_unresolved_finding():
    events = EventBus()
    signal = evaluate_salience("important", source=SalienceSource.USER_MESSAGE)
    events.append("user.message.received", text="important", salience=signal.to_dict())
    events.append("maintenance.reviewed", reviewed=True)

    findings = find_repairs(events=tuple(events.events), pending_proposal_count=0)

    assert all(finding.finding_type != "high_salience_without_resolution" for finding in findings)


def test_consolidation_candidate_is_speculative_and_traceable():
    candidate = create_consolidation_candidate(
        "Brad prefers traceable maintenance.",
        source_events=(1, 2),
        evidence=("two source events",),
    )
    data = candidate.to_dict()

    assert candidate.speculative is True
    assert data["speculative"] is True
    assert data["reason_codes"] == ["consolidation_candidate"]
    assert data["source_events"] == [1, 2]


def test_dream_artifact_is_always_speculative():
    artifact = create_dream_artifact("Maybe cluster these notes.", source_events=(3,), evidence=("idle synthesis",))

    assert artifact.speculative is True
    assert artifact.to_dict()["reason_codes"] == ["dream_artifact", "speculative_synthesis"]
    with pytest.raises(ValueError):
        DreamArtifact(
            "bad",
            reason_codes=("dream_artifact",),
            evidence=("attempt",),
            speculative=False,
        )


def test_consolidation_candidate_cannot_be_marked_durable():
    with pytest.raises(ValueError):
        ConsolidationCandidate(
            "bad",
            reason_codes=("consolidation_candidate",),
            evidence=("attempt",),
            speculative=False,
        )


def test_maintenance_does_not_write_memory(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    queue = MemoryProposalQueue()
    with mutation_context(actor="test", operation="setup"):
        queue.submit(propose_memory("pending candidate", "test"))
    before = store.recent()

    create_pulse_snapshot(pending_proposal_count=len(queue.pending()))
    find_repairs(pending_proposal_count=len(queue.pending()))
    create_consolidation_candidate("candidate", evidence=("test",))
    create_dream_artifact("dream", evidence=("test",))

    assert before == []
    assert store.recent() == []
    store.close()


def test_maintenance_report_requires_traceable_fields():
    findings = find_repairs(pending_proposal_count=1)
    report = create_maintenance_report(findings)

    assert report.reason_codes == ("maintenance_report",)
    assert report.evidence == ("findings=1",)
    assert report.to_dict()["findings"][0]["reason_codes"]
