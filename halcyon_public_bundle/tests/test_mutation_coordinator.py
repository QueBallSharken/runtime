import ast
from pathlib import Path


API_SERVER = Path("halcyon_core/api/server.py")


def _function_source(function_name: str) -> str:
    text = API_SERVER.read_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            source = ast.get_source_segment(text, node)
            assert source is not None
            return source
    raise AssertionError(f"function not found: {function_name}")


def test_api_mutation_routes_delegate_to_coordinator():
    route_expectations = {
        "approve_memory_proposal": ("approve_memory_proposal", ("queue.approve", "bus.append", "SQLiteMemoryStore")),
        "reject_memory_proposal": ("reject_memory_proposal", ("queue.reject", "bus.append")),
        "approve_tool_proposal": ("approve_tool_proposal", ("executor.evaluate", "bus.append", "ApprovalRecord")),
    }

    for route_name, (coordinator_method, forbidden_tokens) in route_expectations.items():
        source = _function_source(route_name)
        assert "MutationCoordinator" in source
        assert f"coordinator.{coordinator_method}" in source
        for token in forbidden_tokens:
            assert token not in source
