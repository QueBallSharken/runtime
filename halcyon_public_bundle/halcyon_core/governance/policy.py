"""Deny-by-default policy helpers."""

from __future__ import annotations

from halcyon_core.governance.abac import ActionPackage, ApprovalRecord, GateDecision, evaluate_action


def external_mutation_allowed(explicit_approval: bool) -> bool:
    return bool(explicit_approval)


def decide_external_mutation(
    action: ActionPackage,
    *,
    approval: ApprovalRecord | None = None,
    refusal_capacity: bool = True,
) -> GateDecision:
    return evaluate_action(action, approval=approval, refusal_capacity=refusal_capacity)
