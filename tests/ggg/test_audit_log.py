"""The audit log keeps its last EVENTS_KEPT events, and dropping the oldest one costs an update next to nothing
(realms-gos#672 finding F2). The cost is measured on the real wasm in tests/deployed/test_audit_log.py."""
import pytest
from ic_python_db import Database
from ic_python_db.properties import Relation

import world
from core import op
from ggg import Event
from kit import ok


def audited(count, grant="", start=0):
    for i in range(start, start + count):
        op.audit("profile_update", "alice", "alice", "user", {"nickname": f"Ali{i}"}, {}, grant=grant)


def ids(**args):
    return [e["id"] for e in ok("events", "ctl", **args)["events"]]


@pytest.fixture
def kept_five(monkeypatch):
    world.build()
    monkeypatch.setattr(op, "EVENTS_KEPT", 5)


def test_a_log_that_is_not_full_keeps_everything(kept_five):
    audited(4)
    assert ids() == ["4", "3", "2", "1"] and Event.count() == 4
    assert op.oldest_event().id == "1"


def test_a_full_log_drops_the_oldest_event_of_each_new_one(kept_five):
    audited(5)
    assert ids() == ["5", "4", "3", "2", "1"]
    audited(1, start=5)
    assert ids() == ["6", "5", "4", "3", "2"] and Event.count() == 5
    audited(6, start=6)
    assert ids() == ["12", "11", "10", "9", "8"] and Event.count() == 5
    assert op.oldest_event().id == "8" and Event["7"] is None and Event["8"].args == {"nickname": "Ali7"}


def test_a_reader_pages_through_the_log_by_id_as_before(kept_five):
    audited(12)
    page = ok("events", "ctl", count=2)
    assert [e["id"] for e in page["events"]] == ["12", "11"] and page["next"] == 10
    pages = [ids(count=2, **{"from": start}) for start in (10, 8, 6)]
    assert pages == [["10", "9"], ["8"], []]  # ids below the oldest kept are gone, as they always were


def test_the_grant_index_follows_the_events_the_log_drops(kept_five):
    audited(2, grant="D-1")
    audited(3, start=2)
    assert Event.count_by("grant", "D-1") == 2
    audited(1, start=5)  # drops event 1, the first of the grant's
    assert Event.count_by("grant", "D-1") == 1
    audited(1, start=6)
    assert Event.count_by("grant", "D-1") == 0 and Event.count() == 5


def snapshot():
    return dict(Database.get_instance()._db_storage.items())


def restore(rows):
    """The storage as ``rows`` hold it, and the identity map emptied, as a new message starts."""
    db = Database.get_instance()
    for key in list(db._db_storage.keys()):
        db._db_storage.remove(key)
    for key, value in rows.items():
        db._db_storage.insert(key, value)
    db.clear_registry()


def test_forgetting_an_event_leaves_storage_as_deleting_it_does():
    audited(2, grant="D-1")
    audited(2, grant="D-2", start=2)
    audited(2, start=4)
    before = snapshot()
    for event_id in ("1", "3", "5"):  # one of each kind: grant already there with others, the last of its grant, none
        restore(before)
        Event[event_id].delete()
        deleted = snapshot()
        restore(before)
        Event.forget(event_id)
        assert snapshot() == deleted, event_id


def test_forgetting_an_event_the_log_does_not_hold_changes_nothing():
    audited(2)
    before = snapshot()
    Event.forget("3")
    Event.forget("0")
    Event.forget("-1")
    assert snapshot() == before


def relations(entity):
    """(class, attribute, relation) of each relation ``entity`` declares, its bases' included."""
    return [(klass.__name__, name, value) for klass in entity.__mro__ for name, value in vars(klass).items()
            if isinstance(value, Relation)]


def test_event_stays_a_row_that_forget_can_delete():
    """Event.forget does what Entity.delete does for a row with no relation, no sorted index and no hook, and that no other
    entity has a relation to. An event that gains one of them needs its cleanup in forget, or to be deleted by
    Entity.delete again (about 20 M instructions)."""
    entities = {id(entity): entity for entity in Database.get_instance()._entity_types.values() if isinstance(entity, type)}
    pointing_at_event = [(klass, name) for entity in entities.values() for klass, name, relation in relations(entity)
                         if "Event" in relation._get_allowed_types()]
    assert relations(Event) == [] and pointing_at_event == []
    assert Event._sorted_indexes() == {} and not hasattr(Event, "on_event")
