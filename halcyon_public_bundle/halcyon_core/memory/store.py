"""SQLite-backed durable memory store.

The clean memory layer keeps durable memory separate from memory proposals. It
preserves the useful legacy hippocampus shape: chronological entries plus tag
retrieval, with source labels and optional event links for auditability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from halcyon_core.events import canonical_json, digest_payload
from halcyon_core.governance.mutation_context import require_mutation_context


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MemoryItem:
    content: str
    tags: tuple[str, ...] = ()
    source: str = "runtime"
    created_at: str = field(default_factory=utc_now)
    memory_id: int | None = None
    event_index: int | None = None
    pinned: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    content_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tags", tuple(self.tags))
        if self.content_digest is None:
            object.__setattr__(self, "content_digest", self.digest())

    def digest_payload(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tags": list(self.tags),
            "source": self.source,
            "event_index": self.event_index,
            "pinned": self.pinned,
            "metadata": self.metadata,
        }

    def digest(self) -> str:
        return digest_payload(self.digest_payload())


class SQLiteMemoryStore:
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
            CREATE TABLE IF NOT EXISTS memory_items (
                memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                event_index INTEGER,
                pinned INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL,
                content_digest TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_tags (
                memory_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (memory_id, tag),
                FOREIGN KEY (memory_id) REFERENCES memory_items(memory_id)
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_items_created ON memory_items(created_at)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_items_source ON memory_items(source)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_tags_tag ON memory_tags(tag)")
        self._conn.commit()

    def add(self, item: MemoryItem) -> MemoryItem:
        require_mutation_context("durable_memory_store.add")
        cursor = self._conn.execute(
            """
            INSERT INTO memory_items (
                content, source, created_at, event_index, pinned,
                metadata_json, content_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.content,
                item.source,
                item.created_at,
                item.event_index,
                int(item.pinned),
                canonical_json(item.metadata),
                item.content_digest,
            ),
        )
        memory_id = int(cursor.lastrowid)
        self._conn.executemany(
            "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
            [(memory_id, tag) for tag in item.tags],
        )
        self._conn.commit()
        return MemoryItem(
            memory_id=memory_id,
            content=item.content,
            tags=item.tags,
            source=item.source,
            created_at=item.created_at,
            event_index=item.event_index,
            pinned=item.pinned,
            metadata=item.metadata,
            content_digest=item.content_digest,
        )

    def get(self, memory_id: int) -> MemoryItem | None:
        row = self._conn.execute(
            "SELECT * FROM memory_items WHERE memory_id = ?", (memory_id,)
        ).fetchone()
        return self._row_to_item(row) if row else None

    def recent(self, limit: int = 8) -> list[MemoryItem]:
        rows = self._conn.execute(
            "SELECT * FROM memory_items ORDER BY memory_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def by_tag(self, tag: str, limit: int = 8) -> list[MemoryItem]:
        rows = self._conn.execute(
            """
            SELECT mi.* FROM memory_items mi
            JOIN memory_tags mt ON mt.memory_id = mi.memory_id
            WHERE mt.tag = ?
            ORDER BY mi.memory_id DESC
            LIMIT ?
            """,
            (tag, limit),
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def pinned(self, limit: int | None = None) -> list[MemoryItem]:
        sql = "SELECT * FROM memory_items WHERE pinned = 1 ORDER BY memory_id DESC"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return [self._row_to_item(row) for row in self._conn.execute(sql, params)]

    def search_text(self, query: str, limit: int = 8) -> list[MemoryItem]:
        pattern = f"%{query}%"
        rows = self._conn.execute(
            "SELECT * FROM memory_items WHERE content LIKE ? ORDER BY memory_id DESC LIMIT ?",
            (pattern, limit),
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def all_items(self) -> list[MemoryItem]:
        return [
            self._row_to_item(row)
            for row in self._conn.execute("SELECT * FROM memory_items ORDER BY memory_id ASC")
        ]

    def _tags_for(self, memory_id: int) -> tuple[str, ...]:
        rows = self._conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ? ORDER BY tag ASC",
            (memory_id,),
        ).fetchall()
        return tuple(row["tag"] for row in rows)

    def _row_to_item(self, row: sqlite3.Row) -> MemoryItem:
        memory_id = int(row["memory_id"])
        return MemoryItem(
            memory_id=memory_id,
            content=row["content"],
            tags=self._tags_for(memory_id),
            source=row["source"],
            created_at=row["created_at"],
            event_index=row["event_index"],
            pinned=bool(row["pinned"]),
            metadata=json.loads(row["metadata_json"]),
            content_digest=row["content_digest"],
        )

    def close(self) -> None:
        self._conn.close()


# Backward-compatible alias for early scaffold callers.
MemoryStore = SQLiteMemoryStore


def make_memory(
    content: str,
    *,
    tags: Iterable[str] = (),
    source: str = "runtime",
    event_index: int | None = None,
    pinned: bool = False,
    metadata: dict[str, Any] | None = None,
) -> MemoryItem:
    return MemoryItem(
        content=content,
        tags=tuple(tags),
        source=source,
        event_index=event_index,
        pinned=pinned,
        metadata=metadata or {},
    )
