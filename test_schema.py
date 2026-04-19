import pytest
from schema import validate, REQUIRED_FIELDS, REJECTION_FIELDS


def valid_rejection():
    return {
        "timestamp": "2026-04-19T00:00:00+00:00",
        "status": "rejected",
        "subject_id": "agent_finance_01",
        "proposal_id": 123,
        "reason": "LIMIT_EXCEEDED",
        "requested": 700,
        "limit": 500,
        "policy_version": "2026.04.19.1",
        "policy_hash": "sha256:abc123",
        "side_effect": False,
    }


def valid_execution():
    return {
        "timestamp": "2026-04-19T00:00:00+00:00",
        "status": "executed",
        "subject_id": "agent_ops_01",
        "proposal_id": 456,
        "policy_version": "2026.04.19.1",
        "policy_hash": "sha256:abc123",
        "side_effect": True,
    }


def test_valid_rejection_passes():
    validate(valid_rejection())


def test_valid_execution_passes():
    validate(valid_execution())


@pytest.mark.parametrize("field", sorted(REQUIRED_FIELDS))
def test_missing_required_field_raises(field):
    event = valid_rejection()
    del event[field]
    with pytest.raises(ValueError, match=field):
        validate(event)


@pytest.mark.parametrize("field", sorted(REJECTION_FIELDS))
def test_missing_rejection_field_raises(field):
    event = valid_rejection()
    del event[field]
    with pytest.raises(ValueError, match=field):
        validate(event)


def test_invalid_status_raises():
    event = valid_rejection()
    event["status"] = "pending"
    with pytest.raises(ValueError, match="Invalid status"):
        validate(event)
