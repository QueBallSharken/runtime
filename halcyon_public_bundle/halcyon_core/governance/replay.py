"""Replay evidence reducer.

Walks the disciplined mutation event family and reconstructs the governance
proof chain per governed action. Produces structured evidence that answers:
- Who requested what?
- Was the binding check run and what did it find?
- Was approval granted before execution?
- Are digest references intact across the chain?
- What governance claim can actually be made from this evidence?

This is a read-only, side-effect-free reducer. It does not write to the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from halcyon_core.events import RuntimeEvent, SQLiteEventLedger


# ---------------------------------------------------------------------------
# Evidence structures
# ---------------------------------------------------------------------------

@dataclass
class ToolChain:
    """Governance proof chain for one tool execution path."""

    call_digest: str

    proposal_event: RuntimeEvent | None = None
    override_event: RuntimeEvent | None = None
    binding_evaluated_event: RuntimeEvent | None = None
    approval_event: RuntimeEvent | None = None
    execution_event: RuntimeEvent | None = None

    ordering_violations: list[str] = field(default_factory=list)
    digest_violations: list[str] = field(default_factory=list)
    parent_violations: list[str] = field(default_factory=list)

    @property
    def has_proposal(self) -> bool:
        return self.proposal_event is not None

    @property
    def has_binding_evaluated(self) -> bool:
        return self.binding_evaluated_event is not None

    @property
    def has_approval(self) -> bool:
        return self.approval_event is not None

    @property
    def has_execution(self) -> bool:
        return self.execution_event is not None

    @property
    def execution_outcome(self) -> str:
        if self.execution_event is None:
            return "incomplete"
        name = self.execution_event.name
        if name == "tool.execution.completed":
            return "executed"
        if name == "tool.execution.refused":
            return "refused"
        return "unknown"

    @property
    def missing_events(self) -> list[str]:
        missing = []
        if not self.has_proposal:
            missing.append("tool.proposal.evaluated")
        if not self.has_binding_evaluated:
            missing.append("tool.intent_binding.evaluated")
        if not self.has_approval:
            missing.append("tool.approval.granted")
        if not self.has_execution:
            missing.append("tool.execution.completed|refused")
        return missing

    @property
    def is_complete(self) -> bool:
        return (
            self.has_proposal
            and self.has_binding_evaluated
            and self.has_approval
            and self.has_execution
            and not self.ordering_violations
            and not self.digest_violations
            and not self.parent_violations
        )

    @property
    def governance_claim(self) -> str:
        if not self.has_proposal:
            return "no_evidence"
        if not self.has_approval and self.has_execution:
            return "ungoverned_execution"
        if not self.has_execution:
            if not self.has_approval:
                return "incomplete"
            return "approved_not_executed"
        if not self.has_binding_evaluated:
            return "partial_governance"
        if self.ordering_violations or self.digest_violations or self.parent_violations:
            return "governance_breach"
        return "strong_governed_execution"

    def all_violations(self) -> list[str]:
        return self.ordering_violations + self.digest_violations + self.parent_violations


@dataclass
class MemoryChain:
    """Governance proof chain for one memory write path."""

    proposal_id: str

    proposal_event: RuntimeEvent | None = None
    decision_event: RuntimeEvent | None = None
    durable_event: RuntimeEvent | None = None

    ordering_violations: list[str] = field(default_factory=list)
    parent_violations: list[str] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        if self.decision_event is None:
            return "pending"
        name = self.decision_event.name
        if name == "memory.proposal.approved":
            return "approved"
        if name == "memory.proposal.rejected":
            return "rejected"
        return "unknown"

    @property
    def is_complete(self) -> bool:
        if self.outcome == "rejected":
            return (
                self.decision_event is not None
                and not self.ordering_violations
                and not self.parent_violations
            )
        return (
            self.decision_event is not None
            and self.durable_event is not None
            and not self.ordering_violations
            and not self.parent_violations
        )

    @property
    def governance_claim(self) -> str:
        if self.decision_event is None:
            return "pending"
        if self.outcome == "approved" and self.durable_event is None:
            return "approved_not_written"
        if self.ordering_violations or self.parent_violations:
            return "governance_breach"
        return "strong_governed_write"

    def all_violations(self) -> list[str]:
        return self.ordering_violations + self.parent_violations


@dataclass
class ReplayEvidence:
    """Full replay evidence summary for a ledger."""

    ledger_path: str
    total_events_scanned: int
    tool_chains: list[ToolChain] = field(default_factory=list)
    memory_chains: list[MemoryChain] = field(default_factory=list)

    @property
    def complete_tool_chains(self) -> int:
        return sum(1 for c in self.tool_chains if c.is_complete)

    @property
    def incomplete_tool_chains(self) -> int:
        return sum(1 for c in self.tool_chains if not c.is_complete)

    @property
    def complete_memory_chains(self) -> int:
        return sum(1 for c in self.memory_chains if c.is_complete)

    @property
    def incomplete_memory_chains(self) -> int:
        return sum(1 for c in self.memory_chains if not c.is_complete)

    def strong_tool_executions(self) -> list[ToolChain]:
        return [c for c in self.tool_chains if c.governance_claim == "strong_governed_execution"]

    def breached_chains(self) -> list[ToolChain | MemoryChain]:
        return [
            c for c in (*self.tool_chains, *self.memory_chains)
            if c.governance_claim == "governance_breach"
        ]

    def to_summary(self) -> dict[str, Any]:
        return {
            "ledger_path": self.ledger_path,
            "total_events_scanned": self.total_events_scanned,
            "tool_chains": {
                "total": len(self.tool_chains),
                "complete": self.complete_tool_chains,
                "incomplete": self.incomplete_tool_chains,
                "by_claim": _count_by(self.tool_chains, lambda c: c.governance_claim),
                "by_outcome": _count_by(self.tool_chains, lambda c: c.execution_outcome),
            },
            "memory_chains": {
                "total": len(self.memory_chains),
                "complete": self.complete_memory_chains,
                "incomplete": self.incomplete_memory_chains,
                "by_claim": _count_by(self.memory_chains, lambda c: c.governance_claim),
                "by_outcome": _count_by(self.memory_chains, lambda c: c.outcome),
            },
        }


# ---------------------------------------------------------------------------
# Reducer
# ---------------------------------------------------------------------------

def reduce_replay_evidence(ledger: SQLiteEventLedger) -> ReplayEvidence:
    """Walk the ledger and reconstruct governance proof chains per action."""
    tool_chains: dict[str, ToolChain] = {}
    memory_chains: dict[str, MemoryChain] = {}
    total = 0

    for event in ledger.iter_events():
        total += 1
        name = event.name
        p = event.payload

        if name == "tool.proposal.evaluated":
            call_digest = p.get("tool_result", {}).get("call_digest")
            if call_digest:
                chain = tool_chains.setdefault(call_digest, ToolChain(call_digest=call_digest))
                chain.proposal_event = event

        elif name == "tool.intent_override.accepted":
            call_digest = p.get("call_digest")
            if call_digest:
                chain = tool_chains.setdefault(call_digest, ToolChain(call_digest=call_digest))
                chain.override_event = event

        elif name == "tool.intent_binding.evaluated":
            call_digest = p.get("call_digest")
            if call_digest:
                chain = tool_chains.setdefault(call_digest, ToolChain(call_digest=call_digest))
                chain.binding_evaluated_event = event

        elif name == "tool.approval.granted":
            call_digest = p.get("call_digest")
            if call_digest:
                chain = tool_chains.setdefault(call_digest, ToolChain(call_digest=call_digest))
                chain.approval_event = event

        elif name in ("tool.execution.completed", "tool.execution.refused"):
            call_digest = p.get("call_digest")
            if call_digest:
                chain = tool_chains.setdefault(call_digest, ToolChain(call_digest=call_digest))
                chain.execution_event = event

        elif name == "memory.proposal.created":
            proposal_id = p.get("proposal_id")
            if proposal_id:
                memory_chains.setdefault(proposal_id, MemoryChain(proposal_id=proposal_id))
                memory_chains[proposal_id].proposal_event = event

        elif name in ("memory.proposal.approved", "memory.proposal.rejected"):
            proposal_id = p.get("proposal_id")
            if proposal_id:
                chain = memory_chains.setdefault(proposal_id, MemoryChain(proposal_id=proposal_id))
                chain.decision_event = event

        elif name == "memory.durable.created":
            proposal_id = p.get("proposal_id")
            if proposal_id:
                chain = memory_chains.setdefault(proposal_id, MemoryChain(proposal_id=proposal_id))
                chain.durable_event = event

    for chain in tool_chains.values():
        _validate_tool_chain(chain)

    for chain in memory_chains.values():
        _validate_memory_chain(chain)

    return ReplayEvidence(
        ledger_path=str(ledger.path),
        total_events_scanned=total,
        tool_chains=list(tool_chains.values()),
        memory_chains=list(memory_chains.values()),
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_tool_chain(chain: ToolChain) -> None:
    events = [
        ("proposal", chain.proposal_event),
        ("override", chain.override_event),
        ("binding_evaluated", chain.binding_evaluated_event),
        ("approval", chain.approval_event),
        ("execution", chain.execution_event),
    ]
    present = [(role, ev) for role, ev in events if ev is not None]

    # Causal ordering: indices must be strictly increasing
    for i in range(len(present) - 1):
        role_a, ev_a = present[i]
        role_b, ev_b = present[i + 1]
        if ev_a.index >= ev_b.index:
            chain.ordering_violations.append(
                f"{role_a}({ev_a.index}) not before {role_b}({ev_b.index})"
            )

    # Parent link: each event's parent should point to the previous present event
    for i in range(1, len(present)):
        role, ev = present[i]
        expected_parent_index = present[i - 1][1].index
        if ev.parent_index != expected_parent_index:
            chain.parent_violations.append(
                f"{role} parent_index={ev.parent_index} expected {expected_parent_index}"
            )

    # Digest integrity: action_digest must match across approval and execution
    if chain.approval_event and chain.execution_event:
        approval_digest = chain.approval_event.payload.get("action_digest")
        execution_digest = chain.execution_event.payload.get("action_digest")
        if approval_digest and execution_digest and approval_digest != execution_digest:
            chain.digest_violations.append(
                f"action_digest mismatch: approval={approval_digest[:16]}... "
                f"execution={execution_digest[:16]}..."
            )

        approval_approval_digest = chain.approval_event.payload.get("approval_digest")
        execution_approval_digest = chain.execution_event.payload.get("approval_digest")
        if (approval_approval_digest and execution_approval_digest
                and approval_approval_digest != execution_approval_digest):
            chain.digest_violations.append(
                f"approval_digest mismatch across approval and execution events"
            )


def _validate_memory_chain(chain: MemoryChain) -> None:
    events = [
        ("proposal", chain.proposal_event),
        ("decision", chain.decision_event),
        ("durable", chain.durable_event),
    ]
    present = [(role, ev) for role, ev in events if ev is not None]

    for i in range(len(present) - 1):
        role_a, ev_a = present[i]
        role_b, ev_b = present[i + 1]
        if ev_a.index >= ev_b.index:
            chain.ordering_violations.append(
                f"{role_a}({ev_a.index}) not before {role_b}({ev_b.index})"
            )

    for i in range(1, len(present)):
        role, ev = present[i]
        expected_parent_index = present[i - 1][1].index
        if ev.parent_index != expected_parent_index:
            chain.parent_violations.append(
                f"{role} parent_index={ev.parent_index} expected {expected_parent_index}"
            )


def _count_by(items: list, key_fn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        k = key_fn(item)
        counts[k] = counts.get(k, 0) + 1
    return counts
