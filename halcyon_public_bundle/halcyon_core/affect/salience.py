"""Deterministic salience metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SalienceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SalienceSource(str, Enum):
    USER_MESSAGE = "user_message"
    MODEL_RESPONSE = "model_response"
    MEMORY_PROPOSAL = "memory_proposal"
    INTERPRETATION = "interpretation"
    TOOL_RESULT = "tool_result"
    GOVERNANCE = "governance"
    LEGACY_IMPORT = "legacy_import"


class ImportanceKind(str, Enum):
    NONE = "none"
    USER_DECLARED = "user_declared"
    SYSTEM_OBSERVED = "system_observed"


_ORDER = {
    SalienceLevel.LOW: 0,
    SalienceLevel.MEDIUM: 1,
    SalienceLevel.HIGH: 2,
    SalienceLevel.CRITICAL: 3,
}


@dataclass(frozen=True)
class SalienceSignal:
    level: SalienceLevel = SalienceLevel.LOW
    source: SalienceSource = SalienceSource.USER_MESSAGE
    importance_kind: ImportanceKind = ImportanceKind.NONE
    reason_codes: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.level, SalienceLevel):
            object.__setattr__(self, "level", SalienceLevel(str(self.level)))
        if not isinstance(self.source, SalienceSource):
            object.__setattr__(self, "source", SalienceSource(str(self.source)))
        if not isinstance(self.importance_kind, ImportanceKind):
            object.__setattr__(self, "importance_kind", ImportanceKind(str(self.importance_kind)))
        object.__setattr__(self, "reason_codes", tuple(str(code) for code in self.reason_codes))
        object.__setattr__(self, "evidence", tuple(str(item) for item in self.evidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "source": self.source.value,
            "importance_kind": self.importance_kind.value,
            "reason_codes": list(self.reason_codes),
            "evidence": list(self.evidence),
            "metadata": self.metadata,
        }


def evaluate_salience(
    text: str = "",
    *,
    source: SalienceSource = SalienceSource.USER_MESSAGE,
    model_memory_proposal: bool = False,
    interpretation_status: str | None = None,
    tool_result_status: str | None = None,
    governance_status: str | None = None,
    durable_memory_requested: bool = False,
    destructive_action_requested: bool = False,
    refusal_capacity_absent: bool = False,
    metadata: dict[str, Any] | None = None,
) -> SalienceSignal:
    normalized = " ".join(text.lower().split())
    level = SalienceLevel.LOW
    importance = ImportanceKind.NONE
    reasons: list[str] = []
    evidence: list[str] = []

    if any(marker in normalized for marker in ("useful", "preference", "context")):
        level = _raise(level, SalienceLevel.MEDIUM)
        importance = _prefer_importance(importance, ImportanceKind.USER_DECLARED)
        reasons.append("user_context_marker")
        evidence.append("useful/preference/context marker")

    if model_memory_proposal:
        level = _raise(level, SalienceLevel.MEDIUM)
        importance = _prefer_importance(importance, ImportanceKind.SYSTEM_OBSERVED)
        reasons.append("model_memory_proposal")
        evidence.append("model proposed memory")

    if interpretation_status == "needs_clarification":
        level = _raise(level, SalienceLevel.MEDIUM)
        importance = _prefer_importance(importance, ImportanceKind.SYSTEM_OBSERVED)
        reasons.append("interpretation_needs_clarification")
        evidence.append("interpretation requested clarification")

    if tool_result_status in {"unknown_tool", "disabled"}:
        level = _raise(level, SalienceLevel.MEDIUM)
        importance = _prefer_importance(importance, ImportanceKind.SYSTEM_OBSERVED)
        reasons.append(f"tool_{tool_result_status}")
        evidence.append(f"tool result status {tool_result_status}")

    if any(marker in normalized for marker in ("important", "remember", "don't forget", "do not forget")):
        level = _raise(level, SalienceLevel.HIGH)
        importance = _prefer_importance(importance, ImportanceKind.USER_DECLARED)
        reasons.append("user_declared_importance")
        evidence.append("important/remember/do-not-forget marker")

    if durable_memory_requested:
        level = _raise(level, SalienceLevel.HIGH)
        importance = _prefer_importance(importance, ImportanceKind.SYSTEM_OBSERVED)
        reasons.append("durable_memory_requested")
        evidence.append("durable memory requested")

    if interpretation_status == "needs_confirmation":
        level = _raise(level, SalienceLevel.HIGH)
        importance = _prefer_importance(importance, ImportanceKind.SYSTEM_OBSERVED)
        reasons.append("interpretation_needs_confirmation")
        evidence.append("interpretation requested confirmation")

    if tool_result_status == "refused":
        level = _raise(level, SalienceLevel.HIGH)
        importance = _prefer_importance(importance, ImportanceKind.SYSTEM_OBSERVED)
        reasons.append("tool_refused")
        evidence.append("tool result refused")

    if governance_status in {"downgrade", "refuse", "halt"}:
        level = _raise(level, SalienceLevel.HIGH)
        importance = _prefer_importance(importance, ImportanceKind.SYSTEM_OBSERVED)
        reasons.append(f"governance_{governance_status}")
        evidence.append(f"governance status {governance_status}")

    if destructive_action_requested:
        level = _raise(level, SalienceLevel.CRITICAL)
        importance = _prefer_importance(importance, ImportanceKind.SYSTEM_OBSERVED)
        reasons.append("destructive_action_requested")
        evidence.append("destructive action requested")

    if refusal_capacity_absent:
        level = _raise(level, SalienceLevel.CRITICAL)
        importance = _prefer_importance(importance, ImportanceKind.SYSTEM_OBSERVED)
        reasons.append("refusal_capacity_absent")
        evidence.append("refusal capacity absent")

    if any(marker in normalized for marker in ("private", "legal", "personal")):
        level = _raise(level, SalienceLevel.CRITICAL)
        importance = _prefer_importance(importance, ImportanceKind.USER_DECLARED)
        reasons.append("sensitivity_marker")
        evidence.append("private/legal/personal marker")

    if any(marker in normalized for marker in ("urgent", "emergency")):
        level = _raise(level, SalienceLevel.CRITICAL)
        importance = _prefer_importance(importance, ImportanceKind.USER_DECLARED)
        reasons.append("urgent_marker")
        evidence.append("urgent/emergency marker")

    return SalienceSignal(
        level=level,
        source=source,
        importance_kind=importance,
        reason_codes=tuple(dict.fromkeys(reasons)),
        evidence=tuple(dict.fromkeys(evidence)),
        metadata=metadata or {},
    )


def _raise(current: SalienceLevel, candidate: SalienceLevel) -> SalienceLevel:
    return candidate if _ORDER[candidate] > _ORDER[current] else current


def _prefer_importance(current: ImportanceKind, candidate: ImportanceKind) -> ImportanceKind:
    if current is ImportanceKind.USER_DECLARED:
        return current
    if candidate is ImportanceKind.USER_DECLARED:
        return candidate
    if current is ImportanceKind.SYSTEM_OBSERVED:
        return current
    return candidate
