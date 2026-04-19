"""
decision_event_schema_v1 — canonical shape for all coordinator decisions.
Any field added after this freeze must be optional and backward-compatible.
"""

SCHEMA_VERSION = "decision_event_schema_v1"

# Required on every event regardless of status.
REQUIRED_FIELDS = {
    "timestamp",
    "status",
    "subject_id",
    "proposal_id",
    "policy_version",
    "policy_hash",
    "side_effect",
}

# Required only when status == "rejected".
REJECTION_FIELDS = {"reason", "requested", "limit"}

VALID_STATUSES = {"rejected", "executed"}


def validate(event: dict) -> None:
    missing = REQUIRED_FIELDS - event.keys()
    if missing:
        raise ValueError(f"Event missing required fields: {sorted(missing)}")

    if event["status"] not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{event['status']}', must be one of {VALID_STATUSES}")

    if event["status"] == "rejected":
        missing_rejection = REJECTION_FIELDS - event.keys()
        if missing_rejection:
            raise ValueError(f"Rejected event missing fields: {sorted(missing_rejection)}")

    if event["status"] == "executed" and "next_actions" in event:
        raise ValueError("next_actions must not appear on executed events")
