import json
from coordinator import Proposal, coordinate_mutation
from policy_store import PolicyStore

policy = PolicyStore()

print("=" * 55)
print("RUNTIME DEMO — governed decision ledger")
print("=" * 55)

proposal = Proposal(proposal_id=123, subject_id="default", amount=500)
print(f"\nProposal:  ${proposal.amount} from {proposal.subject_id}")
print(f"Policy:    max ${policy.get('default', 'max_spend_usd')}")
print("\nDecision:")

result = coordinate_mutation(proposal, policy)
print(json.dumps(result, indent=2))

print("\nUser selects:", result["next_actions"][0])
print("=> escalation path opened. execute() never ran.")
