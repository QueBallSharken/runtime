"""Prompt assembly with CBAC-aware invariant placement."""

from __future__ import annotations

from halcyon_core.governance.invariants import CanonicalInvariant, default_invariant


STRUCTURED_OUTPUT_INSTRUCTIONS = """Return either plain text or a JSON object with this shape:
{
  "reply": "user-facing text",
  "memory_proposals": [
    {"content": "candidate memory", "reason": "why it matters", "tags": ["tag"], "pinned": false}
  ],
  "tool_proposals": [
    {"action_type": "name", "target": "target", "payload": {}, "reason": "why", "mutation_class": "external"}
  ]
}
Never claim that proposals were executed or permanently remembered.
"""


def build_system_prompt(
    *,
    invariant: CanonicalInvariant | None = None,
    role: str = "You are Halcyon, a local-first governed runtime assistant.",
) -> str:
    invariant = invariant or default_invariant()
    return "\n".join(
        [
            role,
            "Governing invariant:",
            f"- id: {invariant.invariant_id}",
            f"- digest: {invariant.digest()}",
            f"- statement: {invariant.statement}",
            "Rules:",
            "- Do not execute tools.",
            "- Do not claim memory was saved unless approval is provided by the runtime.",
            "- Treat uncertain facts as uncertain.",
            STRUCTURED_OUTPUT_INSTRUCTIONS,
        ]
    )


def build_messages(system: str, user: str, memory_context: str = "") -> list[dict[str, str]]:
    content = system if not memory_context else f"{system}\n\nRelevant memory:\n{memory_context}"
    return [{"role": "system", "content": content}, {"role": "user", "content": user}]
