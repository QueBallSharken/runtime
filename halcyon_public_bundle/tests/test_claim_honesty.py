"""Isolate the RuntimeTurn claim honesty gap.

The question: can final_claim imply execution happened when it didn't?

Findings:
  1. A turn with a refused tool produces the same claim classification
     as a turn with no tools at all — the claim cannot distinguish them.
  2. A turn with a refused tool that is later approved+executed via the
     approval endpoint: the RuntimeTurn's final_claim never updates.
     Execution evidence lives only in the ledger.
  3. The scope_limitations field carries the disclaimer ("MVL does not claim
     tool execution") but the classification field alone — strong_segment_governance —
     does not signal "tool execution is pending approval."

Fix applied here: when any tool proposal is governed by REFUSED status,
the kernel adds "tool_proposals_refused_not_executed" to scope_limitations so that
callers reading only the claim cannot mistake governance-of-refusal for governance-of-execution.
The claim also declares that it is a turn-scoped snapshot; later execution evidence
belongs in the append-only ledger, not in the frozen RuntimeTurn.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from halcyon_core.api.server import create_app
from halcyon_core.config import RuntimeConfig
from halcyon_core.governance.claims import ClaimClassifier, ClaimEvidence
from halcyon_core.kernel import RuntimeKernel, build_kernel_for_path
from halcyon_core.governance.scope import ScopeLimitation, runtime_turn_scope_limitations
from halcyon_core.model.client import LocalModelClient


from halcyon_core.tools.registry import ToolRegistry
from halcyon_core.tools.types import MutationClass, ToolResultStatus, ToolSpec


def fake_client(content: str):
    return LocalModelClient(
        "http://local.test/v1",
        "fake",
        transport=lambda url, payload: {"choices": [{"message": {"content": content}}]},
    )


def reply(tool_name: str | None = None) -> str:
    tools = []
    if tool_name:
        tools = [{"action_type": tool_name, "target": tool_name,
                  "payload": {"message": "hi"}, "mutation_class": "external", "reason": "test"}]
    return json.dumps({"reply": "ok", "memory_proposals": [], "tool_proposals": tools})


def kernel_with_echo(path, model_client):
    """Kernel with the echo tool registered so proposals are REFUSED, not UNKNOWN."""
    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "Echo", MutationClass.EXTERNAL, commit_threshold="reversible"))
    registry.register_callable("echo", lambda message="", **_: {"echoed": message})
    config = RuntimeConfig(database_path=str(path))
    return RuntimeKernel(config=config, model_client=model_client, tool_registry=registry)


# ---------------------------------------------------------------------------
# Gap 1: refused tool and no-tool turns produce identical claim classifications
# ---------------------------------------------------------------------------

def test_runtime_turn_scope_limitations_are_named_and_temporal():
    limitations = runtime_turn_scope_limitations(())

    assert ScopeLimitation.MVL_TURN_SNAPSHOT.value in limitations
    assert ScopeLimitation.POST_TURN_EXECUTION_REQUIRES_LEDGER.value in limitations
    assert ScopeLimitation.TOOL_EXECUTION_NOT_CLAIMED.value in limitations
    assert ScopeLimitation.STRONG_SBAC_TIMING_NOT_CLAIMED.value in limitations
    assert ScopeLimitation.TOOL_PROPOSALS_REFUSED_NOT_EXECUTED.value not in limitations


def test_refused_status_adds_distinct_scope_limitation():
    limitations = runtime_turn_scope_limitations((ToolResultStatus.UNKNOWN_TOOL.value, ToolResultStatus.REFUSED.value))

    assert ScopeLimitation.TOOL_PROPOSALS_REFUSED_NOT_EXECUTED.value in limitations


def test_refused_tool_and_no_tool_produce_same_classification(tmp_path):
    """This is the lie. The claim can't distinguish refused-tool from no-tool."""
    no_tool_kernel = build_kernel_for_path(
        tmp_path / "a.sqlite",
        model_client=fake_client(reply()),
    )
    refused_kernel = kernel_with_echo(
        tmp_path / "b.sqlite",
        model_client=fake_client(reply("echo")),
    )

    no_tool_turn = no_tool_kernel.handle_message("hello")
    refused_turn = refused_kernel.handle_message("echo hi")

    no_tool_kernel.close()
    refused_kernel.close()

    # Both produce strong_segment_governance — indistinguishable from the claim alone
    assert no_tool_turn.final_claim.classification == refused_turn.final_claim.classification

    # The refused turn DID have tool results — the claim does not reflect this
    assert refused_turn.tool_results[0].reason_code == "abac_denied_by_default"
    assert no_tool_turn.tool_results == ()


# ---------------------------------------------------------------------------
# Gap 2: claim is frozen at turn time; deferred execution is invisible to it
# ---------------------------------------------------------------------------

def test_final_claim_does_not_update_after_deferred_execution(tmp_path):
    """The RuntimeTurn claim is a snapshot. Execution via the approval endpoint
    is not reflected in final_claim — only in the ledger."""
    config = RuntimeConfig(database_path=str(tmp_path / "halcyon.sqlite"))
    app = create_app(config, model_client=fake_client(reply("echo")))
    client = TestClient(app)

    turn_response = client.post("/chat", json={"message": "echo hi"}).json()
    claim_before = turn_response["final_claim"]["classification"]
    call_digest = turn_response["tool_results"][0]["call_digest"]

    # Approve and execute
    exec_response = client.post(
        f"/tools/proposals/{call_digest}/approve",
        json={"approved_by": "brad", "approval_basis": "test"},
    )
    assert exec_response.status_code == 200
    assert exec_response.json()["status"] == "executed"

    # The original turn's final_claim has not changed — it still reflects
    # the state at chat time (refused), not the post-approval execution
    assert turn_response["final_claim"]["classification"] == claim_before


# ---------------------------------------------------------------------------
# Fix: refused tool proposals MUST leave a distinct signal in the claim
# ---------------------------------------------------------------------------

def test_refused_tool_turn_scope_limitations_signal_no_execution(tmp_path):
    """After the fix: a turn with refused tool proposals declares
    'tool_proposals_refused_not_executed' in scope_limitations so callers
    cannot read the claim and infer execution happened."""
    kernel = kernel_with_echo(
        tmp_path / "halcyon.sqlite",
        model_client=fake_client(reply("echo")),
    )
    turn = kernel.handle_message("echo hi")
    kernel.close()

    assert turn.tool_results[0].reason_code == "abac_denied_by_default"
    assert ScopeLimitation.TOOL_PROPOSALS_REFUSED_NOT_EXECUTED.value in turn.final_claim.scope_limitations


def test_no_tool_turn_does_not_add_refused_signal(tmp_path):
    """When no tools were proposed, the signal is absent — preserving
    the distinction between 'refused' and 'not proposed'."""
    kernel = build_kernel_for_path(
        tmp_path / "halcyon.sqlite",
        model_client=fake_client(reply()),
    )
    turn = kernel.handle_message("hello")
    kernel.close()

    assert turn.tool_results == ()
    assert ScopeLimitation.TOOL_PROPOSALS_REFUSED_NOT_EXECUTED.value not in turn.final_claim.scope_limitations


def test_refused_signal_is_gated_on_refused_status_not_mere_presence(tmp_path):
    """The signal appears only for REFUSED results — not for UNKNOWN_TOOL,
    which means an unregistered tool proposal does NOT trigger it."""
    # Use a bare kernel with no registry so the tool is unknown (not refused).
    kernel = build_kernel_for_path(
        tmp_path / "halcyon.sqlite",
        model_client=fake_client(reply("unregistered_tool")),
    )
    turn = kernel.handle_message("do the thing")
    kernel.close()

    assert turn.tool_results[0].status == ToolResultStatus.UNKNOWN_TOOL
    # Unknown tool is a different failure mode — not a governed refusal
    assert ScopeLimitation.TOOL_PROPOSALS_REFUSED_NOT_EXECUTED.value not in turn.final_claim.scope_limitations
