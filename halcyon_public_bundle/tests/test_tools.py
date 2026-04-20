import pytest

from halcyon_core.governance.abac import approval_for
from halcyon_core.governance.invariants import default_invariant
from halcyon_core.tools.executor import ToolExecutor
from halcyon_core.tools.registry import ToolRegistry
from halcyon_core.tools.types import MutationClass, ToolCall, ToolSpec, ToolResultStatus


def test_tool_call_digest_is_stable():
    left = ToolCall("lookup", payload={"b": 2, "a": 1})
    right = ToolCall("lookup", payload={"a": 1, "b": 2})

    assert left.digest() == right.digest()


def test_registry_registers_and_lists_specs():
    registry = ToolRegistry()
    registry.register(ToolSpec("lookup", "Read-only lookup", MutationClass.READ_ONLY, requires_approval=False))

    assert registry.get("lookup").name == "lookup"
    assert registry.names() == ("lookup",)


def test_registry_rejects_duplicate_tool_names():
    registry = ToolRegistry((ToolSpec("lookup", "first"),))

    with pytest.raises(ValueError):
        registry.register(ToolSpec("lookup", "second"))


def test_unknown_tool_returns_unknown_tool_result():
    result = ToolExecutor(ToolRegistry()).evaluate(ToolCall("missing"))

    assert result.status == ToolResultStatus.UNKNOWN_TOOL
    assert result.reason_code == "unknown_tool"


def test_disabled_tool_returns_disabled_result():
    registry = ToolRegistry((ToolSpec("lookup", "Read-only lookup", enabled=False),))

    result = ToolExecutor(registry).evaluate(ToolCall("lookup"))

    assert result.status == ToolResultStatus.DISABLED
    assert result.reason_code == "tool_disabled"


def test_read_only_tool_without_callable_is_approved_not_executed():
    registry = ToolRegistry((
        ToolSpec("lookup", "Read-only lookup", MutationClass.READ_ONLY, requires_approval=False),
    ))

    result = ToolExecutor(registry).evaluate(ToolCall("lookup", payload={"q": "status"}))

    assert result.status == ToolResultStatus.APPROVED_NOT_EXECUTED
    assert result.reason_code == "read_only_declared_no_callable_registered"
    assert result.gate_decision is None


def test_side_effecting_tool_without_approval_is_refused():
    registry = ToolRegistry((ToolSpec("write_file", "Write a file", MutationClass.FILESYSTEM),))

    result = ToolExecutor(registry).evaluate(ToolCall("write_file", payload={"path": "x.txt"}))

    assert result.status == ToolResultStatus.REFUSED
    assert result.reason_code == "abac_denied_by_default"
    assert result.gate_decision.status == "refuse"


def test_approval_digest_mismatch_is_refused():
    registry = ToolRegistry((ToolSpec("write_file", "Write a file", MutationClass.FILESYSTEM),))
    executor = ToolExecutor(registry, invariant=default_invariant())
    call = ToolCall("write_file", payload={"path": "x.txt"})
    different_call = ToolCall("write_file", payload={"path": "y.txt"})
    different_action = executor.action_for_call(different_call)
    approval = approval_for(different_action, approved_by="brad", approval_basis="test")

    result = executor.evaluate(call, approval=approval)

    assert result.status == ToolResultStatus.REFUSED
    assert result.reason_code == "approval_digest_mismatch"
    assert result.gate_decision.status == "halt"


def test_approved_side_effecting_tool_without_callable_is_not_executed():
    registry = ToolRegistry((ToolSpec("write_file", "Write a file", MutationClass.FILESYSTEM),))
    executor = ToolExecutor(registry, invariant=default_invariant())
    call = ToolCall("write_file", payload={"path": "x.txt"})
    action = executor.action_for_call(call)
    action = type(action)(
        action_type=action.action_type,
        target=action.target,
        payload=action.payload,
        mutation_class=action.mutation_class,
        actor=action.actor,
        invariant_digest=action.invariant_digest,
        irreversible_primitive="tool_callable",
        commit_threshold="reversible",
        metadata=action.metadata,
    )
    approval = approval_for(action, approved_by="brad", approval_basis="test")

    # The approval was bound to an action with commit topology. The executor's
    # default action is still incomplete, so this must not execute.
    result = executor.evaluate(call, approval=approval)

    assert result.status == ToolResultStatus.REFUSED
    assert result.reason_code == "approval_digest_mismatch"
