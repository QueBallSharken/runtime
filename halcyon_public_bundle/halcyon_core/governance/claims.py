"""Final governance claim classifier.

The classifier turns path evidence into the strongest honest governance claim.
It is deliberately conservative: missing evidence is not inferred as pass, and
breach notices override every nicer-looking local condition.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Final, Iterable, Literal

ConditionState = Literal["pass", "partial", "fail", "unknown", "disputed", "breach"]
ClaimClassification = Literal[
    "strong_full_path_governance",
    "strong_segment_governance",
    "partial_governance",
    "observability_only",
    "governance_breach",
]

CONDITIONS: Final[tuple[str, ...]] = ("C1", "C2", "C3", "C4", "C5", "C6")
CONDITION_TO_FAMILY: Final[dict[str, tuple[str, ...]]] = {
    "C2": ("F1",),
    "C1": ("F2",),
    "C3": ("F3",),
    "C5": ("F3",),
    "C4": ("F4", "F5"),
    "C6": ("F6",),
}
FAMILIES: Final[tuple[str, ...]] = ("F1", "F2", "F3", "F4", "F5", "F6")
STRONG_CLAIMS: Final[set[ClaimClassification]] = {
    "strong_full_path_governance",
    "strong_segment_governance",
}


@dataclass(frozen=True)
class ClaimEvidence:
    """Evidence available for one evaluated execution path."""

    execution_path_id: str
    conditions: dict[str, ConditionState] = field(default_factory=dict)
    breach_notices: tuple[str, ...] = ()
    detector_caps: tuple[str, ...] = ()
    scope_limitations: tuple[str, ...] = ()
    epistemic_taint: tuple[str, ...] = ()
    segment_documented: bool = False


@dataclass(frozen=True)
class FinalClaim:
    artifact_type: str
    execution_path_id: str
    classification: ClaimClassification
    bbis_score: int
    binary_override_triggered: bool
    condition_summary: dict[str, ConditionState]
    f_family_rollups: dict[str, int]
    epistemic_taint: tuple[str, ...]
    scope_limitations: tuple[str, ...]
    breach: bool
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ClaimClassifier:
    """Emit the strongest governance classification supported by evidence."""

    def classify(
        self,
        evidence: ClaimEvidence | dict[str, ConditionState],
        *,
        execution_path_id: str = "path_01",
    ) -> FinalClaim:
        if isinstance(evidence, dict):
            evidence = ClaimEvidence(execution_path_id=execution_path_id, conditions=evidence)

        conditions = normalize_conditions(evidence.conditions)
        reasons: list[str] = []
        breach = bool(evidence.breach_notices) or any(
            state == "breach" for state in conditions.values()
        )

        if breach:
            classification: ClaimClassification = "governance_breach"
            reasons.append("valid breach notice or breach condition present")
        elif any(state == "fail" for state in conditions.values()):
            classification = "observability_only"
            reasons.append("one or more relevant conditions failed")
        elif any(state == "unknown" for state in conditions.values()):
            classification = "observability_only"
            reasons.append("missing evidence is not inferred as pass")
        elif has_partial_or_disputed(conditions) or evidence.detector_caps:
            classification = classify_limited_path(evidence, conditions)
            reasons.extend(limitation_reasons(evidence, conditions))
        elif evidence.scope_limitations:
            classification = "strong_segment_governance"
            reasons.append("explicit scope limitation caps full-path claim")
        else:
            classification = "strong_full_path_governance"
            reasons.append("all relevant conditions passed with no caps or breach")

        f_family_rollups = rollup_families(conditions, classification, breach)
        binary_override = has_binary_override(conditions) or breach
        score = 0 if breach else sum(f_family_rollups.values())

        return FinalClaim(
            artifact_type="final_claim",
            execution_path_id=evidence.execution_path_id,
            classification=classification,
            bbis_score=score,
            binary_override_triggered=binary_override,
            condition_summary=conditions,
            f_family_rollups=f_family_rollups,
            epistemic_taint=evidence.epistemic_taint,
            scope_limitations=evidence.scope_limitations,
            breach=breach,
            reasons=tuple(reasons),
        )


def normalize_conditions(conditions: dict[str, ConditionState]) -> dict[str, ConditionState]:
    normalized: dict[str, ConditionState] = {}
    for condition in CONDITIONS:
        normalized[condition] = conditions.get(condition, "unknown")
    return normalized


def has_partial_or_disputed(conditions: dict[str, ConditionState]) -> bool:
    return any(state in {"partial", "disputed"} for state in conditions.values())


def classify_limited_path(
    evidence: ClaimEvidence, conditions: dict[str, ConditionState]
) -> ClaimClassification:
    c4_scope_break = conditions["C4"] in {"partial", "disputed"}
    detector_limited = bool(evidence.detector_caps)
    explicitly_scoped = evidence.segment_documented and bool(evidence.scope_limitations)

    if explicitly_scoped and (c4_scope_break or detector_limited):
        return "strong_segment_governance"
    return "partial_governance"


def limitation_reasons(
    evidence: ClaimEvidence, conditions: dict[str, ConditionState]
) -> tuple[str, ...]:
    reasons: list[str] = []
    limited = [name for name, state in conditions.items() if state in {"partial", "disputed"}]
    if limited:
        reasons.append("limited conditions: " + ",".join(limited))
    if evidence.detector_caps:
        reasons.append("detector evidence caps: " + ",".join(evidence.detector_caps))
    if evidence.segment_documented and evidence.scope_limitations:
        reasons.append("verified claim is limited to a documented segment")
    return tuple(reasons)


def rollup_families(
    conditions: dict[str, ConditionState],
    classification: ClaimClassification,
    breach: bool,
) -> dict[str, int]:
    if breach:
        return {family: 0 for family in FAMILIES}

    rollups = {family: 2 for family in FAMILIES}
    for condition, families in CONDITION_TO_FAMILY.items():
        score = condition_score(conditions[condition])
        for family in families:
            rollups[family] = min(rollups[family], score)

    rollups["F6"] = composition_score(classification)
    return rollups


def condition_score(state: ConditionState) -> int:
    if state == "pass":
        return 2
    if state in {"partial", "disputed"}:
        return 1
    return 0


def composition_score(classification: ClaimClassification) -> int:
    if classification == "strong_full_path_governance":
        return 2
    if classification in {"strong_segment_governance", "partial_governance"}:
        return 1
    return 0


def has_binary_override(conditions: dict[str, ConditionState]) -> bool:
    return any(
        state in {"fail", "breach"} and condition in CONDITION_TO_FAMILY
        for condition, state in conditions.items()
    )


def classify_claim(
    conditions: dict[str, ConditionState],
    *,
    execution_path_id: str = "path_01",
    breach_notices: Iterable[str] = (),
    detector_caps: Iterable[str] = (),
    scope_limitations: Iterable[str] = (),
    epistemic_taint: Iterable[str] = (),
    segment_documented: bool = False,
) -> FinalClaim:
    """Convenience wrapper for callers that do not need to build evidence."""

    return ClaimClassifier().classify(
        ClaimEvidence(
            execution_path_id=execution_path_id,
            conditions=conditions,
            breach_notices=tuple(breach_notices),
            detector_caps=tuple(detector_caps),
            scope_limitations=tuple(scope_limitations),
            epistemic_taint=tuple(epistemic_taint),
            segment_documented=segment_documented,
        )
    )
