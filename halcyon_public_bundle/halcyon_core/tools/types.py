"""Tool type definitions for governed action surfaces."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal

from halcyon_core.events import canonical_json, digest_payload



ToolIntentBindingStatus = Literal["bound", "unbound", "needs_confirmation", "unknown"]


@dataclass(frozen=True)
class ToolIntentBinding:
    status: ToolIntentBindingStatus
    user_event_index: int | None
    interpretation_event_index: int | None
    tool_proposal_event_index: int | None = None
    reason_codes: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        allowed = {"bound", "unbound", "needs_confirmation", "unknown"}
        if self.status not in allowed:
            raise ValueError(f"unknown tool intent binding status: {self.status}")
        object.__setattr__(self, "reason_codes", tuple(str(code) for code in self.reason_codes))
        object.__setattr__(self, "evidence", tuple(str(item) for item in self.evidence))
        if not self.reason_codes:
            raise ValueError("tool intent binding requires reason_codes")
        if not self.evidence:
            raise ValueError("tool intent binding requires evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "user_event_index": self.user_event_index,
            "interpretation_event_index": self.interpretation_event_index,
            "tool_proposal_event_index": self.tool_proposal_event_index,
            "reason_codes": list(self.reason_codes),
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "ToolIntentBinding":
        if not payload:
            return cls(
                status="unknown",
                user_event_index=None,
                interpretation_event_index=None,
                reason_codes=("intent_binding_missing",),
                evidence=("tool proposal event did not include intent binding",),
            )
        return cls(
            status=str(payload.get("status", "unknown")),
            user_event_index=payload.get("user_event_index"),
            interpretation_event_index=payload.get("interpretation_event_index"),
            tool_proposal_event_index=payload.get("tool_proposal_event_index"),
            reason_codes=tuple(payload.get("reason_codes", ())),
            evidence=tuple(payload.get("evidence", ())),
        )


class MutationClass(str, Enum):
    READ_ONLY = "read_only"
    MEMORY = "memory"
    FILESYSTEM = "filesystem"
    NETWORK = "network"
    PROCESS = "process"
    EXTERNAL = "external"

    def requires_abac(self) -> bool:
        return self is not MutationClass.READ_ONLY

    def to_abac(self) -> str:
        if self is MutationClass.MEMORY:
            return "durable_memory"
        if self in {MutationClass.FILESYSTEM, MutationClass.NETWORK}:
            return self.value
        return "external"


class ToolResultStatus(str, Enum):
    REFUSED = "refused"
    UNKNOWN_TOOL = "unknown_tool"
    DISABLED = "disabled"
    APPROVED_NOT_EXECUTED = "approved_not_executed"
    EXECUTED = "executed"
    ERROR = "error"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    mutation_class: MutationClass = MutationClass.EXTERNAL
    requires_approval: bool = True
    enabled: bool = True
    commit_threshold: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tool name is required")
        if not isinstance(self.mutation_class, MutationClass):
            object.__setattr__(self, "mutation_class", MutationClass(str(self.mutation_class)))

    def to_payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["mutation_class"] = self.mutation_class.value
        return data


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    payload: dict[str, Any] = field(default_factory=dict)
    actor: str = "model_adapter"
    reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    def canonical_json(self) -> str:
        return canonical_json(self.to_payload())

    def digest(self) -> str:
        return digest_payload(self.to_payload())


@dataclass(frozen=True)
class ToolResult:
    status: ToolResultStatus
    call_digest: str
    reason_code: str
    reason: str = ""
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    gate_decision: Any | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ToolResultStatus):
            object.__setattr__(self, "status", ToolResultStatus(str(self.status)))
        if not self.reason_code.strip():
            raise ValueError("tool result reason_code is required")

    def to_dict(self) -> dict[str, Any]:
        gate = self.gate_decision
        if hasattr(gate, "__dict__"):
            gate = gate.__dict__
        return {
            "status": self.status.value,
            "call_digest": self.call_digest,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "output": self.output,
            "error": self.error,
            "gate_decision": gate,
        }
