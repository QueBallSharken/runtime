import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import coordinator
from coordinator import Proposal, coordinate_mutation
from policy_store import PolicyStore


def make_policy_store(policies: dict) -> PolicyStore:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(policies, f)
        name = f.name
    return PolicyStore(path=name)


POLICIES = {
    "default": {"max_spend_usd": 50},
    "agent_finance_01": {"max_spend_usd": 500},
}


def test_over_limit_rejects_before_execute():
    proposal = Proposal(proposal_id=123, subject_id="default", amount=500)
    policy = make_policy_store(POLICIES)

    with patch.object(coordinator, "execute") as mock_execute:
        result = coordinate_mutation(proposal, policy)

        assert result["status"] == "rejected"
        assert result["subject_id"] == "default"
        assert result["proposal_id"] == 123
        assert result["reason"] == "LIMIT_EXCEEDED"
        assert result["requested"] == 500
        assert result["limit"] == 50
        assert result["side_effect"] is False
        assert result["next_actions"] == ["request_approval", "reduce_to_limit", "cancel"]
        mock_execute.assert_not_called()  # the money assertion


def test_under_limit_executes():
    proposal = Proposal(proposal_id=456, subject_id="default", amount=30)
    policy = make_policy_store(POLICIES)

    result = coordinate_mutation(proposal, policy)

    assert result["status"] == "executed"
    assert result["side_effect"] is True


def test_subject_specific_limit():
    proposal = Proposal(proposal_id=789, subject_id="agent_finance_01", amount=400)
    policy = make_policy_store(POLICIES)

    result = coordinate_mutation(proposal, policy)

    assert result["status"] == "executed"


def test_subject_specific_limit_still_enforced():
    proposal = Proposal(proposal_id=999, subject_id="agent_finance_01", amount=600)
    policy = make_policy_store(POLICIES)

    with patch.object(coordinator, "execute") as mock_execute:
        result = coordinate_mutation(proposal, policy)

        assert result["status"] == "rejected"
        assert result["limit"] == 500
        mock_execute.assert_not_called()
