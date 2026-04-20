# C1-C4 Critical Governance Fixes

The four critical fixes to the BBIS architecture have been applied to `halcyon_core`, closing the inspected execution paths that were missing formal validation.

## Changes Made

- **C1 Mutation Guards**: Enforced the G1 invariant by adding `require_mutation_context` into the `MemoryProposalQueue.approve` methods (`halcyon_core/memory/proposals.py`), throwing a `MutationContextRequired` error on any backdoor attempts to save state outside of normal processes.
- **C2 Replay Label Corrections**: Stopped tools refused by the governance gate from being labeled `"approved_not_executed"`. They are now correctly tagged `"incomplete"` inside `halcyon_core/governance/replay.py`.
- **C3 Explicit Authority Claim Mapping**: Wired the missing explicit authority constraint (`C6`) into the governance path. ABAC now emits C6 in every action decision, and the kernel final claim consumes that condition so authority failures propagate through orchestration instead of stopping at classifier-level tests.
- **C4 Instrumenting State-Bounds Checks**: Passed runtime-derived `SBACEvidence` representations to the state-bounds audit layer within `halcyon_core/kernel.py`, eliminating the empty default-evidence gap and correctly triggering observation dependencies in `_final_claim`.

---

# Runtime Health Adjudication (SBAC 2.0)

We elevated SBAC from a passive POST-execution evidence tagger to an active PRE-execution circuit breaker. The kernel now monitors real-time system health and uses an adjudicator to aggressively restrict or terminate operation loops before action authorization occurs.

## Changes Made

- **Telemetry Tracing**: Expanded `HealthState` in `state.py` to continuously track `consecutive_refusals`, `stall_count`, `recursion_depth`, and session time.
- **Runtime Governor**: Created `halcyon_core/governance/health.py` containing the `RuntimeGovernor`, which maps telemetry pressures onto a graded `RuntimePosture` control ladder:
  - `HEALTHY`: Standard permissive execution.
  - `MILD_STRESS`: Automatically locks out high-risk ABAC tools.
  - `MODERATE_STRESS`: Requires human-in-the-loop explicit approval for *all* tools.
  - `SEVERE_STRESS`: Freezes all tool authority natively.
  - `CRITICAL`: Issues hard chain terminations for runaway processes.
- **Executor Preemption**: Rewrote `halcyon_core/tools/executor.py` so that posture constraints override any capability evaluation upfront (before ABAC/CBAC even attempt resolution). 
- **Persistent Runtime Telemetry**: The API now preserves `RuntimeState` across chat turns while keeping SQLite-backed kernels request-local, so health posture can accumulate without sharing database connections across request threads.

## Claim Boundary

The runtime health path is a governed software boundary. It supports claims about registered tool proposals, mutation-context enforcement, and ledger-backed execution evidence inside `halcyon_core`. It does not establish external host-primitive sandboxing, foundational model containment, or full raw-text egress filtering outside the registered runtime path.

## Validation Results

We executed the full suite of **191 structural tests**, including SBAC telemetry checks that verify the pre-execution restrictions engage during adversarial loops or repeated refusal pressure.

```bash
============================= test session starts ==============================
...
tests/test_sbac_telemetry.py::test_false_positive_persistence_does_not_freeze PASSED [ 20%]
tests/test_sbac_telemetry.py::test_adversarial_loop_freezes_authority PASSED [ 40%]
tests/test_sbac_telemetry.py::test_recovery_cooldown_lifts_freeze PASSED [ 60%]
tests/test_sbac_telemetry.py::test_health_frozen_executor_terminates PASSED [ 80%]
tests/test_sbac_telemetry.py::test_health_critical_executor_terminates PASSED [100%]
============================== 5 passed in 0.03s ===============================
```
