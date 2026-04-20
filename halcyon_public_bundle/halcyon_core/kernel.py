"""Top-level governed runtime kernel.

The MVL kernel connects the already-tested layers into one closed loop without
adding autonomy: user input, event ledger, CBAC-lite prompt context, memory
retrieval, local model call, proposal capture, ABAC refusal, and final claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from pathlib import Path
from typing import Any

from halcyon_core.affect import SalienceSource, evaluate_salience
from halcyon_core.config import RuntimeConfig
from halcyon_core.events import EventBus, SQLiteEventLedger, RuntimeEvent
from halcyon_core.governance.abac import GateDecision
from halcyon_core.governance.cbac import create_envelope, evaluate_handoff
from halcyon_core.governance.claims import ClaimClassifier, ClaimEvidence, FinalClaim
from halcyon_core.governance.invariants import CanonicalInvariant, default_invariant
from halcyon_core.governance.mutation_context import mutation_context
from halcyon_core.governance.health import evaluate_health, SBACEvidence, RuntimeGovernor
from halcyon_core.governance.scope import runtime_turn_scope_limitations
from halcyon_core.interpretation import InterpretationArtifact, InterpretationStatus, evaluate_interpretation
from halcyon_core.memory.proposals import (
    MemoryProposal,
    MemoryProposalQueue,
    ProposalQueue,
    SQLiteMemoryProposalQueue,
    propose_memory,
)
from halcyon_core.memory.retrieval import render_context, retrieve_context
from halcyon_core.memory.store import SQLiteMemoryStore
from halcyon_core.model.client import LocalModelClient, ModelResponse
from halcyon_core.model.prompts import build_messages, build_system_prompt
from halcyon_core.state import RuntimeState
from halcyon_core.tools.executor import ToolExecutor
from halcyon_core.tools.registry import ToolRegistry
from halcyon_core.tools.types import ToolCall, ToolIntentBinding, ToolResult, ToolResultStatus


@dataclass(frozen=True)
class RuntimeTurn:
    reply: str
    user_event: RuntimeEvent
    model_event: RuntimeEvent | None
    claim_event: RuntimeEvent
    final_claim: FinalClaim
    memory_proposals: tuple[MemoryProposal, ...] = ()
    tool_decisions: tuple[GateDecision, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    context_memory_count: int = 0
    model_warnings: tuple[str, ...] = ()
    interpretation: InterpretationArtifact | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "user_event": self.user_event.to_record(),
            "model_event": self.model_event.to_record() if self.model_event else None,
            "claim_event": self.claim_event.to_record(),
            "final_claim": self.final_claim.to_dict(),
            "memory_proposals": [proposal.__dict__ for proposal in self.memory_proposals],
            "tool_decisions": [decision.__dict__ for decision in self.tool_decisions],
            "tool_results": [result.to_dict() for result in self.tool_results],
            "context_memory_count": self.context_memory_count,
            "model_warnings": list(self.model_warnings),
            "interpretation": self.interpretation.to_dict() if self.interpretation else None,
        }


class RuntimeKernel:
    def __init__(
        self,
        config: RuntimeConfig | None = None,
        *,
        events: EventBus | None = None,
        memory_store: SQLiteMemoryStore | None = None,
        model_client: LocalModelClient | None = None,
        proposal_queue: ProposalQueue | None = None,
        invariant: CanonicalInvariant | None = None,
        interpretation_gate=evaluate_interpretation,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.config = config or RuntimeConfig()
        self.ledger = None if events else SQLiteEventLedger(self.config.events_database_path())
        self.events = events or EventBus(ledger=self.ledger)
        self.memory = memory_store or SQLiteMemoryStore(self.config.memory_database_path())
        self.model = model_client or LocalModelClient(self.config.model_base_url, self.config.model_name)
        self.proposal_store = None if proposal_queue else SQLiteMemoryProposalQueue(self.config.proposal_database_path())
        self.proposals = proposal_queue or self.proposal_store
        self.state = RuntimeState()
        self.invariant = invariant or default_invariant()
        self.claims = ClaimClassifier()
        self.interpretation_gate = interpretation_gate
        self.tool_registry = tool_registry or ToolRegistry()
        self.tool_executor = ToolExecutor(self.tool_registry, invariant=self.invariant)
        self.governor = RuntimeGovernor()

    def handle_message(self, message: str, *, memory_tags: tuple[str, ...] = ()) -> RuntimeTurn:
        user_salience = evaluate_salience(message, source=SalienceSource.USER_MESSAGE)
        user_event = self.events.append("user.message.received", text=message, salience=user_salience.to_dict())

        envelope = create_envelope(
            "runtime_kernel",
            "model_adapter",
            self.invariant,
            identity_evidence="process_bound",
        )
        cbac = evaluate_handoff(envelope, self.invariant.digest())
        self.events.append(
            "governance.cbac.evaluated",
            boundary_id=envelope.payload_digest,
            condition_summary=cbac.condition_summary,
            claim_cap=cbac.claim_cap,
            reasons=cbac.reasons,
        )

        context_items = retrieve_context(self.memory, tags=memory_tags)
        memory_context = render_context(context_items)
        interpretation = self.interpretation_gate(message, context_text=memory_context)
        interpretation_event = self.events.append(
            "interpretation.evaluated",
            parent_index=user_event.index,
            artifact=interpretation.to_dict(),
            salience=evaluate_salience(
                source=SalienceSource.INTERPRETATION,
                interpretation_status=interpretation.status.value,
                durable_memory_requested=interpretation.commitment_risk.durable_memory_requested,
                destructive_action_requested=interpretation.commitment_risk.destructive_action_requested,
            ).to_dict(),
        )
        if interpretation.status == InterpretationStatus.NEEDS_CLARIFICATION:
            return self._return_interpretation_stop(
                reply=interpretation.clarification_prompt or "I need clarification before I proceed.",
                user_event=user_event,
                interpretation_event=interpretation_event,
                interpretation=interpretation,
                context_memory_count=len(context_items),
                event_name="interpretation.clarification_requested",
            )
        if interpretation.status == InterpretationStatus.NEEDS_CONFIRMATION:
            return self._return_interpretation_stop(
                reply=interpretation.confirmation_prompt or "I need confirmation before I proceed.",
                user_event=user_event,
                interpretation_event=interpretation_event,
                interpretation=interpretation,
                context_memory_count=len(context_items),
                event_name="interpretation.confirmation_requested",
            )

        system_prompt = build_system_prompt(invariant=self.invariant)
        messages = build_messages(system_prompt, message, memory_context=memory_context)

        response = self.model.complete(messages)
        model_event = self.events.append(
            "model.response.received",
            parent_index=user_event.index,
            reply=response.text,
            raw=response.raw,
            parse_warnings=response.intent.parse_warnings,
        )

        memory_proposals = self._capture_memory_proposals(response, model_event.index)
        tool_results = self._evaluate_tool_proposals(
            response,
            model_event.index,
            user_event_index=user_event.index,
            interpretation_event_index=interpretation_event.index,
            interpretation=interpretation,
        )
        tool_decisions = tuple(result.gate_decision for result in tool_results if result.gate_decision is not None)
        final_claim = self._final_claim(cbac.condition_summary, tool_decisions, tool_results)
        claim_event = self.events.append(
            "runtime.claim.emitted",
            parent_index=model_event.index,
            claim=final_claim.to_dict(),
        )
        self.state.health.last_claim = final_claim.classification

        return RuntimeTurn(
            reply=response.text,
            user_event=user_event,
            model_event=model_event,
            claim_event=claim_event,
            final_claim=final_claim,
            memory_proposals=memory_proposals,
            tool_decisions=tool_decisions,
            tool_results=tool_results,
            context_memory_count=len(context_items),
            model_warnings=response.intent.parse_warnings,
            interpretation=interpretation,
        )

    def _return_interpretation_stop(
        self,
        *,
        reply: str,
        user_event: RuntimeEvent,
        interpretation_event: RuntimeEvent,
        interpretation: InterpretationArtifact,
        context_memory_count: int,
        event_name: str,
    ) -> RuntimeTurn:
        stop_event = self.events.append(
            event_name,
            parent_index=interpretation_event.index,
            reply=reply,
            interpretation=interpretation.to_dict(),
        )
        final_claim = self._final_claim({"C2": "pass", "C4": "pass"}, ())
        claim_event = self.events.append(
            "runtime.claim.emitted",
            parent_index=stop_event.index,
            claim=final_claim.to_dict(),
        )
        self.state.health.last_claim = final_claim.classification
        return RuntimeTurn(
            reply=reply,
            user_event=user_event,
            model_event=None,
            claim_event=claim_event,
            final_claim=final_claim,
            context_memory_count=context_memory_count,
            interpretation=interpretation,
        )

    def _capture_memory_proposals(
        self, response: ModelResponse, event_index: int
    ) -> tuple[MemoryProposal, ...]:
        captured: list[MemoryProposal] = []
        for intent in response.intent.memory_proposals:
            with mutation_context(
                actor="runtime_kernel",
                operation="capture_memory_proposal",
                evidence=(f"model_event_index={event_index}",),
            ):
                proposal = self.proposals.submit(
                    propose_memory(
                        intent.content,
                        intent.reason,
                        tags=intent.tags,
                        source="model_proposal",
                        event_index=event_index,
                        pinned=intent.pinned,
                    )
                )
            captured.append(proposal)
            self.events.append(
                "memory.proposal.created",
                parent_index=event_index,
                proposal_id=proposal.proposal_id,
                content=proposal.content,
                reason=proposal.reason,
                tags=proposal.tags,
                pinned=proposal.pinned,
                salience=evaluate_salience(
                    intent.content,
                    source=SalienceSource.MEMORY_PROPOSAL,
                    model_memory_proposal=True,
                    metadata={"proposal_id": proposal.proposal_id},
                ).to_dict(),
            )
        return tuple(captured)

    def _evaluate_tool_proposals(
        self,
        response: ModelResponse,
        event_index: int,
        *,
        user_event_index: int | None = None,
        interpretation_event_index: int | None = None,
        interpretation: InterpretationArtifact | None = None,
    ) -> tuple[ToolResult, ...]:
        import time
        results: list[ToolResult] = []
        for intent in response.intent.tool_proposals:
            current_ts = time.time()
            wall_clock_ms = (current_ts - self.state.health.last_progress_ts) * 1000.0
            self.state.health.posture = self.governor.adjudicate(self.state.health, wall_clock_ms)
            self.state.health.tool_attempts_this_turn += 1

            call = ToolCall(
                tool_name=intent.action_type,
                payload=intent.payload,
                actor="model_adapter",
                reason=intent.reason,
            )
            result = self.tool_executor.evaluate(
                call, posture=self.state.health.posture
            )
            results.append(result)
            
            if result.status == ToolResultStatus.EXECUTED:
                self.state.health.consecutive_refusals = 0
                self.state.health.stall_count = 0
                self.state.health.last_successful_action_ts = current_ts
                self.state.health.last_progress_ts = current_ts
                self.state.health.anomaly_score *= 0.5
            elif result.status in (ToolResultStatus.REFUSED, ToolResultStatus.UNKNOWN_TOOL, ToolResultStatus.ERROR):
                self.state.health.consecutive_refusals += 1
                self.state.health.stall_count += 1
            elif result.status == ToolResultStatus.APPROVED_NOT_EXECUTED:
                self.state.health.stall_count += 1

            action = self.tool_executor.action_for_call(call)
            binding = self._bind_tool_intent(
                call,
                interpretation=interpretation,
                user_event_index=user_event_index,
                interpretation_event_index=interpretation_event_index,
            )
            self.events.append(
                "tool.proposal.evaluated",
                parent_index=event_index,
                tool_call=call.to_payload(),
                tool_result=result.to_dict(),
                action=action.to_payload() if action else None,
                intent_binding=binding.to_dict(),
                salience=evaluate_salience(
                    source=SalienceSource.TOOL_RESULT,
                    tool_result_status=result.status.value,
                    metadata={"tool": call.tool_name, "reason_code": result.reason_code},
                ).to_dict(),
            )
        return tuple(results)


    def _bind_tool_intent(
        self,
        call: ToolCall,
        *,
        interpretation: InterpretationArtifact | None,
        user_event_index: int | None,
        interpretation_event_index: int | None,
    ) -> ToolIntentBinding:
        if interpretation is None:
            return ToolIntentBinding(
                status="unknown",
                user_event_index=user_event_index,
                interpretation_event_index=interpretation_event_index,
                reason_codes=("interpretation_missing",),
                evidence=("tool proposal was evaluated without an interpretation artifact",),
            )

        normalized = " ".join(interpretation.interpreted_intent.lower().split())
        tokens = set(re.findall(r"[a-z0-9_'-]+", normalized))
        tool_name = call.tool_name.lower()
        tool_name_parts = tuple(part for part in re.split(r"[_\-]+", tool_name) if part)

        name_bound = tool_name in tokens or (
            len(tool_name_parts) > 1 and all(part in tokens for part in tool_name_parts)
        )
        name_reason = (
            "tool_name_in_interpreted_intent" if tool_name in tokens
            else "tool_name_parts_in_interpreted_intent"
        )

        if name_bound:
            anomaly = self._check_payload_surface(call.payload, tokens)
            if anomaly:
                return ToolIntentBinding(
                    status="needs_confirmation",
                    user_event_index=user_event_index,
                    interpretation_event_index=interpretation_event_index,
                    reason_codes=(name_reason, "payload_mutation_verb_not_in_intent"),
                    evidence=(
                        f"interpreted intent contains tool name: {call.tool_name}",
                        anomaly,
                    ),
                )
            return ToolIntentBinding(
                status="bound",
                user_event_index=user_event_index,
                interpretation_event_index=interpretation_event_index,
                reason_codes=(name_reason,),
                evidence=(f"interpreted intent contains tool name: {call.tool_name}",),
            )

        return ToolIntentBinding(
            status="unbound",
            user_event_index=user_event_index,
            interpretation_event_index=interpretation_event_index,
            reason_codes=("tool_not_in_interpreted_intent",),
            evidence=(
                f"interpreted intent did not name proposed tool: {call.tool_name}",
                f"interpreted_intent={interpretation.interpreted_intent}",
            ),
        )

    _PAYLOAD_MUTATION_VERBS = frozenset({
        "delete", "remove", "wipe", "overwrite", "kill", "drop",
        "truncate", "destroy", "purge", "erase", "clear", "reset",
        "revoke", "terminate", "disable", "deactivate",
    })

    def _check_payload_surface(self, payload: dict, intent_tokens: set[str]) -> str:
        """Return an anomaly string if a mutation verb appears in payload values but not intent."""
        for key, value in payload.items():
            if not isinstance(value, str):
                continue
            value_tokens = set(re.findall(r"[a-z0-9_'-]+", value.lower()))
            anomalous = value_tokens & self._PAYLOAD_MUTATION_VERBS - intent_tokens
            if anomalous:
                return (
                    f"payload field '{key}' contains mutation verb(s) not in interpreted intent: "
                    + ", ".join(sorted(anomalous))
                )
        return ""

    def _final_claim(
        self,
        cbac_conditions: dict[str, str],
        tool_decisions: tuple[GateDecision, ...],
        tool_results: tuple[ToolResult, ...] = (),
    ) -> FinalClaim:
        sbac_evidence = self._sbac_evidence(tool_results)
        detector_caps = self._sbac_detector_caps(sbac_evidence)
        conditions = {
            "C1": "pass",
            "C2": cbac_conditions.get("C2", "unknown"),
            "C3": "partial",
            "C4": cbac_conditions.get("C4", "partial"),
            "C5": evaluate_health(sbac_evidence)["C5"],
            "C6": "pass",
        }
        breach_notices: list[str] = []
        scope_limitations = list(
            runtime_turn_scope_limitations(r.status.value for r in tool_results)
        )

        for decision in tool_decisions:
            for condition, state in decision.condition_summary.items():
                if state == "fail":
                    conditions[condition] = "fail"
                elif conditions.get(condition) == "pass" and state in {"partial", "unknown"}:
                    conditions[condition] = state

        return self.claims.classify(
            ClaimEvidence(
                execution_path_id="mvl_turn",
                conditions=conditions,
                breach_notices=tuple(breach_notices),
                detector_caps=tuple(detector_caps),
                scope_limitations=tuple(scope_limitations),
                segment_documented=True,
            )
        )

    def _sbac_evidence(self, tool_results: tuple[ToolResult, ...]) -> SBACEvidence:
        """Return the strongest honest SBAC evidence available to this runtime turn."""

        health_refusal = any(
            result.reason_code in {
                "health_critical",
                "health_frozen",
                "health_restricted",
                "health_approval_required",
            }
            for result in tool_results
        )
        telemetry_observed = (
            bool(tool_results)
            or self.state.health.tool_attempts_this_turn > 0
            or self.state.health.consecutive_refusals > 0
            or self.state.health.stall_count > 0
            or self.state.health.posture.value != "healthy"
            or health_refusal
        )
        if telemetry_observed:
            return SBACEvidence(
                detector_trust="indirect_instrumentation",
                timing_relation="before_commitment",
                commitment_model_declared=True,
            )
        return SBACEvidence(
            detector_trust="heuristic",
            timing_relation="before_commitment",
            commitment_model_declared=True,
        )

    def _sbac_detector_caps(self, evidence: SBACEvidence) -> list[str]:
        if evidence.detector_trust in {"activation_level", "architecture_bound_verified"}:
            return []
        if evidence.detector_trust == "indirect_instrumentation":
            return ["sbac_indirect_runtime_telemetry"]
        if evidence.detector_trust == "heuristic":
            return ["sbac_heuristic_runtime_telemetry"]
        return ["sbac_uninstrumented"]

    def close(self) -> None:
        self.memory.close()
        if self.proposal_store is not None:
            self.proposal_store.close()
        if self.ledger is not None:
            self.ledger.close()


def build_kernel_for_path(
    path: str | Path,
    *,
    model_client: LocalModelClient | None = None,
) -> RuntimeKernel:
    config = RuntimeConfig(database_path=str(path))
    return RuntimeKernel(config=config, model_client=model_client)
