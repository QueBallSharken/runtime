"""Monotonic runtime events, sequence authority, and append-only ledger.

This module is the first clean transplant from the legacy `core/events.py`.
It gives ABAC, CBAC, memory, pulse, and claim classification one shared
causal ordering source. Wall-clock time is recorded as metadata only; the
monotonic event index is the ordering primitive.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_payload(value: Any) -> str:
    return "sha384:" + hashlib.sha384(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RuntimeEvent:
    index: int
    name: str
    payload: dict[str, Any]
    created_at: str = field(default_factory=utc_now)
    boundary_id: str | None = None
    parent_index: int | None = None
    payload_digest: str | None = None

    def __post_init__(self) -> None:
        if self.payload_digest is None:
            object.__setattr__(self, "payload_digest", digest_payload(self.payload))

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


class SequenceAuthority:
    """Assigns monotonic causal indices.

    This is deliberately tiny. Distributed ordering can come later, but the MVP
    must never pretend wall-clock timestamps prove causal order.
    """

    def __init__(self, start: int = 0) -> None:
        self._counter = start

    @property
    def current(self) -> int:
        return self._counter

    def next_index(self) -> int:
        self._counter += 1
        return self._counter

    def observe(self, index: int) -> None:
        if index > self._counter:
            self._counter = index


class SQLiteEventLedger:
    """Append-only SQLite event ledger.

    The class does not expose update/delete operations. If a later event revises
    a claim, it appends a new event instead of mutating history.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True) if self.path.parent != Path(".") else None
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_events (
                event_index INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                boundary_id TEXT,
                parent_index INTEGER,
                payload_json TEXT NOT NULL,
                payload_digest TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_events_name ON runtime_events(name)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_events_boundary ON runtime_events(boundary_id)"
        )
        self._conn.commit()

    def append(self, event: RuntimeEvent) -> None:
        self._conn.execute(
            """
            INSERT INTO runtime_events (
                event_index, name, created_at, boundary_id, parent_index,
                payload_json, payload_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.index,
                event.name,
                event.created_at,
                event.boundary_id,
                event.parent_index,
                canonical_json(event.payload),
                event.payload_digest,
            ),
        )
        self._conn.commit()

    def latest_index(self) -> int:
        row = self._conn.execute("SELECT MAX(event_index) AS max_index FROM runtime_events").fetchone()
        return int(row["max_index"] or 0)

    def iter_events(self, limit: int | None = None) -> Iterable[RuntimeEvent]:
        sql = "SELECT * FROM runtime_events ORDER BY event_index ASC"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        for row in self._conn.execute(sql, params):
            yield self._row_to_event(row)

    def iter_events_by_name(self, name: str) -> Iterable[RuntimeEvent]:
        for row in self._conn.execute(
            "SELECT * FROM runtime_events WHERE name = ? ORDER BY event_index ASC", (name,)
        ):
            yield self._row_to_event(row)

    def get(self, index: int) -> RuntimeEvent | None:
        row = self._conn.execute(
            "SELECT * FROM runtime_events WHERE event_index = ?", (index,)
        ).fetchone()
        return self._row_to_event(row) if row else None

    def _row_to_event(self, row: sqlite3.Row) -> RuntimeEvent:
        return RuntimeEvent(
            index=int(row["event_index"]),
            name=row["name"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
            boundary_id=row["boundary_id"],
            parent_index=row["parent_index"],
            payload_digest=row["payload_digest"],
        )

    def close(self) -> None:
        self._conn.close()


class EventBus:
    def __init__(
        self,
        ledger: SQLiteEventLedger | None = None,
        sequence: SequenceAuthority | None = None,
    ) -> None:
        self.ledger = ledger
        start = ledger.latest_index() if ledger else 0
        self.sequence = sequence or SequenceAuthority(start=start)
        self._subscribers: dict[str, list[Callable[[RuntimeEvent], None]]] = {}
        self.events: list[RuntimeEvent] = []

    def append(
        self,
        name: str,
        *,
        boundary_id: str | None = None,
        parent_index: int | None = None,
        **payload: Any,
    ) -> RuntimeEvent:
        event = RuntimeEvent(
            index=self.sequence.next_index(),
            name=name,
            payload=payload,
            boundary_id=boundary_id,
            parent_index=parent_index,
        )
        self.events.append(event)
        if self.ledger:
            self.ledger.append(event)
        self._notify(event)
        return event

    def dispatch(self, name: str, **payload: Any) -> RuntimeEvent:
        """Compatibility alias for the legacy event bus."""
        return self.append(name, **payload)

    def subscribe(self, name: str, handler: Callable[[RuntimeEvent], None]) -> None:
        self._subscribers.setdefault(name, []).append(handler)

    def _notify(self, event: RuntimeEvent) -> None:
        for handler in self._subscribers.get(event.name, []) + self._subscribers.get("*", []):
            handler(event)
