"""ABAC-gated tool executor."""

from __future__ import annotations

from halcyon_core.governance.abac import ActionPackage, ApprovalRecord, evaluate_action
from halcyon_core.governance.invariants import CanonicalInvariant, default_invariant
from halcyon_core.governance.mutation_context import MutationContextRequired, require_mutation_context
from halcyon_core.tools.registry import ToolRegistry
from halcyon_core.tools.types import MutationClass, ToolCall, ToolResult, ToolResultStatus
from halcyon_core.state import RuntimePosture


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        *,
        invariant: CanonicalInvariant | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.invariant = invariant or default_invariant()

    def action_for_call(self, call: ToolCall) -> ActionPackage | None:
        spec = self.registry.get(call.tool_name)
        if spec is None:
            return None
        return ActionPackage(
            action_type=call.tool_name,
            target=call.tool_name,
            payload=call.payload,
            mutation_class=spec.mutation_class.to_abac(),
            actor=call.actor,
            invariant_digest=self.invariant.digest(),
            commit_threshold=spec.commit_threshold,
            metadata={
                "tool_call_digest": call.digest(),
                "reason": call.reason,
                "tool_mutation_class": spec.mutation_class.value,
            },
        )

    def evaluate(
        self,
        call: ToolCall,
        *,
        approval: ApprovalRecord | None = None,
        refusal_capacity: bool = True,
        posture: RuntimePosture = RuntimePosture.HEALTHY,
    ) -> ToolResult:
        spec = self.registry.get(call.tool_name)
        call_digest = call.digest()

        if posture == RuntimePosture.CRITICAL:
            return ToolResult(
                status=ToolResultStatus.REFUSED,
                call_digest=call_digest,
                reason_code="health_critical",
                reason="chain terminated by runtime governor",
            )
        if posture == RuntimePosture.SEVERE_STRESS:
            return ToolResult(
                status=ToolResultStatus.REFUSED,
                call_digest=call_digest,
                reason_code="health_frozen",
                reason="tool authority frozen due to severe stress",
            )
        if posture == RuntimePosture.MODERATE_STRESS and not approval:
            return ToolResult(
                status=ToolResultStatus.REFUSED,
                call_digest=call_digest,
                reason_code="health_approval_required",
                reason="runtime under moderate stress requires explicit tool approval",
            )

        if spec is None:
            return ToolResult(
                status=ToolResultStatus.UNKNOWN_TOOL,
                call_digest=call_digest,
                reason_code="unknown_tool",
                reason=f"tool is not registered: {call.tool_name}",
            )

        if not spec.enabled:
            return ToolResult(
                status=ToolResultStatus.DISABLED,
                call_digest=call_digest,
                reason_code="tool_disabled",
                reason=f"tool is disabled: {call.tool_name}",
            )

        if posture == RuntimePosture.CRITICAL:
            return ToolResult(
                status=ToolResultStatus.REFUSED,
                call_digest=call_digest,
                reason_code="health_critical",
                reason="chain terminated by runtime governor",
            )
        if posture == RuntimePosture.SEVERE_STRESS:
            return ToolResult(
                status=ToolResultStatus.REFUSED,
                call_digest=call_digest,
                reason_code="health_frozen",
                reason="tool authority frozen due to severe stress",
            )
        if posture == RuntimePosture.MILD_STRESS and spec.mutation_class.requires_abac():
            return ToolResult(
                status=ToolResultStatus.REFUSED,
                call_digest=call_digest,
                reason_code="health_restricted",
                reason="high-risk tool restricted during mild stress",
            )
        if posture == RuntimePosture.MODERATE_STRESS and not approval:
            return ToolResult(
                status=ToolResultStatus.REFUSED,
                call_digest=call_digest,
                reason_code="health_approval_required",
                reason="runtime under moderate stress requires explicit tool approval",
            )

        if spec.mutation_class is MutationClass.READ_ONLY and not spec.requires_approval:
            return ToolResult(
                status=ToolResultStatus.APPROVED_NOT_EXECUTED,
                call_digest=call_digest,
                reason_code="read_only_declared_no_callable_registered",
                reason="tool declared read-only but no callable is registered",
                output={"tool": spec.name},
            )

        action = self.action_for_call(call)
        assert action is not None
        decision = evaluate_action(action, approval=approval, refusal_capacity=refusal_capacity)
        if decision.status == "approve":
            fn = self.registry.get_callable(call.tool_name)
            if fn is not None:
                try:
                    require_mutation_context("tool_executor.callable_execution")
                    output = fn(**call.payload)
                    return ToolResult(
                        status=ToolResultStatus.EXECUTED,
                        call_digest=call_digest,
                        reason_code="executed",
                        reason="approved and executed",
                        output=output if isinstance(output, dict) else {"result": output},
                        gate_decision=decision,
                    )
                except MutationContextRequired as exc:
                    return ToolResult(
                        status=ToolResultStatus.REFUSED,
                        call_digest=call_digest,
                        reason_code="mutation_context_required",
                        reason=str(exc),
                        gate_decision=decision,
                    )
                except Exception as exc:
                    return ToolResult(
                        status=ToolResultStatus.ERROR,
                        call_digest=call_digest,
                        reason_code="execution_error",
                        reason=str(exc),
                        error=str(exc),
                        gate_decision=decision,
                    )
            return ToolResult(
                status=ToolResultStatus.APPROVED_NOT_EXECUTED,
                call_digest=call_digest,
                reason_code="approval_present_but_callable_missing",
                reason="tool action approved but no callable is registered",
                gate_decision=decision,
            )

        return ToolResult(
            status=ToolResultStatus.REFUSED,
            call_digest=call_digest,
            reason_code=_reason_code_for_gate(decision.reason),
            reason=decision.reason,
            gate_decision=decision,
        )


def evaluate_tool_call(
    call: ToolCall,
    registry: ToolRegistry,
    *,
    invariant: CanonicalInvariant | None = None,
    approval: ApprovalRecord | None = None,
    refusal_capacity: bool = True,
    posture: RuntimePosture = RuntimePosture.HEALTHY,
) -> ToolResult:
    return ToolExecutor(registry, invariant=invariant).evaluate(
        call, approval=approval, refusal_capacity=refusal_capacity, posture=posture
    )


def _reason_code_for_gate(reason: str) -> str:
    if "approval digest" in reason:
        return "approval_digest_mismatch"
    if "refusal capacity" in reason:
        return "refusal_capacity_absent"
    if "deny-by-default" in reason:
        return "abac_denied_by_default"
    if "commit topology" in reason:
        return "approval_present_commit_topology_incomplete"
    return "abac_refused"
