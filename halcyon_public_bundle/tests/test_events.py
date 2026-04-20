from halcyon_core.events import EventBus, SQLiteEventLedger, digest_payload


def test_event_bus_assigns_monotonic_indices():
    bus = EventBus()
    first = bus.append("one")
    second = bus.append("two")
    assert first.index == 1
    assert second.index == 2


def test_payload_digest_is_canonical():
    assert digest_payload({"a": 1, "b": 2}) == digest_payload({"b": 2, "a": 1})


def test_subscribers_receive_named_and_wildcard_events():
    bus = EventBus()
    seen = []
    bus.subscribe("alpha", lambda event: seen.append(("named", event.name)))
    bus.subscribe("*", lambda event: seen.append(("wild", event.name)))
    bus.append("alpha", value=1)
    assert seen == [("named", "alpha"), ("wild", "alpha")]


def test_sqlite_ledger_round_trip(tmp_path):
    ledger = SQLiteEventLedger(tmp_path / "events.sqlite")
    bus = EventBus(ledger=ledger)
    event = bus.append(
        "boundary.evaluated",
        boundary_id="abac_01",
        action={"kind": "write", "path": "x"},
    )
    loaded = ledger.get(event.index)
    assert loaded == event
    assert ledger.latest_index() == 1
    ledger.close()


def test_event_bus_resumes_from_existing_ledger(tmp_path):
    path = tmp_path / "events.sqlite"
    ledger = SQLiteEventLedger(path)
    EventBus(ledger=ledger).append("first")
    ledger.close()

    reopened = SQLiteEventLedger(path)
    event = EventBus(ledger=reopened).append("second")
    assert event.index == 2
    reopened.close()
