"""Schema linting for mutation-relevant runtime events.

This is a first-pass replay discipline guard. It validates the mutation events
that currently carry approval, refusal, execution, and durable-write causality;
it does not claim every runtime event is replay-ready yet.
"""

from __future__ import annotations

from typing import Any, Iterable

from halcyon_core.events import RuntimeEvent

MUTATION_EVENT_SCHEMA_VERSION = "mutation-event-v1"

MUTATION_EVENT_NAMES = frozenset({
    "tool.intent_binding.evaluated",
    "tool.intent_override.accepted",
    "tool.approval.granted",
    "tool.execution.completed",
    "tool.execution.refused",
    "memory.proposal.approved",
    "memory.proposal.rejected",
    "memory.durable.created",
})

PARENT_REQUIRED_EVENT_NAMES = frozenset({
    "tool.intent_binding.evaluated",
    "tool.intent_override.accepted",
    "tool.execution.completed",
    "tool.execution.refused",
    "memory.durable.created",
})

REQUIRED_MUTATION_PAYLOAD_FIELDS = frozenset({
    "schema_version",
    "reason_codes",
    "evidence",
    "source_events",
})


class MutationEventSchemaError(AssertionError):
    """Raised when a mutation event is missing replay-relevant fields."""


def is_mutation_event(event: RuntimeEvent) -> bool:
    return event.name in MUTATION_EVENT_NAMES


def mutation_event_record(event: RuntimeEvent) -> dict[str, Any]:
    return {
        "event_name": event.name,
        "event_index": event.index,
        "parent_index": event.parent_index,
        **event.payload,
    }


def validate_mutation_event(event: RuntimeEvent) -> None:
    if not is_mutation_event(event):
        return

    record = mutation_event_record(event)
    required_record_fields = {
        "event_name",
        "event_index",
        "parent_index",
        *REQUIRED_MUTATION_PAYLOAD_FIELDS,
    }
    missing = sorted(field for field in required_record_fields if field not in record)
    if missing:
        raise MutationEventSchemaError(f"{event.name} missing required fields: {missing}")

    if record["schema_version"] != MUTATION_EVENT_SCHEMA_VERSION:
        raise MutationEventSchemaError(
            f"{event.name} has unsupported schema_version: {record['schema_version']!r}"
        )
    if not isinstance(record["event_index"], int) or record["event_index"] <= 0:
        raise MutationEventSchemaError(f"{event.name} has invalid event_index")
    if event.name in PARENT_REQUIRED_EVENT_NAMES and record["parent_index"] is None:
        raise MutationEventSchemaError(f"{event.name} requires parent_index")
    if not _is_string_sequence(record["reason_codes"]):
        raise MutationEventSchemaError(f"{event.name} requires reason_codes as strings")
    if not isinstance(record["evidence"], (dict, list, tuple)):
        raise MutationEventSchemaError(f"{event.name} requires structured evidence")
    if not _is_int_sequence(record["source_events"]):
        raise MutationEventSchemaError(f"{event.name} requires source_events as integer indices")


def validate_mutation_events(events: Iterable[RuntimeEvent]) -> None:
    for event in events:
        validate_mutation_event(event)


def _is_string_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and bool(value) and all(
        isinstance(item, str) and item for item in value
    )


def _is_int_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and all(
        isinstance(item, int) for item in value
    )
