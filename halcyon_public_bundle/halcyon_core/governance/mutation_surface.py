"""Manual mutation surface inventory for claim-scoped governance.

This module is a first control, not proof of discovery completeness. It makes
known mutation-capable paths explicit so claims can stay scoped while the
MutationCoordinator and discovery hardening are still being built.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Literal

MutationType = Literal[
    "sql_write",
    "memory_write",
    "execution",
    "fs_write",
    "network",
    "process",
    "config",
]

SurfaceClassification = Literal[
    "governed",
    "read_only",
    "scaffold",
    "bypass_risk",
    "legacy_quarantined",
]


@dataclass(frozen=True)
class MutationSurface:
    name: str
    location: str
    mutation_type: MutationType
    classification: SurfaceClassification
    requires_tests: bool
    claim_safe: bool
    test_ids: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class MutationCandidate:
    file: str
    line: int
    snippet: str
    pattern: str


@dataclass(frozen=True)
class MutationCandidateExclusion:
    path_prefix: str
    pattern: str
    reason: str


MUTATION_PATTERNS: tuple[str, ...] = (
    "sqlite3.connect",
    ".execute(",
    ".executemany(",
    "open(",
    "write_text(",
    "write_bytes(",
    "subprocess.run",
    "Popen(",
    "urllib.request.urlopen",
    "request.urlopen",
)


EXCLUDED_CANDIDATES: tuple[MutationCandidateExclusion, ...] = (
    MutationCandidateExclusion(
        "tests/",
        "subprocess.run",
        "test harness process spawning is scaffold behavior",
    ),
    MutationCandidateExclusion(
        "tests/test_memory.py",
        "write_text(",
        "legacy import fixture writes temporary test data",
    ),
    MutationCandidateExclusion(
        "tests/test_memory.py",
        "open(",
        "false positive from test name containing reopen",
    ),
    MutationCandidateExclusion(
        "tests/test_mechasteve_runtime.py",
        "open(",
        "false positive from test name containing reopen",
    ),
    MutationCandidateExclusion(
        "tests/test_model.py",
        "urllib.request.urlopen",
        "test monkeypatch references network primitive without calling it",
    ),
    MutationCandidateExclusion(
        "tests/test_model.py",
        "request.urlopen",
        "test monkeypatch references network primitive without calling it",
    ),
    MutationCandidateExclusion(
        "halcyon_core/governance/mutation_surface.py",
        "*",
        "discovery helper contains literal mutation pattern strings",
    ),
)


MUTATION_SURFACES: tuple[MutationSurface, ...] = (
    MutationSurface(
        name="runtime_event_ledger_append",
        location="halcyon_core/events.py::SQLiteEventLedger.append",
        mutation_type="sql_write",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=("tests/test_events.py::test_sqlite_ledger_round_trip",),
        notes="Append-only event persistence is the current sequence authority.",
    ),
    MutationSurface(
        name="event_bus_append",
        location="halcyon_core/events.py::EventBus.append",
        mutation_type="sql_write",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=("tests/test_events.py::test_sqlite_ledger_round_trip",),
        notes="Routes runtime events through the ledger when a ledger is attached.",
    ),
    MutationSurface(
        name="runtime_event_ledger_open",
        location="halcyon_core/events.py::SQLiteEventLedger.__init__",
        mutation_type="sql_write",
        classification="scaffold",
        requires_tests=False,
        claim_safe=False,
        notes="Opens/creates the local SQLite ledger; infrastructure setup, not a governed mutation claim.",
    ),
    MutationSurface(
        name="runtime_event_ledger_schema_init",
        location="halcyon_core/events.py::SQLiteEventLedger._init_schema",
        mutation_type="sql_write",
        classification="scaffold",
        requires_tests=False,
        claim_safe=False,
        notes="Initializes ledger schema; infrastructure setup, not a governed mutation claim.",
    ),
    MutationSurface(
        name="runtime_event_ledger_latest_index_read",
        location="halcyon_core/events.py::SQLiteEventLedger.latest_index",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="runtime_event_ledger_iter_read",
        location="halcyon_core/events.py::SQLiteEventLedger.iter_events",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="runtime_event_ledger_iter_by_name_read",
        location="halcyon_core/events.py::SQLiteEventLedger.iter_events_by_name",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="runtime_event_ledger_get_read",
        location="halcyon_core/events.py::SQLiteEventLedger.get",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="memory_proposal_queue_open",
        location="halcyon_core/memory/proposals.py::SQLiteMemoryProposalQueue.__init__",
        mutation_type="sql_write",
        classification="scaffold",
        requires_tests=False,
        claim_safe=False,
        notes="Opens/creates the local SQLite proposal queue; infrastructure setup, not a governed mutation claim.",
    ),
    MutationSurface(
        name="memory_proposal_queue_schema_init",
        location="halcyon_core/memory/proposals.py::SQLiteMemoryProposalQueue._init_schema",
        mutation_type="sql_write",
        classification="scaffold",
        requires_tests=False,
        claim_safe=False,
        notes="Initializes proposal schema; infrastructure setup, not a governed mutation claim.",
    ),
    MutationSurface(
        name="memory_proposal_submit",
        location="halcyon_core/memory/proposals.py::SQLiteMemoryProposalQueue.submit",
        mutation_type="sql_write",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=(
            "tests/test_memory.py::test_sqlite_memory_proposal_queue_persists_pending_across_reopen",
            "tests/test_memory.py::test_memory_proposal_in_memory_submit_requires_mutation_context",
            "tests/test_memory.py::test_sqlite_memory_proposal_queue_submit_requires_mutation_context",
            "tests/test_memory.py::test_memory_proposal_submit_stale_context_is_denied",
            "tests/test_memory.py::test_memory_proposal_submit_with_active_context_succeeds",
        ),
        notes="Creates reviewable memory proposals rather than durable memory facts. Guard: require_mutation_context on both implementations.",
    ),
    MutationSurface(
        name="memory_proposal_queue_pending_read",
        location="halcyon_core/memory/proposals.py::SQLiteMemoryProposalQueue.pending",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="memory_proposal_queue_all_read",
        location="halcyon_core/memory/proposals.py::SQLiteMemoryProposalQueue.all",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="memory_proposal_queue_get_read",
        location="halcyon_core/memory/proposals.py::SQLiteMemoryProposalQueue.get",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="direct_memory_proposal_approve",
        location="halcyon_core/memory/proposals.py::SQLiteMemoryProposalQueue.approve",
        mutation_type="memory_write",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=(
            "tests/test_memory.py::test_memory_proposal_approve_requires_mutation_context",
            "tests/test_memory.py::test_sqlite_memory_proposal_queue_persists_approval_and_writes_memory",
        ),
        notes="Durable write inside direct proposal approval is guarded by mutation_context; unguarded approval fails before durable insert or decision update.",
    ),
    MutationSurface(
        name="direct_memory_proposal_reject",
        location="halcyon_core/memory/proposals.py::SQLiteMemoryProposalQueue.reject",
        mutation_type="sql_write",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=(
            "tests/test_memory.py::test_sqlite_memory_proposal_queue_reject_requires_mutation_context",
            "tests/test_memory.py::test_sqlite_memory_proposal_queue_persists_rejection",
        ),
        notes="Guarded by require_mutation_context at public surface; unguarded reject raises MutationContextRequired before any SQL write.",
    ),
    MutationSurface(
        name="direct_memory_proposal_update_decision",
        location="halcyon_core/memory/proposals.py::SQLiteMemoryProposalQueue._update_decision",
        mutation_type="sql_write",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=(
            "tests/test_memory.py::test_sqlite_memory_proposal_queue_reject_requires_mutation_context",
        ),
        notes="Private helper called only by approve() and reject(), both of which are now context-guarded at their public surfaces.",
    ),
    MutationSurface(
        name="durable_memory_store_open",
        location="halcyon_core/memory/store.py::SQLiteMemoryStore.__init__",
        mutation_type="sql_write",
        classification="scaffold",
        requires_tests=False,
        claim_safe=False,
        notes="Opens/creates the local SQLite memory store; infrastructure setup, not a governed mutation claim.",
    ),
    MutationSurface(
        name="durable_memory_store_schema_init",
        location="halcyon_core/memory/store.py::SQLiteMemoryStore._init_schema",
        mutation_type="sql_write",
        classification="scaffold",
        requires_tests=False,
        claim_safe=False,
        notes="Initializes memory schema; infrastructure setup, not a governed mutation claim.",
    ),
    MutationSurface(
        name="durable_memory_store_add",
        location="halcyon_core/memory/store.py::SQLiteMemoryStore.add",
        mutation_type="memory_write",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=(
            "tests/test_memory.py::test_memory_store_direct_add_requires_mutation_context",
            "tests/test_memory.py::test_memory_store_adds_and_retrieves_recent_items",
        ),
        notes="Direct durable memory write is guarded by mutation_context; unguarded writes fail before SQL insert.",
    ),
    MutationSurface(
        name="durable_memory_store_get_read",
        location="halcyon_core/memory/store.py::SQLiteMemoryStore.get",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="durable_memory_store_recent_read",
        location="halcyon_core/memory/store.py::SQLiteMemoryStore.recent",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="durable_memory_store_by_tag_read",
        location="halcyon_core/memory/store.py::SQLiteMemoryStore.by_tag",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="durable_memory_store_pinned_read",
        location="halcyon_core/memory/store.py::SQLiteMemoryStore.pinned",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="durable_memory_store_search_read",
        location="halcyon_core/memory/store.py::SQLiteMemoryStore.search_text",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="durable_memory_store_all_items_read",
        location="halcyon_core/memory/store.py::SQLiteMemoryStore.all_items",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="durable_memory_store_tags_read",
        location="halcyon_core/memory/store.py::SQLiteMemoryStore._tags_for",
        mutation_type="sql_write",
        classification="read_only",
        requires_tests=False,
        claim_safe=False,
        notes="Read-only SQL query matched by the static pattern scanner.",
    ),
    MutationSurface(
        name="api_memory_proposal_approve_client",
        location="halcyon_core/api/server.py::approve_memory_proposal",
        mutation_type="memory_write",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=("tests/test_mutation_coordinator.py::test_api_mutation_routes_delegate_to_coordinator",),
        notes="API route client for the coordinator-owned memory approval path.",
    ),
    MutationSurface(
        name="api_memory_proposal_reject_client",
        location="halcyon_core/api/server.py::reject_memory_proposal",
        mutation_type="sql_write",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=("tests/test_mutation_coordinator.py::test_api_mutation_routes_delegate_to_coordinator",),
        notes="API route client for the coordinator-owned memory rejection path.",
    ),
    MutationSurface(
        name="mutation_coordinator_memory_approve",
        location="halcyon_core/governance/mutation_coordinator.py::MutationCoordinator.approve_memory_proposal",
        mutation_type="memory_write",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=(
            "tests/test_api.py::test_api_memory_proposal_list_approve_reject",
            "tests/test_mutation_coordinator.py::test_api_mutation_routes_delegate_to_coordinator",
        ),
        notes="First coordinator-owned API-path memory approval/write seam; lower-level bypasses are still not eliminated.",
    ),
    MutationSurface(
        name="mutation_coordinator_memory_reject",
        location="halcyon_core/governance/mutation_coordinator.py::MutationCoordinator.reject_memory_proposal",
        mutation_type="sql_write",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=(
            "tests/test_api.py::test_api_memory_proposal_list_approve_reject",
            "tests/test_mutation_coordinator.py::test_api_mutation_routes_delegate_to_coordinator",
        ),
        notes="First coordinator-owned API-path memory rejection seam; lower-level bypasses are still not eliminated.",
    ),
    MutationSurface(
        name="kernel_memory_proposal_capture",
        location="halcyon_core/kernel.py::RuntimeKernel._capture_memory_proposals",
        mutation_type="sql_write",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=("tests/test_kernel.py::test_kernel_captures_memory_proposals_without_durable_write",),
        notes="Captures proposals only; durable memory writes still require review.",
    ),
    MutationSurface(
        name="kernel_tool_proposal_evaluation",
        location="halcyon_core/kernel.py::RuntimeKernel._evaluate_tool_proposals",
        mutation_type="execution",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=("tests/test_kernel.py::test_kernel_records_unknown_tool_proposals_without_execution",),
        notes="Evaluates model tool proposals through deny-by-default ABAC without approval.",
    ),
    MutationSurface(
        name="direct_tool_executor_evaluate",
        location="halcyon_core/tools/executor.py::ToolExecutor.evaluate",
        mutation_type="execution",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=(
            "tests/test_tool_execution.py::test_approved_callable_without_mutation_context_is_refused",
            "tests/test_tool_execution.py::test_approved_tool_with_callable_is_executed",
            "tests/test_tool_execution.py::test_callable_execution_stale_context_is_refused",
            "tests/test_tool_execution.py::test_callable_execution_indirect_call_without_context_is_refused",
            "tests/test_tool_execution.py::test_callable_execution_indirect_call_inherits_active_context",
        ),
        notes="Soft gate: require_mutation_context at callable invocation site returns REFUSED (not raise) — executor is an evaluation layer. Stale context and nested indirect paths proven refused.",
    ),
    MutationSurface(
        name="api_tool_proposal_approve_client",
        location="halcyon_core/api/server.py::approve_tool_proposal",
        mutation_type="execution",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=("tests/test_mutation_coordinator.py::test_api_mutation_routes_delegate_to_coordinator",),
        notes="API route client for the coordinator-owned tool approval/execution path.",
    ),
    MutationSurface(
        name="mutation_coordinator_tool_approve_execute",
        location="halcyon_core/governance/mutation_coordinator.py::MutationCoordinator.approve_tool_proposal",
        mutation_type="execution",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=(
            "tests/test_tool_execution.py::test_api_tool_proposal_approve_executes_and_stores_evidence",
            "tests/test_tool_execution.py::test_api_tool_approval_event_precedes_execution_and_links_digest",
            "tests/test_mutation_coordinator.py::test_api_mutation_routes_delegate_to_coordinator",
        ),
        notes="Coordinator-owned API-path tool approval/execution seam; direct executor callable execution is now context-guarded.",
    ),
    MutationSurface(
        name="local_model_client_http_post",
        location="halcyon_core/model/client.py::LocalModelClient._http_post",
        mutation_type="network",
        classification="governed",
        requires_tests=True,
        claim_safe=True,
        test_ids=("tests/test_model.py::test_local_model_client_posts_chat_completion_payload_and_parses_intent",),
        notes="Outbound call is scoped to local OpenAI-compatible model interaction.",
    ),
    MutationSurface(
        name="cli_subprocess_smoke_tests",
        location="tests/test_cli.py and tests/test_examples.py",
        mutation_type="process",
        classification="scaffold",
        requires_tests=False,
        claim_safe=False,
        notes="Test harness process spawning is scaffold behavior, not runtime governance capability.",
    ),
)


class MutationSurfaceInventoryError(AssertionError):
    """Raised when the manual mutation surface inventory violates claim rules."""


def validate_mutation_surface(surface: MutationSurface) -> None:
    if surface.classification == "governed":
        if not surface.requires_tests:
            raise MutationSurfaceInventoryError(
                f"governed surface requires tests: {surface.name}"
            )
        if not surface.claim_safe:
            raise MutationSurfaceInventoryError(
                f"governed surface must be claim-safe: {surface.name}"
            )
        if not surface.test_ids:
            raise MutationSurfaceInventoryError(
                f"governed surface needs at least one test id: {surface.name}"
            )

    if surface.classification in {"bypass_risk", "legacy_quarantined", "scaffold"} and surface.claim_safe:
        raise MutationSurfaceInventoryError(
            f"{surface.classification} surface cannot be claim-safe: {surface.name}"
        )

    if not surface.mutation_type:
        raise MutationSurfaceInventoryError(
            f"unknown mutation type is not allowed: {surface.name}"
        )

    if not surface.classification:
        raise MutationSurfaceInventoryError(
            f"unknown mutation surface classification is not allowed: {surface.name}"
        )


def validate_mutation_surface_inventory(
    surfaces: tuple[MutationSurface, ...] = MUTATION_SURFACES,
) -> None:
    names: set[str] = set()
    locations: set[str] = set()
    for surface in surfaces:
        if surface.name in names:
            raise MutationSurfaceInventoryError(f"duplicate mutation surface name: {surface.name}")
        if surface.location in locations:
            raise MutationSurfaceInventoryError(
                f"duplicate mutation surface location: {surface.location}"
            )
        names.add(surface.name)
        locations.add(surface.location)
        validate_mutation_surface(surface)


def governed_surfaces() -> tuple[MutationSurface, ...]:
    return tuple(surface for surface in MUTATION_SURFACES if surface.classification == "governed")


def non_claim_safe_surfaces() -> tuple[MutationSurface, ...]:
    return tuple(surface for surface in MUTATION_SURFACES if not surface.claim_safe)


def scan_for_mutation_candidates(
    roots: Iterable[str | Path] = ("halcyon_core", "tests"),
    *,
    patterns: tuple[str, ...] = MUTATION_PATTERNS,
) -> tuple[MutationCandidate, ...]:
    candidates: list[MutationCandidate] = []
    for root in roots:
        root_path = Path(root)
        if root_path.is_file():
            files = (root_path,)
        else:
            files = tuple(
                path
                for path in root_path.rglob("*.py")
                if "__pycache__" not in path.parts
            )
        for file_path in files:
            text = file_path.read_text(encoding="utf-8")
            for line_number, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                for pattern in patterns:
                    if pattern in stripped:
                        candidates.append(
                            MutationCandidate(
                                file=file_path.as_posix(),
                                line=line_number,
                                snippet=stripped,
                                pattern=pattern,
                            )
                        )
    return tuple(candidates)


def candidate_matches_inventory(
    candidate: MutationCandidate,
    surfaces: tuple[MutationSurface, ...] = MUTATION_SURFACES,
) -> bool:
    # First-pass matching is function/class scoped. It is still syntactic, not
    # semantic proof, but a new obvious primitive in an uninventoried function
    # must be classified or explicitly excluded.
    for surface in surfaces:
        surface_file, surface_qualname = _surface_location_parts(surface)
        if surface_file != candidate.file:
            continue
        line_range = _python_qualname_line_range(surface_file, surface_qualname)
        if line_range is None:
            continue
        start, end = line_range
        if start <= candidate.line <= end:
            return True
    return False


def candidate_is_excluded(
    candidate: MutationCandidate,
    exclusions: tuple[MutationCandidateExclusion, ...] = EXCLUDED_CANDIDATES,
) -> bool:
    return any(
        exclusion.reason
        and candidate.file.startswith(exclusion.path_prefix)
        and (exclusion.pattern == "*" or exclusion.pattern == candidate.pattern)
        for exclusion in exclusions
    )


def unclassified_mutation_candidates(
    candidates: tuple[MutationCandidate, ...] | None = None,
    surfaces: tuple[MutationSurface, ...] = MUTATION_SURFACES,
    exclusions: tuple[MutationCandidateExclusion, ...] = EXCLUDED_CANDIDATES,
) -> tuple[MutationCandidate, ...]:
    candidates = candidates if candidates is not None else scan_for_mutation_candidates()
    return tuple(
        candidate
        for candidate in candidates
        if not candidate_matches_inventory(candidate, surfaces)
        and not candidate_is_excluded(candidate, exclusions)
    )


def validate_candidate_exclusions(
    exclusions: tuple[MutationCandidateExclusion, ...] = EXCLUDED_CANDIDATES,
) -> None:
    for exclusion in exclusions:
        if not exclusion.path_prefix:
            raise MutationSurfaceInventoryError("candidate exclusion requires path_prefix")
        if not exclusion.pattern:
            raise MutationSurfaceInventoryError("candidate exclusion requires pattern")
        if not exclusion.reason:
            raise MutationSurfaceInventoryError(
                f"candidate exclusion requires reason: {exclusion.path_prefix} {exclusion.pattern}"
            )


def _surface_location_parts(surface: MutationSurface) -> tuple[str, str]:
    file_name, _, qualname = surface.location.partition("::")
    return file_name, qualname


@lru_cache(maxsize=None)
def _python_qualname_line_range(file_name: str, qualname: str) -> tuple[int, int] | None:
    if not qualname:
        return None
    path = Path(file_name)
    if not path.exists():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    ranges: dict[str, tuple[int, int]] = {}

    def visit_body(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}.{node.name}" if prefix else node.name
                ranges[name] = (node.lineno, getattr(node, "end_lineno", node.lineno))
                visit_body(node.body, name)

    visit_body(tree.body)
    return ranges.get(qualname)
