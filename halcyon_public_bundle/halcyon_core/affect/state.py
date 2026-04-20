"""Bounded affect state labels."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Valence(str, Enum):
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    POSITIVE = "positive"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ArousalLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AffectState:
    valence: Valence = Valence.NEUTRAL
    arousal: ArousalLevel = ArousalLevel.LOW
    labels: tuple[str, ...] = ()
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if not isinstance(self.valence, Valence):
            object.__setattr__(self, "valence", Valence(str(self.valence)))
        if not isinstance(self.arousal, ArousalLevel):
            object.__setattr__(self, "arousal", ArousalLevel(str(self.arousal)))
        object.__setattr__(self, "labels", tuple(str(label).lower() for label in self.labels))

    def to_dict(self) -> dict[str, object]:
        return {
            "valence": self.valence.value,
            "arousal": self.arousal.value,
            "labels": list(self.labels),
            "updated_at": self.updated_at,
        }
