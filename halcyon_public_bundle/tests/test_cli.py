import json
import subprocess
import sys

from halcyon_core.api.cli import build_parser, format_text, run_turn


def test_cli_run_turn_with_fake_reply(tmp_path):
    parser = build_parser()
    args = parser.parse_args([
        "hello",
        "there",
        "--db",
        str(tmp_path / "halcyon.sqlite"),
        "--fake-reply",
        '{"reply":"hi back","memory_proposals":[],"tool_proposals":[]}',
    ])

    turn = run_turn(args)

    assert turn.reply == "hi back"
    assert turn.user_event.index == 1
    assert turn.final_claim.classification == "strong_segment_governance"
    assert turn.final_claim.condition_summary["C5"] == "partial"


def test_cli_text_format_includes_claim_events_and_warnings(tmp_path):
    parser = build_parser()
    args = parser.parse_args([
        "hello",
        "--db",
        str(tmp_path / "halcyon.sqlite"),
        "--fake-reply",
        "plain hi",
    ])
    turn = run_turn(args)

    text = format_text(turn)

    assert "plain hi" in text
    assert "claim: strong_segment_governance" in text
    assert "events: user=1 model=4 claim=5" in text
    assert "warnings: model output was plain text" in text


def test_cli_module_prints_json_runtime_turn(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "halcyon_core.api.cli",
            "hello",
            "--db",
            str(tmp_path / "halcyon.sqlite"),
            "--fake-reply",
            '{"reply":"json hi","memory_proposals":[],"tool_proposals":[]}',
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    data = json.loads(result.stdout)
    assert data["reply"] == "json hi"
    assert data["final_claim"]["classification"] == "strong_segment_governance"
    assert data["final_claim"]["condition_summary"]["C5"] == "partial"
    assert data["user_event"]["name"] == "user.message.received"


def test_cli_requires_message(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "halcyon_core.api.cli",
            "--db",
            str(tmp_path / "halcyon.sqlite"),
            "--fake-reply",
            "hi",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "message is required" in result.stderr



def test_cli_proposals_list_approve_and_reject(tmp_path):
    db = tmp_path / "halcyon.sqlite"
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "halcyon_core.api.cli",
            "what should be proposed?",
            "--db",
            str(db),
            "--fake-reply",
            '{"reply":"ok","memory_proposals":[{"content":"cli proposal","reason":"test","tags":["cli"]}],"tool_proposals":[]}',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "memory proposals: 1 pending" in create.stdout

    listed = subprocess.run(
        [sys.executable, "-m", "halcyon_core.api.cli", "proposals", "--db", str(db), "list", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    proposals = json.loads(listed.stdout)
    assert len(proposals) == 1
    proposal_id = proposals[0]["proposal_id"]
    assert proposals[0]["content"] == "cli proposal"

    approved = subprocess.run(
        [
            sys.executable,
            "-m",
            "halcyon_core.api.cli",
            "proposals",
            "--db",
            str(db),
            "approve",
            proposal_id,
            "--by",
            "brad",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    approval = json.loads(approved.stdout)
    assert approval["status"] == "approved"
    assert approval["memory_id"] == 1

    pending = subprocess.run(
        [sys.executable, "-m", "halcyon_core.api.cli", "proposals", "--db", str(db), "list"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert pending.stdout.strip() == "no proposals"

    all_items = subprocess.run(
        [sys.executable, "-m", "halcyon_core.api.cli", "proposals", "--db", str(db), "list", "--all", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(all_items.stdout)[0]["status"] == "approved"


def test_cli_proposals_reject_command(tmp_path):
    db = tmp_path / "halcyon.sqlite"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "halcyon_core.api.cli",
            "what should be proposed?",
            "--db",
            str(db),
            "--fake-reply",
            '{"reply":"ok","memory_proposals":[{"content":"reject via cli","reason":"test"}],"tool_proposals":[]}',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    listed = subprocess.run(
        [sys.executable, "-m", "halcyon_core.api.cli", "proposals", "--db", str(db), "list", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    proposal_id = json.loads(listed.stdout)[0]["proposal_id"]

    rejected = subprocess.run(
        [
            sys.executable,
            "-m",
            "halcyon_core.api.cli",
            "proposals",
            "--db",
            str(db),
            "reject",
            proposal_id,
            "--by",
            "brad",
            "--reason",
            "not durable",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(rejected.stdout)
    assert result == {"proposal_id": proposal_id, "reason": "not durable", "status": "rejected"}
