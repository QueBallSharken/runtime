# Task: Runtime Health Adjudication (SBAC 2.0)

[x] Expand `state.py` with Live Telemetry
  - [x] Add `consecutive_refusals`, `recursion_depth`, `stall_count`
  - [x] Add `last_progress_ts`, `tool_attempts_this_turn`, `last_successful_action_ts`, `anomaly_score`

[x] Build `RuntimeGovernor` inside `governance/health.py`
  - [x] Rename `sbac.py` concepts into active adjudication mechanics
  - [x] Implement Graded Posture Logic (`HEALTHY`, `MILD_STRESS`, `MODERATE_STRESS`, `SEVERE_STRESS`, `CRITICAL`)
  - [x] Ensure `freeze_authority` triggers only on combined signals (refusals + stalls + depth)

[x] Wire Telemetry Collection into the `kernel.py`
  - [x] Update `evaluate_tool_proposals` to increment metrics dynamically inside the turn execution loop.
  - [x] Clear counters selectively upon progression or success

[x] Connect Governor to `tools/executor.py`
  - [x] Inject `RuntimeGovernor` pre-execution checks before ABAC/CBAC
  - [x] Map the generated posture to the corresponding restrictions (e.g., denying high-risk tools on MILD_STRESS)

[ ] Verification Testing
  - [ ] Test False Positive Persistence (User asking explicitly should not freeze)
  - [ ] Test Recovery (Cooldown resolving)
  - [ ] Test Adversarial Loop Freezing
