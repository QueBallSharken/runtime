"""Heartbeat and maintenance pulse artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class PulseSnapshot:
    created_at: str
    event_count: int = 0
    pending_proposal_count: int = 0
    last_claim: str | None = None
    containment: bool = False
    reason_codes: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    source_events: tuple[int, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "event_count": self.event_count,
            "pending_proposal_count": self.pending_proposal_count,
            "last_claim": self.last_claim,
            "containment": self.containment,
            "reason_codes": list(self.reason_codes),
            "evidence": list(self.evidence),
            "source_events": list(self.source_events),
            "metadata": self.metadata,
        }


def create_pulse_snapshot(
    *,
    events: tuple[Any, ...] = (),
    pending_proposal_count: int = 0,
    last_claim: str | None = None,
    containment: bool = False,
    metadata: dict[str, Any] | None = None,
) -> PulseSnapshot:
    reason_codes = ["pulse_snapshot"]
    evidence = [f"events={len(events)}", f"pending_proposals={pending_proposal_count}"]
    if last_claim:
        evidence.append(f"last_claim={last_claim}")
    if containment:
        reason_codes.append("containment_active")
        evidence.append("containment active")
    return PulseSnapshot(
        created_at=datetime.now(timezone.utc).isoformat(),
        event_count=len(events),
        pending_proposal_count=pending_proposal_count,
        last_claim=last_claim,
        containment=containment,
        reason_codes=tuple(reason_codes),
        evidence=tuple(evidence),
        source_events=_event_indices(events),
        metadata=metadata or {},
    )


def _event_indices(events: tuple[Any, ...]) -> tuple[int, ...]:
    indices: list[int] = []
    for event in events:
        index = getattr(event, "index", None)
        if index is None and isinstance(event, dict):
            index = event.get("index")
        if index is not None:
            indices.append(int(index))
    return tuple(indices)
