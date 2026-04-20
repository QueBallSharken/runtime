"""Context Boundary Audit Chain MVP primitives.

CBAC governs whether a canonical invariant survives a context handoff without
being weakened, approximated, or hidden behind an unobservable transform.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from halcyon_core.events import digest_payload
from halcyon_core.governance.invariants import CanonicalInvariant

ConditionState = Literal["pass", "partial", "fail", "unknown", "disputed"]
IdentityEvidence = Literal[
    "runtime_metadata", "process_bound", "signed_header", "cryptographic_attestation_pqc"
]
TransformVisibility = Literal["owned", "declared", "opaque", "unknown"]
TransformKind = Literal["identity", "serialization", "projection", "summary", "opaque"]


@dataclass(frozen=True)
class TransformRecord:
    name: str
    kind: TransformKind = "identity"
    visibility: TransformVisibility = "owned"
    lossy: bool = False

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextEnvelope:
    sender: str
    receiver: str
    invariant_digest: str
    transform_chain: tuple[TransformRecord, ...] = field(default_factory=tuple)
    identity_evidence: IdentityEvidence = "runtime_metadata"
    transform_visibility: TransformVisibility = "owned"
    payload_digest: str | None = None

    def __post_init__(self) -> None:
        if self.payload_digest is None:
            object.__setattr__(self, "payload_digest", digest_payload(self.to_payload(include_digest=False)))

    def to_payload(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload = {
            "sender": self.sender,
            "receiver": self.receiver,
            "invariant_digest": self.invariant_digest,
            "transform_chain": [item.to_payload() for item in self.transform_chain],
            "identity_evidence": self.identity_evidence,
            "transform_visibility": self.transform_visibility,
        }
        if include_digest:
            payload["payload_digest"] = self.payload_digest
        return payload

    def verify(self, expected_digest: str) -> bool:
        """Compatibility helper: true only when invariant identity is intact."""

        return self.invariant_digest == expected_digest


@dataclass(frozen=True)
class ContextBoundaryEvaluation:
    envelope_digest: str
    condition_summary: dict[str, ConditionState]
    claim_cap: str
    reasons: tuple[str, ...] = ()


def create_envelope(
    sender: str,
    receiver: str,
    invariant: CanonicalInvariant,
    *,
    transform_chain: tuple[TransformRecord, ...] = (),
    identity_evidence: IdentityEvidence = "runtime_metadata",
    transform_visibility: TransformVisibility = "owned",
) -> ContextEnvelope:
    return ContextEnvelope(
        sender=sender,
        receiver=receiver,
        invariant_digest=invariant.digest(),
        transform_chain=transform_chain,
        identity_evidence=identity_evidence,
        transform_visibility=transform_visibility,
    )


def evaluate_handoff(
    envelope: ContextEnvelope,
    receiver_invariant_digest: str,
) -> ContextBoundaryEvaluation:
    reasons: list[str] = []
    c2: ConditionState = "pass"
    c4: ConditionState = "pass"

    if envelope.invariant_digest != receiver_invariant_digest:
        c2 = "fail"
        reasons.append("receiver invariant digest does not match envelope invariant digest")

    if any(transform.lossy for transform in envelope.transform_chain):
        c2 = "fail"
        reasons.append("lossy transform in invariant-bearing handoff")

    if any(transform.visibility in {"opaque", "unknown"} for transform in envelope.transform_chain):
        c4 = "partial"
        reasons.append("transform chain contains opaque or unknown visibility")

    if envelope.transform_visibility in {"opaque", "unknown"}:
        c4 = "partial"
        reasons.append("handoff transform visibility is incomplete")

    if envelope.identity_evidence == "runtime_metadata" and c2 == "pass":
        c2 = "partial"
        reasons.append("sender/receiver identity evidence is runtime metadata only")

    if c2 == "fail":
        claim_cap = "observability_only"
    elif c2 == "partial" or c4 == "partial":
        claim_cap = "partial_governance"
    else:
        claim_cap = "strong_segment_governance"

    return ContextBoundaryEvaluation(
        envelope_digest=envelope.payload_digest or digest_payload(envelope.to_payload()),
        condition_summary={"C2": c2, "C4": c4},
        claim_cap=claim_cap,
        reasons=tuple(reasons),
    )
