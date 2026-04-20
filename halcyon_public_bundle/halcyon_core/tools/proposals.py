"""Tool proposal records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolProposal:
    tool_name: str
    args: dict
    reason: str
    status: str = "pending"
