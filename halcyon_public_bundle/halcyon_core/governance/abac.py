"""Agent Boundary Audit Chain MVP primitives.

ABAC governs external mutation boundaries. The MVP packages intended actions,
binds them to stable digests, and refuses by default unless explicit approval is
present before the declared commit point.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from halcyon_core.events import canonical_json, digest_payload

ConditionState = Literal["pass", "partial", "fail", "unknown"]
GateStatus = Literal["approve", "refuse", "halt", "downgrade"]
MutationClass = Literal["internal", "external", "durable_memory", "network", "filesystem"]
CommitThreshold = Literal[
    "reversible",
    "conditionally_reversible",
    "externally_observable",
    "economically_irreversible",
    "physically_irreversible",
    "unknown",
]


@dataclass(frozen=True)
class ActionPackage:
    action_type: str
    target: str
    payload: dict[str, Any]
    mutation_class: MutationClass = "external"
    actor: str = "runtime"
    invariant_digest: str | None = None
    irreversible_primitive: str | None = None
    commit_threshold: CommitThreshold = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    def canonical_json(self) -> str:
        return canonical_json(self.to_payload())

    def digest(self) -> str:
        return digest_payload(self.to_payload())


@dataclass(frozen=True)
class GateDecision:
    status: GateStatus
    reason: str
    action_digest: str
    condition_summary: dict[str, ConditionState]
    approval_digest: str | None = None
    refusal_capacity: bool = True


@dataclass(frozen=True)
class ApprovalRecord:
    action_digest: str
    approved_by: str
    approval_basis: str

    def digest(self) -> str:
        return digest_payload(asdict(self))


def approval_for(action: ActionPackage, *, approved_by: str, approval_basis: str) -> ApprovalRecord:
    return ApprovalRecord(
        action_digest=action.digest(),
        approved_by=approved_by,
        approval_basis=approval_basis,
    )


def refuse_by_default(action: ActionPackage) -> GateDecision:
    return GateDecision(
        status="refuse",
        reason="ABAC deny-by-default MVP",
        action_digest=action.digest(),
        condition_summary={"C1": "pass", "C3": "partial", "C4": "partial", "C6": "pass"},
        refusal_capacity=True,
    )


def evaluate_action(
    action: ActionPackage,
    *,
    approval: ApprovalRecord | None = None,
    refusal_capacity: bool = True,
) -> GateDecision:
    action_digest = action.digest()

    if not refusal_capacity:
        return GateDecision(
            status="halt",
            reason="refusal capacity absent at ABAC gate",
            action_digest=action_digest,
            condition_summary={"C1": "fail", "C3": "unknown", "C4": "unknown", "C6": "fail"},
            refusal_capacity=False,
        )

    if approval is None:
        return refuse_by_default(action)

    if approval.action_digest != action_digest:
        return GateDecision(
            status="halt",
            reason="approval digest does not match action digest",
            action_digest=action_digest,
            condition_summary={"C1": "pass", "C3": "fail", "C4": "partial", "C6": "fail"},
            approval_digest=approval.digest(),
            refusal_capacity=True,
        )

    reversible_declared = action.commit_threshold in {"reversible", "conditionally_reversible"}
    c3: ConditionState = "pass" if (action.irreversible_primitive or reversible_declared) else "partial"
    c4: ConditionState = "pass" if action.commit_threshold != "unknown" else "partial"
    status: GateStatus = "approve" if c3 == "pass" and c4 == "pass" else "downgrade"
    c6: ConditionState = "pass" if status == "approve" else "partial"
    reason = "approved before declared commit point" if status == "approve" else "approval present but commit topology is incomplete"

    return GateDecision(
        status=status,
        reason=reason,
        action_digest=action_digest,
        condition_summary={"C1": "pass", "C3": c3, "C4": c4, "C6": c6},
        approval_digest=approval.digest(),
        refusal_capacity=True,
    )
