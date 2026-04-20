"""Types for the interpretation and binding gate."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class AmbiguityType(str, Enum):
    REGISTER_COLLAPSE = "register_collapse"
    FRAMING_HIJACK = "framing_hijack"
    ASSUMED_CONTEXT = "assumed_context"
    SCOPE_CREEP = "scope_creep"
    LEXICAL_DRIFT = "lexical_drift"
    REFERENT_AMBIGUITY = "referent_ambiguity"
    NONE = "none"
    UNKNOWN = "unknown"


class InterpretationStatus(str, Enum):
    STABLE = "stable"
    NEEDS_CLARIFICATION = "needs_clarification"
    NEEDS_CONFIRMATION = "needs_confirmation"
    UNSTABLE = "unstable"
    PROVIDER_MISSING = "provider_missing"


class ConsequenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class CandidateBinding:
    text: str
    evidence: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BindingRisk:
    unclear_referent: bool = False
    lexical_drift: bool = False
    frame_collision: bool = False
    scope_uncertainty: bool = False
    evidence: tuple[str, ...] = ()

    def has_any(self) -> bool:
        return any((
            self.unclear_referent,
            self.lexical_drift,
            self.frame_collision,
            self.scope_uncertainty,
        ))

    def ambiguity_types(self) -> tuple[AmbiguityType, ...]:
        types: list[AmbiguityType] = []
        if self.unclear_referent:
            types.append(AmbiguityType.REFERENT_AMBIGUITY)
        if self.lexical_drift:
            types.append(AmbiguityType.LEXICAL_DRIFT)
        if self.frame_collision:
            types.append(AmbiguityType.FRAMING_HIJACK)
        if self.scope_uncertainty:
            types.append(AmbiguityType.SCOPE_CREEP)
        return tuple(types) or (AmbiguityType.NONE,)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CommitmentRisk:
    mutation_requested: bool = False
    durable_memory_requested: bool = False
    external_action_requested: bool = False
    destructive_action_requested: bool = False
    approval_required: bool = False
    consequence_level: ConsequenceLevel = ConsequenceLevel.LOW
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["consequence_level"] = self.consequence_level.value
        return data


@dataclass(frozen=True)
class InterpretationArtifact:
    status: InterpretationStatus
    interpreted_intent: str
    ambiguity_types: tuple[AmbiguityType, ...]
    binding_risk: BindingRisk
    commitment_risk: CommitmentRisk
    candidate_bindings: tuple[CandidateBinding, ...] = ()
    clarification_prompt: str | None = None
    confirmation_prompt: str | None = None

    def allows_response(self) -> bool:
        return self.status in {
            InterpretationStatus.STABLE,
            InterpretationStatus.NEEDS_CONFIRMATION,
        }

    def allows_proposals(self) -> bool:
        return self.status == InterpretationStatus.STABLE

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "interpreted_intent": self.interpreted_intent,
            "ambiguity_types": [item.value for item in self.ambiguity_types],
            "binding_risk": self.binding_risk.to_dict(),
            "commitment_risk": self.commitment_risk.to_dict(),
            "candidate_bindings": [item.to_dict() for item in self.candidate_bindings],
            "clarification_prompt": self.clarification_prompt,
            "confirmation_prompt": self.confirmation_prompt,
        }
