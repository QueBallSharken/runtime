"""
Maps rejection reasons to available next actions.
Isolated here so routing logic stays out of the coordinator.
"""

_ROUTING: dict[str, list[str]] = {
    "LIMIT_EXCEEDED": ["request_approval", "reduce_to_limit", "cancel"],
}

_FALLBACK: list[str] = ["cancel"]


def for_reason(reason: str) -> list[str]:
    return _ROUTING.get(reason, _FALLBACK)
