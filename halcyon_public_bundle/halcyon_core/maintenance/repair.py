"""Read-only runtime repair findings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RepairFinding:
    finding_type: str
    severity: str
    summary: str
    reason_codes: tuple[str, ...]
    evidence: tuple[str, ...]
    source_events: tuple[int, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.reason_codes:
            raise ValueError("repair finding requires reason_codes")
        if not self.evidence:
            raise ValueError("repair finding requires evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_type": self.finding_type,
            "severity": self.severity,
            "summary": self.summary,
            "reason_codes": list(self.reason_codes),
            "evidence": list(self.evidence),
            "source_events": list(self.source_events),
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class MaintenanceReport:
    findings: tuple[RepairFinding, ...]
    reason_codes: tuple[str, ...]
    evidence: tuple[str, ...]
    source_events: tuple[int, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.reason_codes:
            raise ValueError("maintenance report requires reason_codes")
        if not self.evidence:
            raise ValueError("maintenance report requires evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [finding.to_dict() for finding in self.findings],
            "reason_codes": list(self.reason_codes),
            "evidence": list(self.evidence),
            "source_events": list(self.source_events),
            "metadata": self.metadata,
        }


def find_repairs(
    *,
    events: tuple[Any, ...] = (),
    pending_proposal_count: int = 0,
    proposal_backlog_threshold: int = 1,
    clarification_threshold: int = 2,
    observability_threshold: int = 2,
) -> tuple[RepairFinding, ...]:
    findings: list[RepairFinding] = []

    if pending_proposal_count >= proposal_backlog_threshold:
        findings.append(RepairFinding(
            finding_type="pending_proposal_backlog",
            severity="medium",
            summary="Pending memory proposals need review.",
            reason_codes=("pending_proposal_backlog",),
            evidence=(f"pending_proposals={pending_proposal_count}",),
        ))

    unknown_tool_events = _matching_events(events, _is_unknown_tool_event)
    if unknown_tool_events:
        findings.append(RepairFinding(
            finding_type="unknown_tool_proposals",
            severity="medium",
            summary="Model proposed undeclared tools.",
            reason_codes=("unknown_tool_proposals",),
            evidence=(f"unknown_tool_events={len(unknown_tool_events)}",),
            source_events=_event_indices(unknown_tool_events),
        ))

    clarification_events = _matching_events(events, lambda event: _event_name(event) == "interpretation.clarification_requested")
    if len(clarification_events) >= clarification_threshold:
        findings.append(RepairFinding(
            finding_type="repeated_clarifications",
            severity="medium",
            summary="Repeated clarification turns suggest binding friction.",
            reason_codes=("repeated_clarifications",),
            evidence=(f"clarification_events={len(clarification_events)}",),
            source_events=_event_indices(clarification_events),
        ))

    observability_events = _matching_events(events, _is_observability_claim_event)
    if len(observability_events) >= observability_threshold:
        findings.append(RepairFinding(
            finding_type="observability_only_claims",
            severity="low",
            summary="Runtime repeatedly emitted observability-only claims.",
            reason_codes=("observability_only_claims",),
            evidence=(f"observability_claim_events={len(observability_events)}",),
            source_events=_event_indices(observability_events),
        ))

    unresolved_salience = _unresolved_high_salience_events(events)
    if unresolved_salience:
        findings.append(RepairFinding(
            finding_type="high_salience_without_resolution",
            severity="high",
            summary="High or critical salience accumulated without later resolution evidence.",
            reason_codes=("high_salience_without_resolution",),
            evidence=(f"unresolved_high_salience_events={len(unresolved_salience)}",),
            source_events=_event_indices(unresolved_salience),
        ))

    return tuple(findings)


def create_maintenance_report(findings: tuple[RepairFinding, ...]) -> MaintenanceReport:
    source_events: list[int] = []
    for finding in findings:
        source_events.extend(finding.source_events)
    return MaintenanceReport(
        findings=findings,
        reason_codes=("maintenance_report",),
        evidence=(f"findings={len(findings)}",),
        source_events=tuple(dict.fromkeys(source_events)),
    )


def _matching_events(events: tuple[Any, ...], predicate) -> tuple[Any, ...]:
    return tuple(event for event in events if predicate(event))


def _event_name(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("name", ""))
    return str(getattr(event, "name", ""))


def _payload(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event.get("payload", {}) or {}
    return getattr(event, "payload", {}) or {}


def _event_indices(events: tuple[Any, ...]) -> tuple[int, ...]:
    indices: list[int] = []
    for event in events:
        index = event.get("index") if isinstance(event, dict) else getattr(event, "index", None)
        if index is not None:
            indices.append(int(index))
    return tuple(indices)


def _is_unknown_tool_event(event: Any) -> bool:
    payload = _payload(event)
    result = payload.get("tool_result", {})
    return _event_name(event) == "tool.proposal.evaluated" and result.get("status") == "unknown_tool"


def _is_observability_claim_event(event: Any) -> bool:
    payload = _payload(event)
    claim = payload.get("claim", {})
    return _event_name(event) == "runtime.claim.emitted" and claim.get("classification") == "observability_only"


def _unresolved_high_salience_events(events: tuple[Any, ...]) -> tuple[Any, ...]:
    resolution_names = {
        "memory.proposal.approved",
        "memory.proposal.rejected",
        "interpretation.confirmation_received",
        "maintenance.reviewed",
    }
    high_events: list[Any] = []
    last_resolution_index = -1
    for event in events:
        index = int(getattr(event, "index", event.get("index", 0) if isinstance(event, dict) else 0))
        if _event_name(event) in resolution_names:
            last_resolution_index = max(last_resolution_index, index)
        salience = _payload(event).get("salience", {})
        if salience.get("level") in {"high", "critical"}:
            high_events.append(event)
    return tuple(event for event in high_events if _event_index(event) > last_resolution_index)


def _event_index(event: Any) -> int:
    if isinstance(event, dict):
        return int(event.get("index", 0))
    return int(getattr(event, "index", 0))
