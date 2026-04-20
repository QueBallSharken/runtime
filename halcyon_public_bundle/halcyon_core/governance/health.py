"""Runtime Health Adjudication and State Boundary Audit Chain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from halcyon_core.state import HealthState, RuntimePosture

ConditionState = Literal["pass", "partial", "fail", "unknown"]
DetectorTrust = Literal["none", "heuristic", "indirect_instrumentation", "activation_level", "architecture_bound_verified"]
TimingRelation = Literal["before_commitment", "after_commitment", "unknown"]

SBAC_MVP_STATUS = "partial"

@dataclass(frozen=True)
class SBACEvidence:
    detector_trust: DetectorTrust = "none"
    timing_relation: TimingRelation = "unknown"
    commitment_model_declared: bool = False

class RuntimeGovernor:
    """Evaluates telemetric health and forces graded control postures."""
    def __init__(self, run_budget_ms: float = 30000.0, recursion_limit: int = 6):
        self.run_budget_ms = run_budget_ms
        self.recursion_limit = recursion_limit
        
    def adjudicate(self, state: HealthState, current_wall_clock_ms: float) -> RuntimePosture:
        if current_wall_clock_ms > self.run_budget_ms and state.stall_count > 0:
            return RuntimePosture.CRITICAL
            
        if state.recursion_depth > self.recursion_limit and state.consecutive_refusals >= 2:
            return RuntimePosture.SEVERE_STRESS
            
        if state.consecutive_refusals >= 3 and state.stall_count > 0:
            return RuntimePosture.SEVERE_STRESS
            
        if state.recursion_depth > 3 or state.anomaly_score > 0.8:
            return RuntimePosture.MODERATE_STRESS
            
        if state.consecutive_refusals > 0 or state.anomaly_score > 0.5:
            return RuntimePosture.MILD_STRESS
            
        return RuntimePosture.HEALTHY

def evaluate_health(evidence: SBACEvidence | None = None) -> dict[str, ConditionState]:
    evidence = evidence or SBACEvidence()
    if evidence.timing_relation == "after_commitment":
        return {"C5": "fail"}
    if (
        evidence.timing_relation == "before_commitment"
        and evidence.commitment_model_declared
        and evidence.detector_trust in {"activation_level", "architecture_bound_verified"}
    ):
        return {"C5": "pass"}
    if evidence.timing_relation == "before_commitment" or evidence.commitment_model_declared:
        return {"C5": "partial"}
    return {"C5": "unknown"}
