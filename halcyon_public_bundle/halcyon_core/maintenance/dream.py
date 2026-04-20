"""Speculative synthesis artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DreamArtifact:
    content: str
    reason_codes: tuple[str, ...]
    evidence: tuple[str, ...]
    source_events: tuple[int, ...] = ()
    speculative: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.speculative:
            raise ValueError("dream artifacts must be speculative")
        if not self.reason_codes:
            raise ValueError("dream artifact requires reason_codes")
        if not self.evidence:
            raise ValueError("dream artifact requires evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "reason_codes": list(self.reason_codes),
            "evidence": list(self.evidence),
            "source_events": list(self.source_events),
            "speculative": self.speculative,
            "metadata": self.metadata,
        }


def create_dream_artifact(
    content: str,
    *,
    source_events: tuple[int, ...] = (),
    evidence: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> DreamArtifact:
    return DreamArtifact(
        content=content,
        reason_codes=("dream_artifact", "speculative_synthesis"),
        evidence=evidence or ("speculative synthesis",),
        source_events=source_events,
        metadata=metadata or {},
    )


def synthesize() -> dict:
    return create_dream_artifact("No synthesis requested.").to_dict()
