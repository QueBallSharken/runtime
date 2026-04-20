"""Local FastAPI surface for the Halcyon MVL."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from halcyon_core.config import RuntimeConfig
from halcyon_core.events import SQLiteEventLedger
from halcyon_core.governance.mutation_coordinator import (
    MutationConflictError,
    MutationCoordinator,
    MutationNotFoundError,
    MutationUnprocessableError,
    ToolExecutionRefused,
    ToolIntentBindingRequired,
)
from halcyon_core.kernel import RuntimeKernel
from halcyon_core.maintenance import create_maintenance_report, create_pulse_snapshot, find_repairs
from halcyon_core.memory.proposals import MemoryProposal, SQLiteMemoryProposalQueue
from halcyon_core.model.client import LocalModelClient, ModelClientError
from halcyon_core.state import RuntimeState
from halcyon_core.tools.registry import ToolRegistry
from halcyon_core.tools.types import MutationClass, ToolSpec

RUNTIME_VERSION = "v0.1"
SPEC_VERSION = "mvl-v0.1"
GOVERNANCE_MODE = "deny_by_default"
STATIC_DIR = Path(__file__).resolve().parent / "static"
HUD_HTML = STATIC_DIR / "hud.html"


class ChatRequest(BaseModel):
    message: str
    memory_tags: list[str] = Field(default_factory=list)


class DecisionRequest(BaseModel):
    decided_by: str
    reason: str = "approved"


class ToolApprovalRequest(BaseModel):
    approved_by: str
    approval_basis: str = "user_direct_consent"


def _build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolSpec(
        "echo",
        "Echo a message back. No side effects, fully reversible.",
        MutationClass.EXTERNAL,
        commit_threshold="reversible",
    ))
    registry.register_callable("echo", lambda message="", **_: {"echoed": message})
    return registry


def create_app(
    config: RuntimeConfig | None = None,
    *,
    model_client: LocalModelClient | None = None,
    tool_registry: ToolRegistry | None = None,
) -> FastAPI:
    config = config or RuntimeConfig()
    app = FastAPI(title="Halcyon Local API", version="0.1.0")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.state.config = config
    app.state.model_client = model_client
    app.state.tool_registry = tool_registry or _build_default_registry()
    app.state.runtime_state = RuntimeState()

    @app.get("/", include_in_schema=False)
    @app.get("/hud", include_in_schema=False)
    def serve_hud() -> FileResponse:
        return FileResponse(HUD_HTML)

    @app.get("/health")
    def health() -> dict[str, Any]:
        queue = SQLiteMemoryProposalQueue(config.proposal_database_path())
        ledger = SQLiteEventLedger(config.events_database_path())
        try:
            pending_count = len(queue.pending())
            latest_index = ledger.latest_index()
        finally:
            queue.close()
            ledger.close()
        return {
            "status": "ok",
            "runtime": "halcyon_core",
            "runtime_version": RUNTIME_VERSION,
            "spec_version": SPEC_VERSION,
            "database_path": str(Path(config.database_path)),
            "model_base_url": config.model_base_url,
            "model": config.model_name,
            "pending_proposals": pending_count,
            "latest_event_index": latest_index,
            "governance_mode": GOVERNANCE_MODE,
            "runtime_posture": app.state.runtime_state.health.posture.value,
        }

    @app.post("/chat")
    def chat(request: ChatRequest) -> dict[str, Any]:
        kernel = RuntimeKernel(
            config=config,
            model_client=app.state.model_client,
            tool_registry=app.state.tool_registry,
        )
        kernel.state = app.state.runtime_state
        try:
            turn = kernel.handle_message(request.message, memory_tags=tuple(request.memory_tags))
            app.state.runtime_state = kernel.state
            turn_data = turn.to_dict()
            interpretation = turn_data.get("interpretation")
            status = interpretation.get("status") if interpretation else "stable"
            return {
                "turn": turn_data,
                "status": status,
                "interpretation": interpretation,
                "memory_proposals": turn_data.get("memory_proposals", []),
                "tool_results": turn_data.get("tool_results", []),
                "final_claim": turn_data.get("final_claim"),
            }
        except ModelClientError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "reason_code": "model_endpoint_error",
                    "evidence": [exc.to_dict()],
                    "message": "model endpoint failed",
                },
            ) from exc
        finally:
            kernel.close()

    @app.get("/memory/proposals")
    def list_memory_proposals(all: bool = False) -> dict[str, Any]:
        queue = SQLiteMemoryProposalQueue(config.proposal_database_path())
        try:
            proposals = queue.all() if all else queue.pending()
            return {"proposals": [_proposal_to_dict(proposal) for proposal in proposals]}
        finally:
            queue.close()

    @app.post("/memory/proposals/{proposal_id}/approve")
    def approve_memory_proposal(proposal_id: str, request: DecisionRequest) -> dict[str, Any]:
        coordinator = MutationCoordinator(config, tool_registry=app.state.tool_registry)
        try:
            result = coordinator.approve_memory_proposal(
                proposal_id,
                decided_by=request.decided_by,
                decision_reason=request.reason,
            )
            return {"status": "approved", "proposal_id": proposal_id, "memory_id": result.memory_id}
        except MutationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except MutationConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/memory/proposals/{proposal_id}/reject")
    def reject_memory_proposal(proposal_id: str, request: DecisionRequest) -> dict[str, Any]:
        coordinator = MutationCoordinator(config, tool_registry=app.state.tool_registry)
        try:
            result = coordinator.reject_memory_proposal(
                proposal_id,
                decided_by=request.decided_by,
                decision_reason=request.reason,
            )
            return {"status": "rejected", "proposal": _proposal_to_dict(result.proposal)}
        except MutationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except MutationConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/tools/proposals")
    def list_tool_proposals() -> dict[str, Any]:
        ledger = SQLiteEventLedger(config.events_database_path())
        try:
            executed = {
                event.payload.get("call_digest")
                for event in ledger.iter_events_by_name("tool.execution.completed")
            }
            proposals = []
            for event in ledger.iter_events_by_name("tool.proposal.evaluated"):
                call_digest = event.payload.get("tool_result", {}).get("call_digest")
                if call_digest not in executed:
                    proposals.append({
                        "call_digest": call_digest,
                        "tool_name": event.payload.get("tool_call", {}).get("tool_name"),
                        "payload": event.payload.get("tool_call", {}).get("payload"),
                        "reason": event.payload.get("tool_call", {}).get("reason"),
                        "status": event.payload.get("tool_result", {}).get("status"),
                        "reason_code": event.payload.get("tool_result", {}).get("reason_code"),
                        "event_index": event.index,
                        "created_at": event.created_at,
                        "intent_binding": event.payload.get("intent_binding"),
                    })
            return {"proposals": proposals}
        finally:
            ledger.close()

    @app.post("/tools/proposals/{call_digest}/approve")
    def approve_tool_proposal(call_digest: str, request: ToolApprovalRequest) -> dict[str, Any]:
        coordinator = MutationCoordinator(config, tool_registry=app.state.tool_registry)
        try:
            result = coordinator.approve_tool_proposal(
                call_digest,
                approved_by=request.approved_by,
                approval_basis=request.approval_basis,
            )
            gate_decision = result.gate_decision
            return {
                "status": "executed",
                "call_digest": call_digest,
                "tool_name": result.tool_name,
                "approved_by": result.approved_by,
                "output": result.output,
                "gate_decision": gate_decision.__dict__ if hasattr(gate_decision, "__dict__") else gate_decision,
            }
        except MutationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except MutationUnprocessableError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ToolIntentBindingRequired as exc:
            raise HTTPException(
                status_code=403,
                detail={
                    "reason_code": "tool_intent_unbound",
                    "message": "tool proposal is not bound to interpreted user intent",
                    "intent_binding": exc.binding.to_dict(),
                    "override_basis": "intent_override_accepted",
                },
            ) from exc
        except ToolExecutionRefused as exc:
            result = exc.result
            raise HTTPException(
                status_code=403,
                detail={
                    "reason_code": result.reason_code,
                    "reason": result.reason,
                    "status": result.status.value,
                    "call_digest": call_digest,
                    "message": "governance gate refused execution",
                },
            ) from exc

    @app.get("/maintenance/pulse")
    def maintenance_pulse() -> dict[str, Any]:
        events = _read_events(config)
        queue = SQLiteMemoryProposalQueue(config.proposal_database_path())
        try:
            pending_count = len(queue.pending())
        finally:
            queue.close()
        snapshot = create_pulse_snapshot(
            events=events,
            pending_proposal_count=pending_count,
            last_claim=_last_claim(events),
        )
        return {"pulse": snapshot.to_dict()}

    @app.get("/maintenance/report")
    def maintenance_report() -> dict[str, Any]:
        events = _read_events(config)
        queue = SQLiteMemoryProposalQueue(config.proposal_database_path())
        try:
            pending_count = len(queue.pending())
        finally:
            queue.close()
        findings = find_repairs(events=events, pending_proposal_count=pending_count)
        report = create_maintenance_report(findings)
        return {"report": report.to_dict()}

    return app


def _proposal_to_dict(proposal: MemoryProposal) -> dict[str, Any]:
    return {
        "proposal_id": proposal.proposal_id,
        "content": proposal.content,
        "reason": proposal.reason,
        "tags": list(proposal.tags),
        "source": proposal.source,
        "created_at": proposal.created_at,
        "event_index": proposal.event_index,
        "pinned": proposal.pinned,
        "metadata": proposal.metadata,
        "status": proposal.status,
        "decided_by": proposal.decided_by,
        "decided_at": proposal.decided_at,
        "decision_reason": proposal.decision_reason,
    }


def _read_events(config: RuntimeConfig) -> tuple[Any, ...]:
    ledger = SQLiteEventLedger(config.events_database_path())
    try:
        return tuple(ledger.iter_events())
    finally:
        ledger.close()


def _last_claim(events: tuple[Any, ...]) -> str | None:
    for event in reversed(events):
        if event.name == "runtime.claim.emitted":
            claim = event.payload.get("claim", {})
            return claim.get("classification")
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="halcyon-api", description="Run the local Halcyon API server.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Non-localhost requires --allow-external.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default="halcyon.sqlite", help="Base SQLite path for local runtime state.")
    parser.add_argument("--model-base-url", default="http://localhost:1234/v1")
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--allow-external", action="store_true", help="Allow binding to a non-localhost interface.")
    return parser


def is_local_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def validate_bind(host: str, *, allow_external: bool = False) -> None:
    if not is_local_host(host) and not allow_external:
        raise SystemExit("non-localhost bind requires --allow-external")


def startup_summary(config: RuntimeConfig, *, host: str, port: int, allow_external: bool = False) -> str:
    exposure = "EXTERNAL ENABLED" if not is_local_host(host) else "LOCAL ONLY"
    lines = [
        "Halcyon API running",
        f"Host: {host}",
        f"Port: {port}",
        f"DB: {config.database_path}",
        f"Model: {config.model_base_url} ({config.model_name})",
        f"Mode: {GOVERNANCE_MODE}",
        f"Exposure: {exposure}",
    ]
    if exposure != "LOCAL ONLY" and allow_external:
        lines.append("Warning: external network exposure explicitly enabled")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    validate_bind(args.host, allow_external=args.allow_external)
    config = RuntimeConfig(
        model_base_url=args.model_base_url,
        model_name=args.model,
        database_path=args.db,
    )
    print(startup_summary(config, host=args.host, port=args.port, allow_external=args.allow_external), flush=True)
    import uvicorn

    uvicorn.run(create_app(config), host=args.host, port=args.port)
    return 0


import os as _os

app = create_app(RuntimeConfig(
    database_path=_os.environ.get("HALCYON_DB", "halcyon.sqlite"),
    model_base_url=_os.environ.get("HALCYON_MODEL_URL", "http://localhost:1234/v1"),
    model_name=_os.environ.get("HALCYON_MODEL", "openai/gpt-oss-20b"),
))

if __name__ == "__main__":
    raise SystemExit(main())
