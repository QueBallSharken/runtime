"""Declarative tool registry."""

from __future__ import annotations

from typing import Any, Callable

from halcyon_core.tools.types import ToolSpec


class ToolRegistry:
    def __init__(self, specs: tuple[ToolSpec, ...] = ()) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._callables: dict[str, Callable[..., Any]] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec
        return spec

    def register_callable(self, name: str, fn: Callable[..., Any]) -> None:
        if name not in self._tools:
            raise KeyError(f"tool not registered: {name}")
        self._callables[name] = fn

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def get_callable(self, name: str) -> Callable[..., Any] | None:
        return self._callables.get(name)

    def list(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools[name] for name in sorted(self._tools))

    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.list())
