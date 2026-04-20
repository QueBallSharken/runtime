"""Coordinator-scoped mutation context guards.

These guards are intentionally small: they do not prove global authority, but
they prevent the known lower-level durable write and callable execution paths
from silently mutating state outside an explicit mutation context.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class MutationContext:
    actor: str
    operation: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.actor.strip():
            raise ValueError("mutation context requires actor")
        if not self.operation.strip():
            raise ValueError("mutation context requires operation")
        object.__setattr__(self, "evidence", tuple(str(item) for item in self.evidence))


class MutationContextRequired(PermissionError):
    """Raised when a guarded mutation is attempted without context."""


_current_mutation_context: ContextVar[MutationContext | None] = ContextVar(
    "halcyon_mutation_context",
    default=None,
)


def current_mutation_context() -> MutationContext | None:
    return _current_mutation_context.get()


@contextmanager
def mutation_context(
    *,
    actor: str,
    operation: str,
    evidence: tuple[str, ...] = (),
) -> Iterator[MutationContext]:
    context = MutationContext(actor=actor, operation=operation, evidence=evidence)
    token = _current_mutation_context.set(context)
    try:
        yield context
    finally:
        _current_mutation_context.reset(token)


def require_mutation_context(operation: str) -> MutationContext:
    context = current_mutation_context()
    if context is None:
        raise MutationContextRequired(f"{operation} requires mutation coordinator context")
    return context
