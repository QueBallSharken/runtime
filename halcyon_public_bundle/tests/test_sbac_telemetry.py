import pytest
from halcyon_core.state import HealthState, RuntimePosture
from halcyon_core.governance.health import RuntimeGovernor
from halcyon_core.tools.executor import ToolExecutor
from halcyon_core.tools.registry import ToolRegistry
from halcyon_core.tools.types import ToolCall, ToolResultStatus, MutationClass
from dataclasses import dataclass

def test_false_positive_persistence_does_not_freeze():
    """Two legitimate refusals from user requests should not freeze if reasoning remains stable."""
    governor = RuntimeGovernor()
    state = HealthState()
    
    state.consecutive_refusals = 1
    state.stall_count = 1
    posture = governor.adjudicate(state, 100.0)
    assert posture == RuntimePosture.MILD_STRESS
    
    state.consecutive_refusals = 2
    state.stall_count = 2
    posture = governor.adjudicate(state, 200.0)
    assert posture == RuntimePosture.MILD_STRESS

def test_adversarial_loop_freezes_authority():
    """Prompt repeatedly reintroduces denied tool request triggers severe stress freeze."""
    governor = RuntimeGovernor()
    state = HealthState()
    
    state.consecutive_refusals = 3
    state.stall_count = 3
    posture = governor.adjudicate(state, 300.0)
    
    assert posture == RuntimePosture.SEVERE_STRESS

def test_recovery_cooldown_lifts_freeze():
    """After cooldown / resolved turn, freeze lifts cleanly."""
    governor = RuntimeGovernor()
    state = HealthState()
    
    state.consecutive_refusals = 3
    state.stall_count = 3
    posture = governor.adjudicate(state, 300.0)
    assert posture == RuntimePosture.SEVERE_STRESS
    
    state.consecutive_refusals = 0
    state.stall_count = 0
    posture = governor.adjudicate(state, 400.0)
    assert posture == RuntimePosture.HEALTHY

def test_health_frozen_executor_terminates():
    """Executor natively enforces Severe Stress by refusing with health_frozen."""
    executor = ToolExecutor()
    call = ToolCall("write_file", {"content": "x"}, "model", "test")
    
    # We do not have write_file registered in this empty executor, but it's irrelevant,
    # because SECURE STRESS boundary evaluates before registry.
    result = executor.evaluate(call, posture=RuntimePosture.SEVERE_STRESS)
    
    assert result.status == ToolResultStatus.REFUSED
    assert result.reason_code == "health_frozen"

def test_health_critical_executor_terminates():
    """Executor natively enforces Critical Stress by refusing with health_critical."""
    executor = ToolExecutor()
    call = ToolCall("write_file", {"content": "x"}, "model", "test")
    
    result = executor.evaluate(call, posture=RuntimePosture.CRITICAL)
    
    assert result.status == ToolResultStatus.REFUSED
    assert result.reason_code == "health_critical"
