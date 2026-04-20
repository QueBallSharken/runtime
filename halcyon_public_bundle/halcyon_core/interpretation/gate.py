"""Deterministic v1 interpretation and binding gate."""

from __future__ import annotations

import re

from halcyon_core.interpretation.types import (
    BindingRisk,
    CandidateBinding,
    CommitmentRisk,
    ConsequenceLevel,
    InterpretationArtifact,
    InterpretationStatus,
)

REFERENT_WORDS = frozenset({"it", "that", "this", "them", "these", "those"})
CONTEXTUAL_LEXICAL_DRIFT = frozenset({"patch", "ship", "green", "rip", "commit"})
BROAD_SCOPE_PHRASES = (
    "clean this up",
    "fix it",
    "fix this",
    "move on",
    "do the next thing",
    "handle it",
)
DURABLE_MEMORY_VERBS = frozenset({"remember", "save", "store", "pin"})
EXTERNAL_ACTION_VERBS = frozenset({"write", "move", "rename", "publish", "push", "install", "run", "execute"})
DESTRUCTIVE_VERBS = frozenset({"delete", "remove", "wipe", "overwrite", "kill"})
MUTATION_VERBS = DURABLE_MEMORY_VERBS | EXTERNAL_ACTION_VERBS | DESTRUCTIVE_VERBS | frozenset({"approve"})
CASUAL_OPERATIONAL_WORDS = frozenset({"rip", "ship", "green"})


def evaluate_interpretation(message: str, *, context_text: str = "") -> InterpretationArtifact:
    normalized = _normalize(message)
    tokens = tuple(re.findall(r"[a-z0-9_'-]+", normalized))
    token_set = set(tokens)

    binding_evidence: list[str] = []
    commitment_evidence: list[str] = []

    mutation_requested = bool(token_set & MUTATION_VERBS)
    durable_memory_requested = bool(token_set & DURABLE_MEMORY_VERBS)
    external_action_requested = bool(token_set & EXTERNAL_ACTION_VERBS)
    destructive_action_requested = bool(token_set & DESTRUCTIVE_VERBS)

    unclear_referent = False
    referents = tuple(word for word in tokens if word in REFERENT_WORDS)
    contextual_action = bool(token_set & CONTEXTUAL_LEXICAL_DRIFT)
    if referents and (mutation_requested or contextual_action or _has_broad_scope_phrase(normalized)):
        unclear_referent = True
        binding_evidence.append("referent requires resolution: " + ", ".join(sorted(set(referents))))

    lexical_terms = tuple(sorted(token_set & CONTEXTUAL_LEXICAL_DRIFT))
    lexical_drift = bool(lexical_terms and (mutation_requested or bool(context_text)))
    if lexical_drift:
        binding_evidence.append("context-sensitive term: " + ", ".join(lexical_terms))

    scope_uncertainty = _has_broad_scope_phrase(normalized)
    if scope_uncertainty:
        binding_evidence.append("broad or unclear task scope")

    frame_collision = bool(token_set & CASUAL_OPERATIONAL_WORDS) and mutation_requested
    if frame_collision:
        binding_evidence.append("casual wording also has operational reading")

    binding = BindingRisk(
        unclear_referent=unclear_referent,
        lexical_drift=lexical_drift,
        frame_collision=frame_collision,
        scope_uncertainty=scope_uncertainty,
        evidence=tuple(binding_evidence),
    )

    if durable_memory_requested:
        commitment_evidence.append("durable memory requested")
    if external_action_requested:
        commitment_evidence.append("external action requested")
    if destructive_action_requested:
        commitment_evidence.append("destructive action requested")

    consequence_level = _consequence_level(
        durable_memory_requested=durable_memory_requested,
        external_action_requested=external_action_requested,
        destructive_action_requested=destructive_action_requested,
    )
    commitment = CommitmentRisk(
        mutation_requested=mutation_requested,
        durable_memory_requested=durable_memory_requested,
        external_action_requested=external_action_requested,
        destructive_action_requested=destructive_action_requested,
        approval_required=consequence_level is not ConsequenceLevel.LOW,
        consequence_level=consequence_level,
        evidence=tuple(commitment_evidence),
    )

    if binding.has_any():
        status = InterpretationStatus.NEEDS_CLARIFICATION
        clarification = _clarification_prompt(binding)
        confirmation = None
    elif commitment.approval_required:
        status = InterpretationStatus.NEEDS_CONFIRMATION
        clarification = None
        confirmation = _confirmation_prompt(commitment)
    else:
        status = InterpretationStatus.STABLE
        clarification = None
        confirmation = None

    return InterpretationArtifact(
        status=status,
        interpreted_intent=message.strip(),
        ambiguity_types=binding.ambiguity_types(),
        binding_risk=binding,
        commitment_risk=commitment,
        candidate_bindings=_candidate_bindings(binding),
        clarification_prompt=clarification,
        confirmation_prompt=confirmation,
    )


def _normalize(message: str) -> str:
    return " ".join(message.lower().split())


def _has_broad_scope_phrase(normalized: str) -> bool:
    return any(phrase in normalized for phrase in BROAD_SCOPE_PHRASES)


def _consequence_level(
    *,
    durable_memory_requested: bool,
    external_action_requested: bool,
    destructive_action_requested: bool,
) -> ConsequenceLevel:
    if destructive_action_requested:
        return ConsequenceLevel.DESTRUCTIVE
    if external_action_requested:
        return ConsequenceLevel.HIGH
    if durable_memory_requested:
        return ConsequenceLevel.MEDIUM
    return ConsequenceLevel.LOW


def _clarification_prompt(binding: BindingRisk) -> str:
    if binding.unclear_referent:
        return "I need one clarification before I proceed: what exactly are you referring to?"
    if binding.scope_uncertainty:
        return "I need one clarification before I proceed: what scope should I apply this to?"
    if binding.lexical_drift:
        return "I need one clarification before I proceed: which meaning or context should I use here?"
    return "I need one clarification before I proceed: what did you mean?"


def _confirmation_prompt(commitment: CommitmentRisk) -> str:
    if commitment.destructive_action_requested:
        return "I understand the request, but it is destructive. Please confirm before I proceed."
    if commitment.external_action_requested:
        return "I understand the request, but it affects an external target. Please confirm before I proceed."
    if commitment.durable_memory_requested:
        return "I understand the request, but it would create durable memory. Please confirm before I proceed."
    return "I understand the request, but it needs confirmation before I proceed."


def _candidate_bindings(binding: BindingRisk) -> tuple[CandidateBinding, ...]:
    candidates: list[CandidateBinding] = []
    if binding.unclear_referent:
        candidates.append(CandidateBinding("unresolved referent", evidence=("referent word without explicit target",)))
    if binding.lexical_drift:
        candidates.append(CandidateBinding("context-sensitive term", evidence=("term has multiple runtime meanings",)))
    if binding.scope_uncertainty:
        candidates.append(CandidateBinding("unclear scope", evidence=("broad task phrase",)))
    if binding.frame_collision:
        candidates.append(CandidateBinding("frame collision", evidence=("casual phrasing overlaps operational command",)))
    return tuple(candidates)
