"""Runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeConfig:
    model_base_url: str = "http://localhost:1234/v1"
    model_name: str = "openai/gpt-oss-20b"
    database_path: str = "halcyon.sqlite"
    allow_external_mutation: bool = False

    def events_database_path(self) -> Path:
        path = Path(self.database_path)
        return path.with_suffix(".events.sqlite")

    def memory_database_path(self) -> Path:
        path = Path(self.database_path)
        return path.with_suffix(".memory.sqlite")

    def proposal_database_path(self) -> Path:
        path = Path(self.database_path)
        return path.with_suffix(".proposals.sqlite")
