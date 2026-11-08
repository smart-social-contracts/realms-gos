"""db_convert_indexes: the index lists an earlier release (ic-python-db before 0.13) saved as one array each are
converted a batch at a time, with their ids and their order, and the realm remembers when it is done."""

import json

import world
from api.setup import INDEX_CONVERSION
from ic_python_db import Database
from kit import call, denied, ok

from ggg import Member, Proposal, Realm, Vote


def storage():
    return Database.get_instance()._db_storage


def forget_the_conversion():
    """The realm as an earlier release left it: it knew nothing of a conversion."""
    realm = Realm["1"]
    realm.manifest_data = {k: v for k, v in realm.manifest_data.items() if k != INDEX_CONVERSION}


def legacy(base, ids):
    """Save ``base`` as ic-python-db before 0.13 did: one JSON array holding the ids."""
    storage().insert(base, json.dumps([str(i) for i in ids]))


def make_legacy(base):
    """Turn the list ``base``, saved one entry per id, back into the array an earlier release saved."""
    db = Database.get_instance()
    ids = db._ix_all(base)
    db._ix_drain(base, len(ids))
    storage().remove(f"{base}|c")
    storage().insert(base, json.dumps(ids))


def is_converted(base, count):
    return storage().get(base) is None and storage().get(f"{base}|c") == str(count)


def convert_all(**args):
    """db_convert_indexes until it is done; returns the replies."""
    replies = []
    while not replies or not replies[-1]["done"]:
        replies.append(ok("db_convert_indexes", "ctl", **args))
        assert len(replies) < 50, replies
    return replies


def refuse_scans(monkeypatch):
    """From here on, reading the index entries by range fails the test."""
    def scanned(*args):
        raise AssertionError("the index entries were scanned")

    monkeypatch.setattr(Database.get_instance(), "_scan", scanned)


def test_members_votes_and_proposals_keep_their_lists_through_the_conversion():
    world.build()
    p = ok("proposal_create", "alice", title="Bike lane")["proposal"]
    for voter in ("alice", "bob", "dana"):
        ok("proposal_vote", voter, proposal=p, choice="yes")
    members, votes = len(Member.load_some(1, 100)), len(Vote.load_some(1, 100))
    bases = ["_fi:Member:status:active", f"_fi:Vote:proposal_id:{p}", f"_ri:Proposal:{Proposal[p]._id}:votes"]
    ids = {base: Database.get_instance()._ix_all(base) for base in bases}
    assert [len(v) for v in ids.values()] == [members, votes, votes]
    for base in bases:
        make_legacy(base)
    forget_the_conversion()

    out = ok("db_convert_indexes", "ctl")

    assert out == {"ok": True, "lists": 3, "ids": members + 2 * votes, "done": True}
    for base in bases:
        assert is_converted(base, len(ids[base]))
        assert Database.get_instance()._ix_all(base) == ids[base]
    assert Vote.count_by("proposal_id", p) == votes and Member.count_by("status", "active") == members
    assert [v.voter for v in Vote.find_by("proposal_id", p)[0]] == ["alice", "bob", "dana"]
    assert len(Proposal[p].votes) == votes
    ok("proposal_vote", "elena", proposal=p, choice="no")
    assert Vote.count_by("proposal_id", p) == votes + 1 and len(Proposal[p].votes) == votes + 1


def test_a_call_converts_about_ids_ids_and_the_next_call_continues_it():
    world.build()
    sizes = {"_fi:Vote:proposal_id:a": 300, "_ri:Proposal:bb:votes": 150, "_fi:Vote:voter:ccc": 50,
             "_fi:Vote:voter:dddd": 40}
    for base, size in sizes.items():
        legacy(base, range(1, size + 1))
    forget_the_conversion()

    replies = convert_all(ids=100)

    # Shorter keys come first. A list is converted whole: the two small ones share a call, each longer than ``ids``
    # goes alone
    assert [r["ids"] for r in replies if r["ids"]] == [90, 300, 150], replies
    assert sum(r["lists"] for r in replies) == 4 and sum(r["ids"] for r in replies) == 540
    assert all(is_converted(base, size) for base, size in sizes.items())


def test_a_new_realm_is_done_and_scans_nothing(monkeypatch):
    world.build()
    refuse_scans(monkeypatch)
    assert Realm["1"].manifest_data[INDEX_CONVERSION] == {"done": True}
    assert ok("db_convert_indexes", "ctl") == {"ok": True, "lists": 0, "ids": 0, "done": True}


def test_a_converted_realm_is_done_and_scans_nothing(monkeypatch):
    world.build()
    legacy("_fi:Vote:proposal_id:P-9", range(1, 21))
    forget_the_conversion()
    assert sum(r["lists"] for r in convert_all()) == 1
    assert Realm["1"].manifest_data[INDEX_CONVERSION] == {"done": True}
    assert Database.get_instance()._ix_all("_fi:Vote:proposal_id:P-9") == [str(i) for i in range(1, 21)]
    refuse_scans(monkeypatch)
    assert ok("db_convert_indexes", "ctl") == {"ok": True, "lists": 0, "ids": 0, "done": True}


def test_the_realm_remembers_how_far_it_got():
    world.build()
    legacy("_fi:Vote:proposal_id:a", range(1, 301))
    legacy("_fi:Vote:voter:bb", range(1, 201))
    forget_the_conversion()

    first = ok("db_convert_indexes", "ctl", ids=100)

    # A list longer than ``ids`` uses its call up: the shorter one, then the longer, then the end
    assert first["ids"] == 200 and not first["done"] and Realm["1"].manifest_data[INDEX_CONVERSION]["after"]
    second = ok("db_convert_indexes", "ctl", ids=100)
    assert second["ids"] == 300 and not second["done"]
    assert ok("db_convert_indexes", "ctl", ids=100) == {"ok": True, "lists": 0, "ids": 0, "done": True}
    assert Realm["1"].manifest_data[INDEX_CONVERSION] == {"done": True}
    assert is_converted("_fi:Vote:voter:bb", 200) and is_converted("_fi:Vote:proposal_id:a", 300)


def test_a_list_too_long_for_one_call_is_refused_by_name_and_the_rest_waits():
    """Converting it would take more instructions than an update has: say which list, and convert nothing else."""
    world.build()
    legacy("_fi:Vote:voter:small", range(1, 21))
    legacy("_ri:Proposal:big:votes", range(1, 150_001))
    forget_the_conversion()

    assert ok("db_convert_indexes", "ctl")["lists"] == 1  # the short list goes first, in its own call
    out = call("db_convert_indexes", "ctl")

    assert not out["ok"] and out["kind"] == "unavailable" and "_ri:Proposal:big:votes" in out["error"]
    assert "150000 ids" in out["error"] and storage().get("_ri:Proposal:big:votes")[:1] == "["
    assert "after" in Realm["1"].manifest_data[INDEX_CONVERSION]  # not done


def test_an_id_that_looks_like_an_array_is_not_a_list():
    """A custom id such as "[1]" is stored as an entry of a converted list, whose value is the id itself."""
    world.build()
    db = Database.get_instance()
    db.field_index_add("Vote", "proposal_id", "P-7", "[1]")
    db.field_index_add("Vote", "proposal_id", "P-7", "[2]")
    forget_the_conversion()

    assert sum(r["lists"] for r in convert_all()) == 0
    assert sorted(db.field_index_get("Vote", "proposal_id", "P-7")) == ["[1]", "[2]"]


def test_only_the_host_converts():
    world.build()
    denied("db_convert_indexes", "alice")
    denied("db_convert_indexes", "mallory")
