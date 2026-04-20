import pytest

from halcyon_core.governance.mutation_context import MutationContextRequired, mutation_context
from halcyon_core.memory.migrate_legacy import import_legacy_json, legacy_records
from halcyon_core.memory.proposals import MemoryProposalQueue, SQLiteMemoryProposalQueue, propose_memory
from halcyon_core.memory.retrieval import render_context, retrieve_context
from halcyon_core.memory.store import SQLiteMemoryStore, make_memory


def _test_mutation_context():
    return mutation_context(actor="test", operation="memory_store_unit_test")


def test_memory_store_direct_add_requires_mutation_context(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    with pytest.raises(MutationContextRequired):
        store.add(make_memory("direct write"))
    assert store.recent() == []
    store.close()


def test_memory_store_adds_and_retrieves_recent_items(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    with _test_mutation_context():
        first = store.add(make_memory("first", tags=("alpha",), source="test"))
        second = store.add(make_memory("second", tags=("beta",), source="test"))

    recent = store.recent(limit=2)
    assert [item.content for item in recent] == ["second", "first"]
    assert first.memory_id == 1
    assert second.memory_id == 2
    store.close()


def test_memory_store_tag_retrieval_and_pinned_reads(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    with _test_mutation_context():
        store.add(make_memory("ordinary", tags=("work",)))
        pinned = store.add(make_memory("anchor", tags=("identity",), pinned=True))

    assert store.by_tag("identity")[0].content == "anchor"
    assert store.pinned()[0].memory_id == pinned.memory_id
    store.close()


def test_memory_digest_is_stable_across_metadata_key_order():
    left = make_memory("same", metadata={"b": 2, "a": 1})
    right = make_memory("same", metadata={"a": 1, "b": 2})
    assert left.content_digest == right.content_digest


def test_memory_proposal_approve_requires_mutation_context(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    queue = MemoryProposalQueue()
    with _test_mutation_context():
        proposal = queue.submit(propose_memory("guard me", "test"))

    with pytest.raises(MutationContextRequired):
        queue.approve(proposal.proposal_id, store, decided_by="brad")

    assert queue.get(proposal.proposal_id).status == "pending"
    assert store.recent() == []
    store.close()


def test_memory_proposal_does_not_write_until_approved(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    queue = MemoryProposalQueue()
    with _test_mutation_context():
        proposal = queue.submit(
            propose_memory("Brad prefers local-first runtime design.", "user stated preference", tags=("preference",))
        )

    assert store.recent() == []
    assert queue.pending() == [proposal]

    with _test_mutation_context():
        memory = queue.approve(proposal.proposal_id, store, decided_by="brad")
    assert memory.content == proposal.content
    assert memory.metadata["proposal_id"] == proposal.proposal_id
    assert queue.get(proposal.proposal_id).status == "approved"
    assert store.recent()[0].content == proposal.content
    store.close()


def test_memory_proposal_reject_does_not_write(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    queue = MemoryProposalQueue()
    with _test_mutation_context():
        proposal = queue.submit(propose_memory("unverified claim", "model guessed"))

    with _test_mutation_context():
        rejected = queue.reject(proposal.proposal_id, decided_by="brad", decision_reason="not true")
    assert rejected.status == "rejected"
    assert store.recent() == []
    store.close()


def test_memory_proposal_cannot_be_decided_twice(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    queue = MemoryProposalQueue()
    with _test_mutation_context():
        proposal = queue.submit(propose_memory("one shot", "test"))
    with _test_mutation_context():
        queue.reject(proposal.proposal_id, decided_by="brad")

    with pytest.raises(ValueError):
        with _test_mutation_context():
            queue.approve(proposal.proposal_id, store, decided_by="brad")
    store.close()


def test_retrieve_context_combines_pinned_tagged_and_recent_without_duplicates(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    with _test_mutation_context():
        pinned = store.add(make_memory("pinned identity", tags=("identity",), pinned=True))
        tagged = store.add(make_memory("tagged project", tags=("project",)))
        recent = store.add(make_memory("recent note", tags=("note",)))

    items = retrieve_context(store, tags=("project",), recent_limit=3)
    assert [item.memory_id for item in items] == [pinned.memory_id, tagged.memory_id, recent.memory_id]
    rendered = render_context(items)
    assert "source=runtime pinned" in rendered
    assert "tagged project" in rendered
    store.close()


def test_legacy_records_are_labeled_archive_not_truth():
    records = legacy_records(["old braid", {"clarity": True}], source="lore_archive")
    assert [record.source for record in records] == ["lore_archive", "lore_archive"]
    assert all("legacy" in record.tags for record in records)
    assert all("archive" in record.tags for record in records)


def test_import_legacy_json_flattens_to_memory_items(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text('["old braid", {"clarity": true}]', encoding="utf-8")
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")

    with _test_mutation_context():
        imported = import_legacy_json(path, store)
    assert len(imported) == 2
    assert store.search_text("old braid")[0].source == "lore_archive"
    assert store.search_text("clarity")[0].metadata["legacy_type"] == "object"
    store.close()



def test_sqlite_memory_proposal_queue_persists_pending_across_reopen(tmp_path):
    path = tmp_path / "proposals.sqlite"
    queue = SQLiteMemoryProposalQueue(path)
    with _test_mutation_context():
        proposal = queue.submit(propose_memory("persistent pending", "test", tags=("persist",)))
    queue.close()

    reopened = SQLiteMemoryProposalQueue(path)
    pending = reopened.pending()
    assert len(pending) == 1
    assert pending[0].proposal_id == proposal.proposal_id
    assert pending[0].tags == ("persist",)
    reopened.close()


def test_sqlite_memory_proposal_queue_persists_approval_and_writes_memory(tmp_path):
    proposal_path = tmp_path / "proposals.sqlite"
    memory_path = tmp_path / "memory.sqlite"
    queue = SQLiteMemoryProposalQueue(proposal_path)
    store = SQLiteMemoryStore(memory_path)
    with _test_mutation_context():
        proposal = queue.submit(propose_memory("approve me", "test", pinned=True))

    with _test_mutation_context():
        memory = queue.approve(proposal.proposal_id, store, decided_by="brad")
    queue.close()
    store.close()

    reopened_queue = SQLiteMemoryProposalQueue(proposal_path)
    reopened_store = SQLiteMemoryStore(memory_path)
    decided = reopened_queue.get(proposal.proposal_id)
    assert decided.status == "approved"
    assert decided.decided_by == "brad"
    assert reopened_store.get(memory.memory_id).content == "approve me"
    assert reopened_store.get(memory.memory_id).pinned is True
    reopened_queue.close()
    reopened_store.close()


def test_sqlite_memory_proposal_queue_persists_rejection(tmp_path):
    path = tmp_path / "proposals.sqlite"
    queue = SQLiteMemoryProposalQueue(path)
    with _test_mutation_context():
        proposal = queue.submit(propose_memory("reject me", "test"))
    with _test_mutation_context():
        queue.reject(proposal.proposal_id, decided_by="brad", decision_reason="nope")
    queue.close()

    reopened = SQLiteMemoryProposalQueue(path)
    decided = reopened.get(proposal.proposal_id)
    assert decided.status == "rejected"
    assert decided.decision_reason == "nope"
    assert reopened.pending() == []
    reopened.close()


def test_memory_proposal_in_memory_reject_requires_mutation_context():
    queue = MemoryProposalQueue()
    with _test_mutation_context():
        proposal = queue.submit(propose_memory("guard me", "test"))

    with pytest.raises(MutationContextRequired):
        queue.reject(proposal.proposal_id, decided_by="brad")

    assert queue.get(proposal.proposal_id).status == "pending"


def test_sqlite_memory_proposal_queue_reject_requires_mutation_context(tmp_path):
    path = tmp_path / "proposals.sqlite"
    queue = SQLiteMemoryProposalQueue(path)
    with _test_mutation_context():
        proposal = queue.submit(propose_memory("guard me", "test"))

    with pytest.raises(MutationContextRequired):
        queue.reject(proposal.proposal_id, decided_by="brad")

    assert queue.get(proposal.proposal_id).status == "pending"
    queue.close()


def test_memory_proposal_in_memory_submit_requires_mutation_context():
    queue = MemoryProposalQueue()
    with pytest.raises(MutationContextRequired):
        queue.submit(propose_memory("should not land", "test"))
    assert queue.pending() == []


def test_sqlite_memory_proposal_queue_submit_requires_mutation_context(tmp_path):
    path = tmp_path / "proposals.sqlite"
    queue = SQLiteMemoryProposalQueue(path)
    with pytest.raises(MutationContextRequired):
        queue.submit(propose_memory("should not land", "test"))
    assert queue.pending() == []
    queue.close()


def test_memory_proposal_submit_stale_context_is_denied():
    queue = MemoryProposalQueue()
    with _test_mutation_context():
        pass  # context exits here
    with pytest.raises(MutationContextRequired):
        queue.submit(propose_memory("stale submit", "test"))
    assert queue.pending() == []


def test_memory_proposal_submit_with_active_context_succeeds():
    queue = MemoryProposalQueue()
    with _test_mutation_context():
        proposal = queue.submit(propose_memory("guarded submit", "test"))
    assert queue.get(proposal.proposal_id).status == "pending"


def test_sqlite_memory_proposal_queue_approve_requires_mutation_context(tmp_path):
    proposal_path = tmp_path / "proposals.sqlite"
    memory_path = tmp_path / "memory.sqlite"
    queue = SQLiteMemoryProposalQueue(proposal_path)
    store = SQLiteMemoryStore(memory_path)
    with _test_mutation_context():
        proposal = queue.submit(propose_memory("guard me", "test"))

    with pytest.raises(MutationContextRequired):
        queue.approve(proposal.proposal_id, store, decided_by="brad")

    assert queue.get(proposal.proposal_id).status == "pending"
    queue.close()
    store.close()
