# Goal: Runtime Health Adjudication (SBAC 2.0)

Transform the currently blind `SBAC` (State-Based Access Control) module from a placeholder into a live, aggressive runtime health adjudicator that throttles and terminates actions based on local telemetry.

## System Interventions

### 1. `halcyon_core/state.py` (The Telemetry Bus)
We expand `HealthState` to track execution health across multiple turns natively.
- `consecutive_refusals: int`: Tracks back-to-back constraint violations.
- `recursion_depth: int`: Tracks repeated loops without resolving the user's intent.
- `wall_clock_ms: float`: Accumulator for current chain duration.
- `anomaly_score: float`: Weighted heuristic value reflecting general system health.
- `freeze_authority: bool`: A hard toggle flipped by SBAC when thresholds are breached.

### 2. `halcyon_core/kernel.py` (The Collector)
The kernel must increment and reset these boundaries on each evaluation.
- Update `_evaluate_tool_proposals` to increment `state.health.consecutive_refusals` when an ABAC/CBAC deny hits, and reset it on `executed`.
- Update the timer at the start and end of turn processing to calculate `wall_clock_ms`.

### 3. `halcyon_core/governance/sbac.py` (The Judge)
Redesign `evaluate_sbac` into the `HealthAdjudicator`.
We introduce concrete rules:
- **Authority Freeze Rule**: `if recursion_depth > 6 and consecutive_refusals >= 2:` -> trigger global tool refusal.
- **Run-away Process Rule**: `if wall_clock_ms > BUDGET:` -> deny processing.
Instead of returning `C5` unknowns, SBAC will return a fast-fail exception or a distinct denial state that structurally stops the `ToolExecutor`.

### 4. `halcyon_core/tools/executor.py` (The Enforcer)
Modify the gate evaluation logic so that before evaluating `ABAC` or `CBAC`, the executor checks `state.health.freeze_authority`. If the runtime health is critically degraded, it halts execution immediately without evaluating tool viability.

## User Review Required
> [!IMPORTANT]
> The current SBAC model evaluated state *after* the tool proposals to mark the post-execution claim with `C5`. By shifting this to a pre-execution **Health Adjudication**, SBAC acts alongside ABAC, literally overriding authorization bounds based on state pressure. Are we comfortable making SBAC an aggressive pre-execution gate layer rather than just post-execution context tagging?

## Verification
- We will add testing (`tests/test_sbac_telemetry.py`) proving that 2 loop failures correctly forces an `Authority Freeze`, preventing a 3rd proposal entirely.
