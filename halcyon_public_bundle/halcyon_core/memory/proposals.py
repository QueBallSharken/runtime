"""Human-reviewed durable memory proposals."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Literal, Protocol

from halcyon_core.events import canonical_json, digest_payload
from halcyon_core.governance.mutation_context import require_mutation_context
from halcyon_core.memory.store import MemoryItem, SQLiteMemoryStore, make_memory

ProposalStatus = Literal["pending", "approved", "rejected"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MemoryProposal:
    content: str
    reason: str
    tags: tuple[str, ...] = ()
    source: str = "model_proposal"
    created_at: str = field(default_factory=utc_now)
    proposal_id: str | None = None
    event_index: int | None = None
    pinned: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    status: ProposalStatus = "pending"
    decided_by: str | None = None
    decided_at: str | None = None
    decision_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tags", tuple(self.tags))
        if self.proposal_id is None:
            object.__setattr__(self, "proposal_id", self.digest())

    def to_payload(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "reason": self.reason,
            "tags": list(self.tags),
            "source": self.source,
            "created_at": self.created_at,
            "event_index": self.event_index,
            "pinned": self.pinned,
            "metadata": self.metadata,
        }

    def digest(self) -> str:
        return digest_payload(self.to_payload())


def propose_memory(
    content: str,
    reason: str,
    *,
    tags: Iterable[str] = (),
    source: str = "model_proposal",
    event_index: int | None = None,
    pinned: bool = False,
    metadata: dict[str, Any] | None = None,
) -> MemoryProposal:
    return MemoryProposal(
        content=content,
        reason=reason,
        tags=tuple(tags),
        source=source,
        event_index=event_index,
        pinned=pinned,
        metadata=metadata or {},
    )


class ProposalQueue(Protocol):
    def submit(self, proposal: MemoryProposal) -> MemoryProposal: ...
    def pending(self) -> list[MemoryProposal]: ...
    def get(self, proposal_id: str) -> MemoryProposal | None: ...
    def approve(
        self,
        proposal_id: str,
        store: SQLiteMemoryStore,
        *,
        decided_by: str,
        decision_reason: str = "approved",
    ) -> MemoryItem: ...
    def reject(
        self,
        proposal_id: str,
        *,
        decided_by: str,
        decision_reason: str = "rejected",
    ) -> MemoryProposal: ...


class MemoryProposalQueue:
    def __init__(self) -> None:
        self._proposals: dict[str, MemoryProposal] = {}

    def submit(self, proposal: MemoryProposal) -> MemoryProposal:
        require_mutation_context("memory_proposal_queue.submit")
        self._proposals[proposal.proposal_id or proposal.digest()] = proposal
        return proposal

    def pending(self) -> list[MemoryProposal]:
        return [proposal for proposal in self._proposals.values() if proposal.status == "pending"]

    def all(self) -> list[MemoryProposal]:
        return list(self._proposals.values())

    def get(self, proposal_id: str) -> MemoryProposal | None:
        return self._proposals.get(proposal_id)

    def approve(
        self,
        proposal_id: str,
        store: SQLiteMemoryStore,
        *,
        decided_by: str,
        decision_reason: str = "approved",
    ) -> MemoryItem:
        require_mutation_context("memory_proposal_queue.approve")
        proposal = self._require_pending(proposal_id)
        memory = store.add(
            make_memory(
                proposal.content,
                tags=proposal.tags,
                source=proposal.source,
                event_index=proposal.event_index,
                pinned=proposal.pinned,
                metadata={**proposal.metadata, "proposal_id": proposal.proposal_id},
            )
        )
        self._proposals[proposal_id] = self._with_decision(
            proposal,
            status="approved",
            decided_by=decided_by,
            decision_reason=decision_reason,
        )
        return memory

    def reject(
        self,
        proposal_id: str,
        *,
        decided_by: str,
        decision_reason: str = "rejected",
    ) -> MemoryProposal:
        require_mutation_context("memory_proposal_queue.reject")
        proposal = self._require_pending(proposal_id)
        rejected = self._with_decision(
            proposal,
            status="rejected",
            decided_by=decided_by,
            decision_reason=decision_reason,
        )
        self._proposals[proposal_id] = rejected
        return rejected

    def _require_pending(self, proposal_id: str) -> MemoryProposal:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)
        if proposal.status != "pending":
            raise ValueError(f"proposal is already {proposal.status}")
        return proposal

    def _with_decision(
        self,
        proposal: MemoryProposal,
        *,
        status: ProposalStatus,
        decided_by: str,
        decision_reason: str,
    ) -> MemoryProposal:
        return MemoryProposal(
            content=proposal.content,
            reason=proposal.reason,
            tags=proposal.tags,
            source=proposal.source,
            created_at=proposal.created_at,
            proposal_id=proposal.proposal_id,
            event_index=proposal.event_index,
            pinned=proposal.pinned,
            metadata=proposal.metadata,
            status=status,
            decided_by=decided_by,
            decided_at=utc_now(),
            decision_reason=decision_reason,
        )


class SQLiteMemoryProposalQueue:
    """SQLite-backed memory proposal review queue."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_proposals (
                proposal_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                reason TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                event_index INTEGER,
                pinned INTEGER NOT NULL,
                metadata_json TEXT NOT NULL,
                status TEXT NOT NULL,
                decided_by TEXT,
                decided_at TEXT,
                decision_reason TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_proposals_status ON memory_proposals(status)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_proposals_event ON memory_proposals(event_index)"
        )
        self._conn.commit()

    def submit(self, proposal: MemoryProposal) -> MemoryProposal:
        require_mutation_context("memory_proposal_queue.submit")
        self._conn.execute(
            """
            INSERT OR IGNORE INTO memory_proposals (
                proposal_id, content, reason, tags_json, source, created_at,
                event_index, pinned, metadata_json, status, decided_by,
                decided_at, decision_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._proposal_params(proposal),
        )
        self._conn.commit()
        stored = self.get(proposal.proposal_id or proposal.digest())
        if stored is None:
            raise RuntimeError("proposal insert failed")
        return stored

    def pending(self) -> list[MemoryProposal]:
        return [
            self._row_to_proposal(row)
            for row in self._conn.execute(
                "SELECT * FROM memory_proposals WHERE status = 'pending' ORDER BY created_at ASC"
            )
        ]

    def all(self) -> list[MemoryProposal]:
        return [
            self._row_to_proposal(row)
            for row in self._conn.execute("SELECT * FROM memory_proposals ORDER BY created_at ASC")
        ]

    def get(self, proposal_id: str) -> MemoryProposal | None:
        row = self._conn.execute(
            "SELECT * FROM memory_proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return self._row_to_proposal(row) if row else None

    def approve(
        self,
        proposal_id: str,
        store: SQLiteMemoryStore,
        *,
        decided_by: str,
        decision_reason: str = "approved",
    ) -> MemoryItem:
        require_mutation_context("memory_proposal_queue.approve")
        proposal = self._require_pending(proposal_id)
        memory = store.add(
            make_memory(
                proposal.content,
                tags=proposal.tags,
                source=proposal.source,
                event_index=proposal.event_index,
                pinned=proposal.pinned,
                metadata={**proposal.metadata, "proposal_id": proposal.proposal_id},
            )
        )
        decided = self._with_decision(
            proposal,
            status="approved",
            decided_by=decided_by,
            decision_reason=decision_reason,
        )
        self._update_decision(decided)
        return memory

    def reject(
        self,
        proposal_id: str,
        *,
        decided_by: str,
        decision_reason: str = "rejected",
    ) -> MemoryProposal:
        require_mutation_context("memory_proposal_queue.reject")
        proposal = self._require_pending(proposal_id)
        rejected = self._with_decision(
            proposal,
            status="rejected",
            decided_by=decided_by,
            decision_reason=decision_reason,
        )
        self._update_decision(rejected)
        return rejected

    def close(self) -> None:
        self._conn.close()

    def _require_pending(self, proposal_id: str) -> MemoryProposal:
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)
        if proposal.status != "pending":
            raise ValueError(f"proposal is already {proposal.status}")
        return proposal

    def _with_decision(
        self,
        proposal: MemoryProposal,
        *,
        status: ProposalStatus,
        decided_by: str,
        decision_reason: str,
    ) -> MemoryProposal:
        return MemoryProposal(
            content=proposal.content,
            reason=proposal.reason,
            tags=proposal.tags,
            source=proposal.source,
            created_at=proposal.created_at,
            proposal_id=proposal.proposal_id,
            event_index=proposal.event_index,
            pinned=proposal.pinned,
            metadata=proposal.metadata,
            status=status,
            decided_by=decided_by,
            decided_at=utc_now(),
            decision_reason=decision_reason,
        )

    def _update_decision(self, proposal: MemoryProposal) -> None:
        self._conn.execute(
            """
            UPDATE memory_proposals
            SET status = ?, decided_by = ?, decided_at = ?, decision_reason = ?
            WHERE proposal_id = ? AND status = 'pending'
            """,
            (
                proposal.status,
                proposal.decided_by,
                proposal.decided_at,
                proposal.decision_reason,
                proposal.proposal_id,
            ),
        )
        self._conn.commit()

    def _proposal_params(self, proposal: MemoryProposal) -> tuple[Any, ...]:
        return (
            proposal.proposal_id,
            proposal.content,
            proposal.reason,
            canonical_json(list(proposal.tags)),
            proposal.source,
            proposal.created_at,
            proposal.event_index,
            int(proposal.pinned),
            canonical_json(proposal.metadata),
            proposal.status,
            proposal.decided_by,
            proposal.decided_at,
            proposal.decision_reason,
        )

    def _row_to_proposal(self, row: sqlite3.Row) -> MemoryProposal:
        return MemoryProposal(
            proposal_id=row["proposal_id"],
            content=row["content"],
            reason=row["reason"],
            tags=tuple(json.loads(row["tags_json"])),
            source=row["source"],
            created_at=row["created_at"],
            event_index=row["event_index"],
            pinned=bool(row["pinned"]),
            metadata=json.loads(row["metadata_json"]),
            status=row["status"],
            decided_by=row["decided_by"],
            decided_at=row["decided_at"],
            decision_reason=row["decision_reason"],
        )
