# decision_event_schema_v1

Canonical shape for all coordinator decisions. Frozen. Any addition must be optional and backward-compatible.

## Fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `timestamp` | ISO 8601 string | always | UTC, emitted at decision time |
| `status` | `"rejected"` \| `"executed"` | always | |
| `subject_id` | string | always | agent or user originating the proposal |
| `proposal_id` | int | always | |
| `policy_version` | string | always | human-readable version of loaded policy file |
| `policy_hash` | string | always | `sha256:<16-char prefix>` of raw policy file contents |
| `side_effect` | bool | always | `false` guarantees no external action was taken |
| `reason` | string | rejected only | rejection code, e.g. `LIMIT_EXCEEDED` |
| `requested` | number | rejected only | amount from proposal |
| `limit` | number | rejected only | limit from policy at decision time |

## Invariants

- `side_effect: false` on any `rejected` event is a hard guarantee — `execute()` was not called.
- `policy_hash` ties the decision to the exact policy file bytes loaded. Version alone is insufficient; the hash is the proof.
- Schema is validated at emission in `audit_log.emit()`. An invalid event raises before it reaches the log.

## Extension policy

Append optional fields only. Never remove or rename existing fields. Bump `SCHEMA_VERSION` in `schema.py` and document here when extending.

## Next planned extension

```json
"next_actions": ["request_approval", "reduce_to_limit", "cancel"]
```

Optional, present only on `rejected` events.
