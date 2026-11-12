"""proposal_list by tag reads the lists of the tags (Proposal.with_tags), not the proposals: its pages are full, in the
order of an unfiltered list, and cost what the page costs, whatever the realm holds. A realm upgraded from a release
that kept no such lists answers by reading the proposals until ``backfill_tags`` has listed them all."""

import random

import kit
import world
from api import proposals, setup
from ggg import Proposal, Realm
from ic_python_db import Database
from kit import call, fire_timers, ok

STATUSES = ("voting", "executed", "rejected")


def file(title, **extra):
    return ok("proposal_create", "alice", title=title, **extra)["proposal"]


def number(proposal):
    return int(proposal.split("-")[1])


def fill(count, tagged, every):
    """``count`` proposals; every ``every``-th carries ``tagged``. Returns their ids."""
    return [file(f"p{i}", tags=[tagged] if i % every == 0 else []) for i in range(1, count + 1)]


def pages(**args):
    """Follow ``next`` from ``from`` (default 1): the ids of each page, and the ``next`` that ended it."""
    out, start = [], args.pop("from", 1)
    while start is not None:
        reply = ok("proposal_list", "bob", **args, **{"from": start})
        out.append(([p["id"] for p in reply["proposals"]], reply["next"] and int(reply["next"])))
        start = reply["next"] and int(reply["next"])
    return out


def found(**args):
    return [i for ids, _ in pages(**args) for i in ids]


def loaded_proposals(monkeypatch):
    """The ids of the Proposal rows read from now on, by the operation alone: the mirror reads the ones it converts."""
    monkeypatch.setattr(kit, "MIRROR", None)
    read, load = [], Proposal.load
    monkeypatch.setattr(Proposal, "load", classmethod(lambda cls, key, *rest: read.append(key) or load(key, *rest)))
    return read


def forget_the_lists():
    """The realm as the release before this one left it: no list under any tag, and no note that there is one."""
    storage = Database.get_instance()._db_storage
    for key in [k for k in storage.keys() if k.startswith("_fi:Proposal:tags:")]:
        storage.remove(key)
    realm = Realm["1"]
    realm.manifest_data = {k: v for k, v in realm.manifest_data.items() if k != proposals.TAG_INDEX}


def test_a_filtered_page_is_full_and_ends_at_the_next_proposal_that_matches():
    world.build()
    ids = fill(40, "rare", 7)  # P-7, P-14, P-21, P-28, P-35
    assert pages(tags=["rare"], count=2) == [
        (["P-7", "P-14"], 21), (["P-21", "P-28"], 35), (["P-35"], None)]
    assert pages(tags=["rare"], count=5) == [(["P-7", "P-14", "P-21", "P-28", "P-35"], None)]
    assert pages(tags=["rare"], count=4) == [(["P-7", "P-14", "P-21", "P-28"], 35), (["P-35"], None)]
    assert found(tags=["rare"]) == [ids[i - 1] for i in (7, 14, 21, 28, 35)]


def test_a_cursor_from_the_old_scan_continues_where_it_names():
    """The scan's ``next`` was the id after the last proposal it read, whether or not that one carried the tag."""
    world.build()
    fill(40, "rare", 7)
    scanned = ok("proposal_list", "bob", count=10)  # unfiltered: P-1 to P-10, next 11
    assert int(scanned["next"]) == 11
    assert found(tags=["rare"], **{"from": 11}) == ["P-14", "P-21", "P-28", "P-35"]
    assert found(tags=["rare"], **{"from": 14}) == ["P-14", "P-21", "P-28", "P-35"]  # from is inclusive
    assert found(tags=["rare"], **{"from": 36}) == []


def test_a_list_reads_only_the_proposals_of_its_page(monkeypatch):
    world.build()
    fill(150, "rare", 50)  # P-50, P-100, P-150
    read = loaded_proposals(monkeypatch)
    assert found(tags=["rare"]) == ["P-50", "P-100", "P-150"]
    assert sorted(map(int, read)) == [50, 100, 150]
    read.clear()
    assert found(tags=["rare"], status="executed") == []  # the status is read from its index: no proposal is read
    assert read == []


def test_a_tag_no_proposal_has_finds_nothing():
    world.build()
    fill(10, "rare", 5)
    assert pages(tags=["nothing"]) == [([], None)]
    assert pages(tags=["nothing", "rare"], count=1) == [(["P-5"], 10), (["P-10"], None)]
    assert pages(tags=["rare"], **{"from": 11}) == [([], None)]


def test_a_tag_added_removed_or_renamed_on_a_draft_changes_what_it_finds():
    world.build()
    a, b, c = file("A", tags=["parks"]), file("B", tags=["parks", "roads"]), file("C")
    for p in (a, b, c):
        Proposal[p].status = "draft"
    assert found(tags=["parks"]) == [a, b] and found(tags=["roads"]) == [b]
    ok("proposal_set_tags", "alice", proposal=c, tags=["roads", "parks"])  # added: C comes after B
    assert found(tags=["parks"]) == [a, b, c] and found(tags=["roads"]) == [b, c]
    ok("proposal_set_tags", "alice", proposal=b, tags=["roads"])  # removed
    assert found(tags=["parks"]) == [a, c] and found(tags=["roads"]) == [b, c]
    ok("proposal_set_tags", "alice", proposal=a, tags=["green"])  # renamed
    assert found(tags=["parks"]) == [c] and found(tags=["green"]) == [a]
    ok("proposal_set_tags", "alice", proposal=c, tags=[])
    assert found(tags=["parks", "roads", "green"]) == [a, b]
    assert [p["tags"] for p in ok("proposal_list", "bob", tags=["green"])["proposals"]] == [["green"]]


def test_a_proposal_with_sixteen_tags_is_found_by_each_and_listed_once():
    world.build()
    tags = [f"t{i}" for i in range(16)]
    many, one = file("Many", tags=tags), file("One", tags=["t3"])
    for tag in tags:
        assert found(tags=[tag]) == ([many, one] if tag == "t3" else [many])
    assert found(tags=tags) == [many, one]  # the union, each proposal once, in id order
    assert pages(tags=tags, count=1) == [([many], number(one)), ([one], None)]


def test_a_status_with_the_tags_keeps_the_pages_full():
    world.build()
    ids = fill(30, "rare", 2)  # 15 tagged: P-2, P-4, ..., P-30
    for p in ids[:20]:
        Proposal[p].status = "executed"  # the first ten tagged ones are executed, the last five voting
    assert pages(tags=["rare"], status="voting", count=3) == [
        (["P-22", "P-24", "P-26"], 28), (["P-28", "P-30"], None)]
    assert found(tags=["rare"], status="executed") == [ids[i] for i in range(1, 20, 2)]
    assert found(tags=["rare"], status="rejected") == []
    assert found(status="voting") == ids[20:]  # the status alone is as before


def refuse(what):
    def refused(*args, **kw):
        raise AssertionError(f"{what} was read")

    return refused


def test_a_status_with_the_tags_is_read_from_the_shorter_list(monkeypatch):
    world.build()
    ids = fill(60, "common", 1)  # every one carries it
    for p in ids[3:]:
        Proposal[p].status = "executed"  # three are still voting
    with monkeypatch.context() as patched:
        patched.setattr(Proposal, "with_tags", refuse("the list of the tag"))
        assert pages(tags=["common"], status="voting", count=2) == [(ids[:2], 3), (ids[2:3], None)]
        assert found(tags=["common", "other"], status="voting") == ids[:3]
    fill(2, "rare", 1)  # two more, voting, with another tag
    monkeypatch.setattr(proposals, "indexed_ids", refuse("the list of the status"))
    assert found(tags=["rare"], status="voting") == ["P-61", "P-62"]


def test_pages_with_any_count_and_filters_are_what_the_proposals_say_and_full():
    """Against the proposals themselves: tags that are alike, one that ends like an index entry, and a long one."""
    world.build()
    vocabulary = ["a", "a|c", "a|d", "a|n000000000001", "a|n000000000001|c", "x" * 64, "😀" * 64, "València"]
    shuffle = random.Random(7)
    ids = []
    for i in range(60):
        ids.append(file(f"p{i}", tags=shuffle.sample(vocabulary, shuffle.randint(0, 4))))
        Proposal[ids[-1]].status = shuffle.choice(STATUSES)
    everything = [Proposal[i] for i in ids]
    for _ in range(40):
        tags = shuffle.sample(vocabulary, shuffle.randint(1, 5))
        status = shuffle.choice((None,) + STATUSES)
        count = shuffle.randint(1, 9)
        expected = [p.id for p in everything if set(tags) & set(p.tags) and (not status or p.status == status)]
        args = {"tags": tags, "count": count, **({"status": status} if status else {})}
        got = pages(**args)
        assert [i for page, _ in got for i in page] == expected, args
        assert all(len(page) == count for page, _ in got[:-1]), args
        assert [after for _, after in got[:-1]] == [number(page[0]) for page, _ in got[1:]], args
        assert got[-1][1] is None


def test_retagging_among_tags_that_look_like_index_entries_leaves_every_list_right():
    """A tag is a key of the store as it is: one that is another's key plus ``|c`` or ``|n`` and an id must not touch
    that other's list, whichever way proposals come and go from them."""
    world.build()
    vocabulary = ["a", "a|c", "a|d", "a|n000000000001", "a|n000000000001|c", "a|n000000000001|n000000000002"]
    shuffle = random.Random(11)
    drafts = [file(f"d{i}", tags=shuffle.sample(vocabulary, shuffle.randint(0, 3))) for i in range(12)]
    for p in drafts:
        Proposal[p].status = "draft"
    for _ in range(80):
        ok("proposal_set_tags", "alice", proposal=shuffle.choice(drafts),
           tags=shuffle.sample(vocabulary, shuffle.randint(0, 4)))
        for tag in vocabulary:
            assert found(tags=[tag]) == [p for p in drafts if tag in Proposal[p].tags], tag


def test_a_list_of_tags_is_a_list_of_text():
    world.build()
    for tags in ("parks", [7], [["parks"]]):
        out = call("proposal_list", "bob", tags=tags)
        assert not out["ok"], tags


def test_a_proposal_filed_in_the_upgrade_window_is_listed_and_the_pass_lists_the_rest(monkeypatch):
    world.build()
    fill(12, "rare", 4)  # P-4, P-8, P-12
    forget_the_lists()
    assert Realm["1"].manifest_data.get(proposals.TAG_INDEX) is None
    assert found(tags=["rare"], count=2) == ["P-4", "P-8", "P-12"]  # by reading the proposals, as the old release did
    new = file("New", tags=["rare"])  # filed after the upgrade: listed as it is filed
    assert found(tags=["rare"]) == ["P-4", "P-8", "P-12", new]
    monkeypatch.setattr(proposals, "TAG_INDEX_PAGE", 5)
    setup.start()  # post_upgrade: the first page of the pass, and a timer for the next
    rounds = 1
    while not Realm["1"].manifest_data.get(proposals.TAG_INDEX):
        assert found(tags=["rare"]) == ["P-4", "P-8", "P-12", new]  # right at every step
        fire_timers()
        rounds += 1
        assert rounds < 10
    assert rounds == 3  # 13 proposals, 5 a message
    read = loaded_proposals(monkeypatch)
    assert found(tags=["rare"], count=2) == ["P-4", "P-8", "P-12", new]
    assert sorted(map(int, read)) == [4, 8, 12, number(new)]  # now by the lists: only the rows of the pages
    assert setup.start() is None and not fire_timers()  # done: nothing to do after the next upgrade


def test_an_upgrade_in_the_middle_of_the_pass_starts_it_again(monkeypatch):
    world.build()
    fill(12, "rare", 4)
    forget_the_lists()
    monkeypatch.setattr(proposals, "TAG_INDEX_PAGE", 5)
    setup.start()
    fire_timers()  # 10 of 12 listed, and the timer of the third page is lost with the upgrade
    fake = Database.get_instance()
    assert fake.field_index_count("Proposal", "tags", "rare") == 2
    setup.start()
    while not Realm["1"].manifest_data.get(proposals.TAG_INDEX):
        fire_timers()
    assert found(tags=["rare"]) == ["P-4", "P-8", "P-12"]
    assert fake.field_index_count("Proposal", "tags", "rare") == 3


def test_a_new_realm_has_every_proposal_listed_from_the_start():
    world.build()
    assert Realm["1"].manifest_data[proposals.TAG_INDEX] is True
