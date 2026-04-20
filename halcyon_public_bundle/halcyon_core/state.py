"""Typed runtime state snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time

class RuntimePosture(str, Enum):
    HEALTHY = "healthy"
    MILD_STRESS = "mild_stress"
    MODERATE_STRESS = "moderate_stress"
    SEVERE_STRESS = "severe_stress"
    CRITICAL = "critical"


@dataclass
class IdentityState:
    name: str = "Halcyon"
    purpose: str = "local governed assistant runtime"
    active_tone: str = "neutral"


@dataclass
class AffectState:
    dominant: list[str] = field(default_factory=lambda: ["neutral"])
    energy: float = 0.0
    entropy: float = 0.0
    stage: str = "Calm"


@dataclass
class HealthState:
    containment: bool = False
    last_claim: str | None = None
    posture: RuntimePosture = RuntimePosture.HEALTHY
    pending_proposals: int = 0
    consecutive_refusals: int = 0
    recursion_depth: int = 0
    stall_count: int = 0
    tool_attempts_this_turn: int = 0
    last_progress_ts: float = field(default_factory=time.time)
    last_successful_action_ts: float = field(default_factory=time.time)
    anomaly_score: float = 0.0


@dataclass
class RuntimeState:
    identity: IdentityState = field(default_factory=IdentityState)
    affect: AffectState = field(default_factory=AffectState)
    health: HealthState = field(default_factory=HealthState)
