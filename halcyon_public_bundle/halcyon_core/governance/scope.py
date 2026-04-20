"""Scope helpers for turn-scoped governance claims.

RuntimeTurn final claims are emitted at chat-turn time. They describe the
governance evidence available for that turn, not later approval or execution
events that may be appended to the ledger.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable


class ScopeLimitation(str, Enum):
    """Machine-readable scope limitation codes for final claims."""

    MVL_TURN_SNAPSHOT = "runtime_turn_claim_is_turn_scoped_snapshot"
    POST_TURN_EXECUTION_REQUIRES_LEDGER = "post_turn_execution_requires_ledger"
    TOOL_EXECUTION_NOT_CLAIMED = "mvl_does_not_claim_tool_execution"
    STRONG_SBAC_TIMING_NOT_CLAIMED = "strong_sbac_timing_uninstrumented"
    TOOL_PROPOSALS_REFUSED_NOT_EXECUTED = "tool_proposals_refused_not_executed"


def runtime_turn_scope_limitations(tool_result_statuses: Iterable[str] = ()) -> tuple[str, ...]:
    """Return scope limitations for a RuntimeTurn final claim.

    The base limitations make the temporal boundary explicit: a RuntimeTurn
    claim is a snapshot. Later approval/execution evidence must be read from
    the ledger. Refused tool proposals get a separate signal so callers can
    distinguish "governance refused this" from "no tool was proposed."
    """

    limitations = [
        ScopeLimitation.MVL_TURN_SNAPSHOT.value,
        ScopeLimitation.POST_TURN_EXECUTION_REQUIRES_LEDGER.value,
        ScopeLimitation.TOOL_EXECUTION_NOT_CLAIMED.value,
        ScopeLimitation.STRONG_SBAC_TIMING_NOT_CLAIMED.value,
    ]
    if any(status == "refused" for status in tool_result_statuses):
        limitations.append(ScopeLimitation.TOOL_PROPOSALS_REFUSED_NOT_EXECUTED.value)
    return tuple(limitations)
