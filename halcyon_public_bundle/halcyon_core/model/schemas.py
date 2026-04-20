"""Structured model output contracts.

The MVL asks the model for one user-facing reply plus optional proposal sidecars.
Sidecars are not executed or persisted directly; downstream layers review them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any


@dataclass(frozen=True)
class MemoryProposalIntent:
    content: str
    reason: str
    tags: tuple[str, ...] = ()
    pinned: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MemoryProposalIntent":
        return cls(
            content=str(value.get("content", "")),
            reason=str(value.get("reason", "")),
            tags=tuple(str(tag) for tag in value.get("tags", ())),
            pinned=bool(value.get("pinned", False)),
        )


@dataclass(frozen=True)
class ToolProposalIntent:
    action_type: str
    target: str
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    mutation_class: str = "external"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ToolProposalIntent":
        payload = value.get("payload", {})
        return cls(
            action_type=str(value.get("action_type", "")),
            target=str(value.get("target", "")),
            payload=payload if isinstance(payload, dict) else {"value": payload},
            reason=str(value.get("reason", "")),
            mutation_class=str(value.get("mutation_class", "external")),
        )


@dataclass(frozen=True)
class ModelIntent:
    reply: str
    memory_proposals: tuple[MemoryProposalIntent, ...] = ()
    tool_proposals: tuple[ToolProposalIntent, ...] = ()
    parse_warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_model_content(content: str) -> str:
    """Remove narrow known wrappers around model payloads."""

    marker = "<|message|>"
    if marker in content:
        content = content.split(marker, 1)[1]
    return content.strip()


def parse_model_intent(content: str) -> ModelIntent:
    """Parse structured model content.

    Preferred shape:

    {
      "reply": "text for the user",
      "memory_proposals": [{"content": "...", "reason": "...", "tags": ["..."]}],
      "tool_proposals": [{"action_type": "...", "target": "...", "payload": {}}]
    }

    Plain text is accepted as a reply with a parse warning so the MVL can keep
    running against weaker local models.
    """

    original = content
    content = normalize_model_content(content)

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return ModelIntent(reply=original, parse_warnings=("model output was plain text",))

    if not isinstance(parsed, dict):
        return ModelIntent(reply=original, parse_warnings=("model JSON was not an object",))

    reply = parsed.get("reply")
    warnings: list[str] = []
    if not isinstance(reply, str) or not reply.strip():
        reply = original
        warnings.append("model JSON missing non-empty reply")

    memories = tuple(
        MemoryProposalIntent.from_dict(item)
        for item in parsed.get("memory_proposals", [])
        if isinstance(item, dict)
    )
    tools = tuple(
        ToolProposalIntent.from_dict(item)
        for item in parsed.get("tool_proposals", [])
        if isinstance(item, dict)
    )

    return ModelIntent(
        reply=reply,
        memory_proposals=memories,
        tool_proposals=tools,
        parse_warnings=tuple(warnings),
    )
