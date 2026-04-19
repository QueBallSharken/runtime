from dataclasses import dataclass
from audit_log import emit
from next_actions import for_reason
from policy_store import PolicyStore


@dataclass
class Proposal:
    proposal_id: int
    subject_id: str
    amount: float


@dataclass
class Constraints:
    max_spend: float


def _policy_provenance(policy: PolicyStore) -> dict:
    return {"policy_version": policy.version, "policy_hash": policy.hash}


def reject(*, proposal: "Proposal", reason: str, limit: float, policy: PolicyStore) -> dict:
    return {
        "status": "rejected",
        **_policy_provenance(policy),
        "subject_id": proposal.subject_id,
        "proposal_id": proposal.proposal_id,
        "reason": reason,
        "requested": proposal.amount,
        "limit": limit,
        "side_effect": False,
        "next_actions": for_reason(reason),
    }


def execute(proposal: Proposal, policy: PolicyStore) -> dict:
    # stub — real side effects go here
    return {
        "status": "executed",
        **_policy_provenance(policy),
        "subject_id": proposal.subject_id,
        "proposal_id": proposal.proposal_id,
        "side_effect": True,
    }


def coordinate_mutation(proposal: Proposal, policy: PolicyStore) -> dict:
    max_spend = policy.get(proposal.subject_id, "max_spend_usd")
    constraints = Constraints(max_spend=max_spend)

    if proposal.amount > constraints.max_spend:
        result = reject(
            proposal=proposal,
            reason="LIMIT_EXCEEDED",
            limit=constraints.max_spend,
            policy=policy,
        )
        emit(result)
        return result

    result = execute(proposal, policy)
    emit(result)
    return result
