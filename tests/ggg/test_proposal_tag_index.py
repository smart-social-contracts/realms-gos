"""proposal_list by tag reads the lists of the tags (Proposal.with_tags), not the proposals: its pages are full, in the
order of an unfiltered list, and cost what the page costs, whatever the realm holds. A realm upgraded from a release
that kept no such lists answers by reading the proposals until ``relist_tags`` has listed them all, which it does
again after every upgrade, so that a rollback to such a release leaves no list wrong."""

import random
import re

import kit
import world
from api import proposals, setup
from ggg import Proposal, Realm
from ic_python_db import Database
from kit import call, fire_timers, ok

STATUSES = ("voting", "executed", "rejected")
# Tags that end like what the index adds to the name of a list: its count (``|c``), its mark (``|d``) and its entries
# (``|n`` and twelve digits). ``a|n0000000000`` is ``a|n`` and ten digits: of the length of an entry of ``a``, and sorted
# among them from the proposal 100 on.
LOOK_ALIKES = ["a", "a|c", "a|d", "a|n000000000001", "a|n0000000000", "a|n0000000000|c", "a|n000000000", "a%7Cc", "a%"]


def file(title, **extra):
    return ok("proposal_create", "alice", title=title, **extra)["proposal"]


def number(proposal):
    return int(proposal.split("-")[1])


def pass_id_100():
    """Untagged proposals up to P-100, so that those filed next have the ids that sort after a key like ``|n0000000000``."""
    for i in range(100):
        file(f"before {i}")


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
    """The realm as the release before this one left it: no list under any tag, no record of what proposals are on, and no
    note that there are lists."""
    storage = Database.get_instance()._db_storage
    for key in [k for k in storage.keys() if k.startswith(("_fi:Proposal:tags:", "_tag_lists@"))]:
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
    """Against the proposals themselves, with ids above 100: tags that are alike, ones that end like an index entry, and
    a long one."""
    world.build()
    vocabulary = LOOK_ALIKES + ["x" * 64, "😀" * 64, "València"]
    shuffle = random.Random(7)
    pass_id_100()
    ids = []
    for i in range(40):
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


def test_a_tag_that_ends_like_an_index_entry_hides_no_proposal():
    """``parks|n0000000000`` is ``parks`` and what the index adds to the name of a list for an entry: its count key sorted
    among the entries of ``parks`` from the proposal 100 on, and a read that met it came back short and was taken for the
    end of the list."""
    world.build()
    pass_id_100()
    parks = [file(f"Parks {i}", tags=["parks"]) for i in range(10)]  # P-101 to P-110
    like = file("Look-alike", tags=["parks|n0000000000"])
    other = file("Other", tags=["parks|n0000000000|c", "parks%7Cn0000000000"])
    for count in (1, 2, 3, 20):
        assert found(tags=["parks"], count=count) == parks, count
        assert found(tags=["parks", "elsewhere"], count=count) == parks, count  # two tags
        assert found(tags=["parks|n0000000000"], count=count) == [like], count
        assert found(tags=["parks", "parks|n0000000000"], count=count) == parks + [like], count
    assert found(tags=["parks|n0000000000|c"]) == [other] and found(tags=["parks%7Cn0000000000"]) == [other]
    assert pages(tags=["parks"], count=2)[-1] == ([parks[8], parks[9]], None)
    # The name of a list holds no ``|``, so that no key of one lies among the entries of another
    keys = [k for k in Database.get_instance()._db_storage.keys() if k.startswith("_fi:Proposal:tags:")]
    assert keys and all(re.fullmatch(r"_fi:Proposal:tags:[^|]*\|(c|n\d{12})", k) for k in keys), keys
    assert len({Proposal.tag_key(t) for t in LOOK_ALIKES}) == len(LOOK_ALIKES)  # and no two tags share a name


def test_a_read_of_a_list_that_comes_back_short_is_not_the_end_of_it():
    """ic-python-db cuts a read at the number of keys asked for and then drops those that are not entries of the list. A
    key of another list among the entries is not there to be had (a tag's name never holds ``|``), but a read that comes
    back short is not taken for the end for that."""
    world.build()
    pass_id_100()
    parks = [file(f"Parks {i}", tags=["parks"]) for i in range(10)]
    stray = "_fi:Proposal:tags:parks|n0000000000|c"  # what the name ``parks|n0000000000`` used to make
    Database.get_instance()._db_storage.insert(stray, "7")
    for count in (1, 2, 3, 20):
        assert found(tags=["parks"], count=count) == parks, count
    assert Proposal.with_tags(["parks"], 1, 4) == [number(p) for p in parks[:4]]


def test_retagging_among_tags_that_look_like_index_entries_leaves_every_list_right():
    """A tag is a key of the store as it is: one that is another's key plus ``|c`` or ``|n`` and an id must not touch
    that other's list, whichever way proposals come and go from them."""
    world.build()
    pass_id_100()
    shuffle = random.Random(11)
    drafts = [file(f"d{i}", tags=shuffle.sample(LOOK_ALIKES, shuffle.randint(0, 3))) for i in range(12)]
    for p in drafts:
        Proposal[p].status = "draft"
    for _ in range(80):
        ok("proposal_set_tags", "alice", proposal=shuffle.choice(drafts),
           tags=shuffle.sample(LOOK_ALIKES, shuffle.randint(0, 4)))
        for tag in LOOK_ALIKES:
            assert found(tags=[tag], count=3) == [p for p in drafts if tag in Proposal[p].tags], tag


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
    setup.start()  # the next upgrade: the lists are not trusted until the pass has been through again
    assert not Realm["1"].manifest_data.get(proposals.TAG_INDEX)
    while not Realm["1"].manifest_data.get(proposals.TAG_INDEX):
        fire_timers()


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


def listed(tag):
    """The ids on the list of ``tag``, as the lists themselves hold them."""
    return Proposal.with_tags([tag], 1, 10_000)


def finish_the_pass():
    while not Realm["1"].manifest_data.get(proposals.TAG_INDEX):
        fire_timers()


def test_what_a_release_without_lists_filed_or_retagged_is_put_right_by_the_next_upgrade(monkeypatch):
    """A rollback to the release before this one, then an upgrade again: the lists stand as the rollback left them."""
    world.build()
    a, b, c = file("A", tags=["parks"]), file("B", tags=["parks", "roads"]), file("C", tags=["roads"])
    for p in (a, b, c):
        Proposal[p].status = "draft"
    with monkeypatch.context() as release_before:
        release_before.setattr(Proposal, "list_tags", lambda self: None)
        ok("proposal_set_tags", "alice", proposal=a, tags=["roads"])  # renamed
        ok("proposal_set_tags", "alice", proposal=b, tags=[])  # removed
        d = file("D", tags=["parks", "green"])  # filed
    assert listed("parks") == [number(a), number(b)] and listed("green") == []  # wrong, and the flag says they are right
    monkeypatch.setattr(proposals, "TAG_INDEX_PAGE", 2)
    setup.start()  # the upgrade after the rollback
    assert not Realm["1"].manifest_data.get(proposals.TAG_INDEX)
    expected = {"parks": [d], "roads": [a, c], "green": [d]}
    while True:
        for tag, ids in expected.items():
            assert found(tags=[tag]) == ids  # right at every step
        if Realm["1"].manifest_data.get(proposals.TAG_INDEX):
            break
        fire_timers()
    for tag, ids in expected.items():
        assert listed(tag) == [number(i) for i in ids]  # and the lists themselves are exact


def test_after_any_rollbacks_the_next_upgrade_leaves_every_list_exact(monkeypatch):
    world.build()
    vocabulary = LOOK_ALIKES + ["b", "c", "x" * 64]
    shuffle = random.Random(3)
    pass_id_100()
    drafts = []

    def file_draft():
        drafts.append(file(f"d{len(drafts)}", tags=shuffle.sample(vocabulary, shuffle.randint(0, 3))))
        Proposal[drafts[-1]].status = "draft"

    def retag():
        ok("proposal_set_tags", "alice", proposal=shuffle.choice(drafts), tags=shuffle.sample(vocabulary, shuffle.randint(0, 4)))

    for _ in range(6):
        file_draft()
    for _ in range(4):
        with monkeypatch.context() as release_before:  # rolled back, and a while of what it does
            release_before.setattr(Proposal, "list_tags", lambda self: None)
            for step in range(12):
                (file_draft if step % 4 == 0 else retag)()
        setup.start()  # upgraded again
        finish_the_pass()
        for _ in range(3):  # and a while of this release's own
            (file_draft if shuffle.random() < 0.3 else retag)()
        for tag in vocabulary:
            assert listed(tag) == [number(p) for p in drafts if tag in Proposal[p].tags], tag


def test_the_pass_puts_proposals_back_on_lists_that_are_gone_whatever_the_records_say():
    """The records say what each proposal is on; the pass adds it to the lists of its tags all the same, so that lists
    kept under another name (an earlier naming of the keys) are rebuilt by the next upgrade."""
    world.build()
    fill(6, "rare", 2)
    storage = Database.get_instance()._db_storage
    for key in [k for k in storage.keys() if k.startswith("_fi:Proposal:tags:")]:
        storage.remove(key)
    assert listed("rare") == []
    setup.start()
    assert listed("rare") == [2, 4, 6] and found(tags=["rare"]) == ["P-2", "P-4", "P-6"]


def test_a_new_realm_has_every_proposal_listed_from_the_start():
    world.build()
    assert Realm["1"].manifest_data[proposals.TAG_INDEX] is True
