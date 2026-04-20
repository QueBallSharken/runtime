"""Import legacy JSON memories as labeled archive records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from halcyon_core.memory.store import MemoryItem, SQLiteMemoryStore, make_memory


def load_legacy_json(path: str | Path) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def legacy_records(value: Any, *, source: str = "lore_archive") -> list[MemoryItem]:
    records: list[MemoryItem] = []
    flatten_legacy(value, records, source=source, path=())
    return records


def flatten_legacy(
    value: Any,
    records: list[MemoryItem],
    *,
    source: str,
    path: tuple[str, ...],
) -> None:
    if isinstance(value, str):
        records.append(
            make_memory(
                value,
                tags=("legacy", "archive", *path),
                source=source,
                metadata={"legacy_path": list(path)},
            )
        )
    elif isinstance(value, dict):
        if all(not isinstance(item, (dict, list)) for item in value.values()):
            records.append(
                make_memory(
                    json.dumps(value, sort_keys=True),
                    tags=("legacy", "archive", *path),
                    source=source,
                    metadata={"legacy_path": list(path), "legacy_type": "object"},
                )
            )
        else:
            for key, child in value.items():
                flatten_legacy(child, records, source=source, path=(*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            flatten_legacy(child, records, source=source, path=(*path, str(index)))
    else:
        records.append(
            make_memory(
                json.dumps(value, sort_keys=True),
                tags=("legacy", "archive", *path),
                source=source,
                metadata={"legacy_path": list(path), "legacy_type": type(value).__name__},
            )
        )


def import_legacy_json(
    path: str | Path,
    store: SQLiteMemoryStore,
    *,
    source: str = "lore_archive",
) -> list[MemoryItem]:
    records = legacy_records(load_legacy_json(path), source=source)
    return [store.add(record) for record in records]
