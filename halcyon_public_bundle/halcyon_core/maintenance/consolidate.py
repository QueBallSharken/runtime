"""Manual-first memory consolidation candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ConsolidationCandidate:
    summary: str
    reason_codes: tuple[str, ...]
    evidence: tuple[str, ...]
    source_events: tuple[int, ...] = ()
    speculative: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.speculative:
            raise ValueError("consolidation candidates must be speculative")
        if not self.reason_codes:
            raise ValueError("consolidation candidate requires reason_codes")
        if not self.evidence:
            raise ValueError("consolidation candidate requires evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "reason_codes": list(self.reason_codes),
            "evidence": list(self.evidence),
            "source_events": list(self.source_events),
            "speculative": self.speculative,
            "metadata": self.metadata,
        }


def create_consolidation_candidate(
    summary: str,
    *,
    source_events: tuple[int, ...] = (),
    evidence: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> ConsolidationCandidate:
    return ConsolidationCandidate(
        summary=summary,
        reason_codes=("consolidation_candidate",),
        evidence=evidence or ("manual consolidation candidate",),
        source_events=source_events,
        metadata=metadata or {},
    )


def consolidate_recent() -> list[dict]:
    return []
