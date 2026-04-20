"""Command-line smoke and review harness for the Halcyon MVL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from halcyon_core.config import RuntimeConfig
from halcyon_core.governance.mutation_coordinator import MutationCoordinator
from halcyon_core.kernel import RuntimeKernel, RuntimeTurn
from halcyon_core.memory.proposals import MemoryProposal, SQLiteMemoryProposalQueue
from halcyon_core.model.client import LocalModelClient


def fake_model_client(reply: str) -> LocalModelClient:
    def transport(url: str, payload: dict) -> dict:
        return {"choices": [{"message": {"content": reply}}]}

    return LocalModelClient("http://fake.local/v1", "fake-model", transport=transport)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="halcyon-mvl",
        description="Run one Halcyon minimal viable loop turn.",
    )
    add_common_args(parser)
    parser.add_argument("message", nargs="*", help="User message for the MVL turn.")
    parser.add_argument("--model-base-url", default="http://localhost:1234/v1")
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--memory-tag", action="append", default=[], help="Memory tag to retrieve into context. Repeatable.")
    parser.add_argument("--fake-reply", help="Use a fake model response instead of calling a live endpoint.")
    parser.set_defaults(command="turn")
    return parser


def build_proposals_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="halcyon-mvl proposals",
        description="Review persistent Halcyon memory proposals.",
    )
    add_common_args(parser)
    parser.set_defaults(command="proposals")
    proposal_subcommands = parser.add_subparsers(dest="proposal_command", required=True)

    proposal_list = proposal_subcommands.add_parser("list", help="List pending memory proposals.")
    proposal_list.add_argument("--all", action="store_true", help="Include decided proposals.")
    proposal_list.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    proposal_approve = proposal_subcommands.add_parser("approve", help="Approve a memory proposal.")
    proposal_approve.add_argument("proposal_id")
    proposal_approve.add_argument("--by", required=True, help="Reviewer identity.")
    proposal_approve.add_argument("--reason", default="approved", help="Decision reason.")
    proposal_approve.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    proposal_reject = proposal_subcommands.add_parser("reject", help="Reject a memory proposal.")
    proposal_reject.add_argument("proposal_id")
    proposal_reject.add_argument("--by", required=True, help="Reviewer identity.")
    proposal_reject.add_argument("--reason", default="rejected", help="Decision reason.")
    proposal_reject.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser

def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default="halcyon.sqlite", help="Base SQLite path for local runtime state.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")


def config_from_args(args: argparse.Namespace) -> RuntimeConfig:
    return RuntimeConfig(
        model_base_url=getattr(args, "model_base_url", "http://localhost:1234/v1"),
        model_name=getattr(args, "model", "openai/gpt-oss-20b"),
        database_path=str(Path(args.db)),
    )


def run_turn(args: argparse.Namespace) -> RuntimeTurn:
    message = " ".join(args.message).strip()
    if not message:
        raise SystemExit("message is required")

    config = config_from_args(args)
    model_client = fake_model_client(args.fake_reply) if args.fake_reply is not None else None
    kernel = RuntimeKernel(config=config, model_client=model_client)
    try:
        return kernel.handle_message(message, memory_tags=tuple(args.memory_tag))
    finally:
        kernel.close()


def run_proposal_command(args: argparse.Namespace) -> object:
    config = config_from_args(args)
    if args.proposal_command == "list":
        queue = SQLiteMemoryProposalQueue(config.proposal_database_path())
        try:
            if args.all:
                return queue.all()
            return queue.pending()
        finally:
            queue.close()

    coordinator = MutationCoordinator(config)
    if args.proposal_command == "approve":
        result = coordinator.approve_memory_proposal(
            args.proposal_id,
            decided_by=args.by,
            decision_reason=args.reason,
        )
        return {"status": "approved", "proposal_id": args.proposal_id, "memory_id": result.memory_id}
    if args.proposal_command == "reject":
        result = coordinator.reject_memory_proposal(
            args.proposal_id,
            decided_by=args.by,
            decision_reason=args.reason,
        )
        return {
            "status": "rejected",
            "proposal_id": result.proposal.proposal_id,
            "reason": result.proposal.decision_reason,
        }
    raise SystemExit(f"unknown proposal command: {args.proposal_command}")


def proposal_to_dict(proposal: MemoryProposal) -> dict:
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


def format_text(turn: RuntimeTurn) -> str:
    lines = [
        turn.reply,
        "",
        f"claim: {turn.final_claim.classification}",
        f"events: user={turn.user_event.index} model={turn.model_event.index if turn.model_event else 'none'} claim={turn.claim_event.index}",
    ]
    if turn.memory_proposals:
        lines.append(f"memory proposals: {len(turn.memory_proposals)} pending")
    if turn.tool_decisions:
        refused = sum(1 for decision in turn.tool_decisions if decision.status == "refuse")
        lines.append(f"tool proposals: {len(turn.tool_decisions)} evaluated, {refused} refused")
    if turn.interpretation and turn.interpretation.status.value != "stable":
        lines.append(f"interpretation: {turn.interpretation.status.value}")
    if turn.model_warnings:
        lines.append("warnings: " + ", ".join(turn.model_warnings))
    return "\n".join(lines)


def format_proposals_text(result: object) -> str:
    if isinstance(result, list):
        if not result:
            return "no proposals"
        lines = []
        for proposal in result:
            tags = ",".join(proposal.tags) if proposal.tags else "untagged"
            lines.append(f"{proposal.proposal_id} [{proposal.status}; {tags}] {proposal.content}")
            lines.append(f"  reason: {proposal.reason}")
        return "\n".join(lines)
    if isinstance(result, dict):
        return "\n".join(f"{key}: {value}" for key, value in result.items())
    return str(result)


def json_ready(value: object) -> object:
    if isinstance(value, RuntimeTurn):
        return value.to_dict()
    if isinstance(value, MemoryProposal):
        return proposal_to_dict(value)
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "proposals":
        return build_proposals_parser().parse_args(argv[1:])
    return build_parser().parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "proposals":
        result = run_proposal_command(args)
        if args.json:
            print(json.dumps(json_ready(result), indent=2, sort_keys=True))
        else:
            print(format_proposals_text(result))
        return 0

    turn = run_turn(args)
    if args.json:
        print(json.dumps(turn.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_text(turn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
