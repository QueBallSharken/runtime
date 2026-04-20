"""Memory retrieval helpers."""

from __future__ import annotations

from halcyon_core.memory.store import MemoryItem, SQLiteMemoryStore


def render_context(items: list[MemoryItem]) -> str:
    lines = []
    for item in items:
        tags = ",".join(item.tags) if item.tags else "untagged"
        pin = " pinned" if item.pinned else ""
        lines.append(f"- [{tags}; source={item.source}{pin}] {item.content}")
    return "\n".join(lines)


def retrieve_context(
    store: SQLiteMemoryStore,
    *,
    tags: tuple[str, ...] = (),
    recent_limit: int = 8,
    pinned_limit: int = 8,
) -> list[MemoryItem]:
    seen: set[int] = set()
    results: list[MemoryItem] = []

    for item in store.pinned(limit=pinned_limit):
        if item.memory_id is not None and item.memory_id not in seen:
            results.append(item)
            seen.add(item.memory_id)

    for tag in tags:
        for item in store.by_tag(tag, limit=recent_limit):
            if item.memory_id is not None and item.memory_id not in seen:
                results.append(item)
                seen.add(item.memory_id)

    for item in store.recent(limit=recent_limit):
        if item.memory_id is not None and item.memory_id not in seen:
            results.append(item)
            seen.add(item.memory_id)

    return results
