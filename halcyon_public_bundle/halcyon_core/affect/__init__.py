"""Bounded affect and salience metadata."""

from halcyon_core.affect.salience import (
    ImportanceKind,
    SalienceLevel,
    SalienceSignal,
    SalienceSource,
    evaluate_salience,
)
from halcyon_core.affect.state import AffectState, ArousalLevel, Valence

__all__ = [
    "AffectState",
    "ArousalLevel",
    "ImportanceKind",
    "SalienceLevel",
    "SalienceSignal",
    "SalienceSource",
    "Valence",
    "evaluate_salience",
]
