"""Canonical governance invariants.

CBAC and ABAC need a structured governing object whose identity can be checked
without natural-language similarity. The MVP uses canonical JSON plus SHA-384.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from halcyon_core.events import canonical_json, digest_payload


@dataclass(frozen=True)
class CanonicalInvariant:
    invariant_id: str
    version: str
    statement: str
    scope: tuple[str, ...] = ()
    refusal_semantics: str = "must_refuse_external_mutation_without_approval"
    enforcement: str = "deny_by_default"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scope"] = list(self.scope)
        return payload

    def canonical_json(self) -> str:
        return canonical_json(self.to_payload())

    def digest(self) -> str:
        return digest_payload(self.to_payload())


# Backward-compatible name used by the scaffold and early callers.
Invariant = CanonicalInvariant


def default_invariant() -> CanonicalInvariant:
    return CanonicalInvariant(
        invariant_id="inv_no_external_mutation_without_explicit_approval",
        version="1.0.0",
        statement="External mutations require an approved ABAC action package before commit.",
        scope=("external_mutation", "tool_execution", "memory_durable_write"),
    )
