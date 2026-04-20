"""Coordinator seam for ledger-required mutation paths.

This is the first authority seam, not the final authority boundary. It owns the
current API approval flows so routes can delegate mutation decisions while lower
level direct writes remain explicitly classified as bypass risks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from halcyon_core.config import RuntimeConfig
from halcyon_core.events import EventBus, RuntimeEvent, SQLiteEventLedger
from halcyon_core.governance.abac import ApprovalRecord
from halcyon_core.governance.event_schema import MUTATION_EVENT_SCHEMA_VERSION
from halcyon_core.governance.invariants import CanonicalInvariant, default_invariant
from halcyon_core.governance.mutation_context import mutation_context
from halcyon_core.memory.proposals import MemoryProposal, SQLiteMemoryProposalQueue
from halcyon_core.memory.store import SQLiteMemoryStore
from halcyon_core.tools.executor import ToolExecutor
from halcyon_core.tools.registry import ToolRegistry
from halcyon_core.tools.types import ToolCall, ToolIntentBinding, ToolResult, ToolResultStatus


class MutationCoordinatorError(Exception):
    """Base class for coordinator-level mutation failures."""


class MutationNotFoundError(MutationCoordinatorError):
    pass


class MutationConflictError(MutationCoordinatorError):
    pass


class MutationUnprocessableError(MutationCoordinatorError):
    pass


class ToolExecutionRefused(MutationCoordinatorError):
    def __init__(self, result: ToolResult) -> None:
        super().__init__(result.reason)
        self.result = result


class ToolIntentBindingRequired(MutationCoordinatorError):
    def __init__(self, binding: ToolIntentBinding) -> None:
        super().__init__("tool proposal is not bound to interpreted user intent")
        self.binding = binding


@dataclass(frozen=True)
class MemoryApprovalResult:
    proposal_id: str
    memory_id: int
    approval_event: RuntimeEvent
    durable_event: RuntimeEvent


@dataclass(frozen=True)
class MemoryRejectionResult:
    proposal: MemoryProposal
    rejection_event: RuntimeEvent


@dataclass(frozen=True)
class ToolApprovalResult:
    call_digest: str
    tool_name: str
    approved_by: str
    output: dict[str, Any]
    gate_decision: Any | None
    approval_event: RuntimeEvent
    execution_event: RuntimeEvent


class MutationCoordinator:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        tool_registry: ToolRegistry | None = None,
        invariant: CanonicalInvariant | None = None,
    ) -> None:
        self.config = config
        self.tool_registry = tool_registry or ToolRegistry()
        self.invariant = invariant or default_invariant()

    def approve_memory_proposal(
        self,
        proposal_id: str,
        *,
        decided_by: str,
        decision_reason: str = "approved",
    ) -> MemoryApprovalResult:
        queue = SQLiteMemoryProposalQueue(self.config.proposal_database_path())
        store = SQLiteMemoryStore(self.config.memory_database_path())
        ledger = SQLiteEventLedger(self.config.events_database_path())
        bus = EventBus(ledger=ledger)
        try:
            proposal = queue.get(proposal_id)
            if proposal is None:
                raise MutationNotFoundError("proposal not found")
            if proposal.status != "pending":
                raise MutationConflictError(f"proposal is already {proposal.status}")

            approval_event = bus.append(
                "memory.proposal.approved",
                parent_index=proposal.event_index,
                schema_version=MUTATION_EVENT_SCHEMA_VERSION,
                proposal_id=proposal_id,
                decided_by=decided_by,
                decision_reason=decision_reason,
                proposal_event_index=proposal.event_index,
                source_events=tuple(event for event in (proposal.event_index,) if event is not None),
                reason_codes=("memory_proposal_approved",),
                evidence={
                    "proposal_id": proposal_id,
                    "proposal_digest": proposal.digest(),
                },
            )
            with mutation_context(
                actor="mutation_coordinator",
                operation="memory_proposal_approval",
                evidence=(f"proposal_id={proposal_id}", f"approval_event_index={approval_event.index}"),
            ):
                memory = queue.approve(
                    proposal_id,
                    store,
                    decided_by=decided_by,
                    decision_reason=decision_reason,
                )
            durable_event = bus.append(
                "memory.durable.created",
                parent_index=approval_event.index,
                schema_version=MUTATION_EVENT_SCHEMA_VERSION,
                proposal_id=proposal_id,
                memory_id=memory.memory_id,
                decided_by=decided_by,
                decision_reason=decision_reason,
                proposal_event_index=proposal.event_index,
                source_events=tuple(
                    event for event in (proposal.event_index, approval_event.index) if event is not None
                ),
                reason_codes=("approved_memory_written",),
                evidence={
                    "proposal_id": proposal_id,
                    "approval_event_index": approval_event.index,
                    "content_digest": memory.content_digest,
                },
            )
            if memory.memory_id is None:
                raise MutationCoordinatorError("durable memory write did not return an id")
            return MemoryApprovalResult(
                proposal_id=proposal_id,
                memory_id=memory.memory_id,
                approval_event=approval_event,
                durable_event=durable_event,
            )
        except ValueError as exc:
            raise MutationConflictError(str(exc)) from exc
        finally:
            queue.close()
            store.close()
            ledger.close()

    def reject_memory_proposal(
        self,
        proposal_id: str,
        *,
        decided_by: str,
        decision_reason: str = "rejected",
    ) -> MemoryRejectionResult:
        queue = SQLiteMemoryProposalQueue(self.config.proposal_database_path())
        ledger = SQLiteEventLedger(self.config.events_database_path())
        bus = EventBus(ledger=ledger)
        try:
            proposal_before = queue.get(proposal_id)
            if proposal_before is None:
                raise MutationNotFoundError("proposal not found")
            if proposal_before.status != "pending":
                raise MutationConflictError(f"proposal is already {proposal_before.status}")

            rejection_event = bus.append(
                "memory.proposal.rejected",
                parent_index=proposal_before.event_index,
                schema_version=MUTATION_EVENT_SCHEMA_VERSION,
                proposal_id=proposal_id,
                decided_by=decided_by,
                decision_reason=decision_reason,
                proposal_event_index=proposal_before.event_index,
                source_events=tuple(event for event in (proposal_before.event_index,) if event is not None),
                reason_codes=("memory_proposal_rejected",),
                evidence={
                    "proposal_id": proposal_id,
                    "proposal_digest": proposal_before.digest(),
                },
            )
            with mutation_context(
                actor="mutation_coordinator",
                operation="memory_proposal_rejection",
                evidence=(f"proposal_id={proposal_id}", f"rejection_event_index={rejection_event.index}"),
            ):
                proposal = queue.reject(
                    proposal_id,
                    decided_by=decided_by,
                    decision_reason=decision_reason,
                )
            return MemoryRejectionResult(proposal=proposal, rejection_event=rejection_event)
        except ValueError as exc:
            raise MutationConflictError(str(exc)) from exc
        finally:
            queue.close()
            ledger.close()

    def approve_tool_proposal(
        self,
        call_digest: str,
        *,
        approved_by: str,
        approval_basis: str = "user_direct_consent",
    ) -> ToolApprovalResult:
        ledger = SQLiteEventLedger(self.config.events_database_path())
        bus = EventBus(ledger=ledger)
        try:
            proposal_event = None
            tool_call_payload = None
            for event in ledger.iter_events_by_name("tool.proposal.evaluated"):
                if event.payload.get("tool_result", {}).get("call_digest") == call_digest:
                    proposal_event = event
                    tool_call_payload = event.payload.get("tool_call")
                    break

            if proposal_event is None or tool_call_payload is None:
                raise MutationNotFoundError("tool proposal not found")

            call = ToolCall(**tool_call_payload)
            binding = ToolIntentBinding.from_payload(proposal_event.payload.get("intent_binding"))
            override_event = None
            if binding.status != "bound":
                if approval_basis != "intent_override_accepted":
                    raise ToolIntentBindingRequired(binding)
                override_event = bus.append(
                    "tool.intent_override.accepted",
                    parent_index=proposal_event.index,
                    schema_version=MUTATION_EVENT_SCHEMA_VERSION,
                    call_digest=call_digest,
                    tool_name=call.tool_name,
                    proposal_event_index=proposal_event.index,
                    source_events=(proposal_event.index,),
                    reason_codes=("explicit_intent_override",),
                    evidence={
                        "binding": binding.to_dict(),
                        "approved_by": approved_by,
                        "approval_basis": approval_basis,
                    },
                )

            binding_parent_index = override_event.index if override_event else proposal_event.index
            binding_source_events = tuple(
                event for event in (proposal_event.index, override_event.index if override_event else None)
                if event is not None
            )
            binding_evaluated_event = bus.append(
                "tool.intent_binding.evaluated",
                parent_index=binding_parent_index,
                schema_version=MUTATION_EVENT_SCHEMA_VERSION,
                call_digest=call_digest,
                tool_name=call.tool_name,
                binding_status=binding.status,
                override_applied=override_event is not None,
                proposal_event_index=proposal_event.index,
                user_event_index=binding.user_event_index,
                interpretation_event_index=binding.interpretation_event_index,
                source_events=binding_source_events,
                reason_codes=list(binding.reason_codes),
                evidence=list(binding.evidence),
            )

            executor = ToolExecutor(self.tool_registry, invariant=self.invariant)
            action = executor.action_for_call(call)
            if action is None:
                raise MutationUnprocessableError("tool not registered in current runtime")

            approval = ApprovalRecord(
                action_digest=action.digest(),
                approved_by=approved_by,
                approval_basis=approval_basis,
            )
            approval_digest = approval.digest()
            source_events = tuple(
                event for event in (
                    proposal_event.index,
                    override_event.index if override_event else None,
                    binding_evaluated_event.index,
                )
                if event is not None
            )
            approval_event = bus.append(
                "tool.approval.granted",
                parent_index=binding_evaluated_event.index,
                schema_version=MUTATION_EVENT_SCHEMA_VERSION,
                call_digest=call_digest,
                tool_name=call.tool_name,
                action_digest=action.digest(),
                approval_digest=approval_digest,
                approved_by=approved_by,
                approval_basis=approval_basis,
                proposal_event_index=proposal_event.index,
                intent_binding=binding.to_dict(),
                source_events=source_events,
                reason_codes=("explicit_tool_approval",),
                evidence={"approval_record": approval.__dict__, "intent_binding": binding.to_dict()},
            )
            with mutation_context(
                actor="mutation_coordinator",
                operation="tool_callable_execution",
                evidence=(f"call_digest={call_digest}", f"approval_event_index={approval_event.index}"),
            ):
                result = executor.evaluate(call, approval=approval)

            if result.status == ToolResultStatus.EXECUTED:
                execution_event = bus.append(
                    "tool.execution.completed",
                    parent_index=approval_event.index,
                    schema_version=MUTATION_EVENT_SCHEMA_VERSION,
                    call_digest=call_digest,
                    tool_name=call.tool_name,
                    action_digest=action.digest(),
                    approval_digest=approval_digest,
                    proposal_event_index=proposal_event.index,
                    source_events=tuple(
                        event
                        for event in (proposal_event.index, override_event.index if override_event else None, approval_event.index)
                        if event is not None
                    ),
                    approved_by=approved_by,
                    approval_basis=approval_basis,
                    reason_codes=(result.reason_code,),
                    evidence={"approval_event_index": approval_event.index},
                    output=result.output,
                )
                return ToolApprovalResult(
                    call_digest=call_digest,
                    tool_name=call.tool_name,
                    approved_by=approved_by,
                    output=result.output,
                    gate_decision=result.gate_decision,
                    approval_event=approval_event,
                    execution_event=execution_event,
                )

            bus.append(
                "tool.execution.refused",
                parent_index=approval_event.index,
                schema_version=MUTATION_EVENT_SCHEMA_VERSION,
                call_digest=call_digest,
                tool_name=call.tool_name,
                action_digest=action.digest(),
                approval_digest=approval_digest,
                proposal_event_index=proposal_event.index,
                source_events=(proposal_event.index, approval_event.index),
                reason_code=result.reason_code,
                reason=result.reason,
                reason_codes=(result.reason_code,),
                evidence={"approval_event_index": approval_event.index},
                approved_by=approved_by,
            )
            raise ToolExecutionRefused(result=result) from None
        finally:
            ledger.close()
