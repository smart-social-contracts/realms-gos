"""Links between realms on the real wasm (realms-gos#500; GGG's I-NEST-1 to 3): realms of one wasm on one PocketIC
instance take legislation from one another by both votes, refuse cycles, accept notices only from the capital at the
other side of a link, publish a record, report, and leave. tests/ggg/test_sync_sources.py runs the same rules in process
against scripted peers.

The realms are built once for the module and the tests below use them in order: ``source`` and ``follower`` link first,
``third`` links to both, ``far`` only through ``follower``, and ``north`` is a quarter of ``follower``.
"""

import hashlib

import pytest
from ic.candid import Types
from network import Network
from test_legislation_runtime import install

CHECKSUM = "sha256:" + "a" * 64
URL = "https://legislation.example.test/eu/releases.json"
STAND_IN = {"Record": {"url": URL, "checksum": CHECKSUM}}

# What a source's legislation does when a follower leaves, and who it will not pay.
HOOK = '''
from realms.legislation import above

class Realm(above("realm")):
    def on_link_notify(self, event):
        status = event.args["status"]
        if "Ended" in status:
            self.ggg.notification_send(recipient="User:%(founder)s", topic="link", title="a follower left",
                                       body=status["Ended"]["by"])
'''
FORBID = '''
from realms.legislation import F, above, forbid

class Treasury(above("treasury")):
    compliant_followers_only = forbid("transfer_create", F.follower_compliance == "NonCompliant",
        legislation="a source pays no follower that is behind")
'''
# Legislation a follower runs from above, and its own on top of it.
EU_TAX = '''
from realms.legislation import above, overridable

class Tax(above("tax")):
    rate = overridable(1, legislation="EU 2026/1")
'''
OWN_TAX = '''
from realms.legislation import above

class Tax(above("tax")):
    rate = 2
'''
STORE = '''
from core import layered_codex as store
for text in %(texts)r:
    store.store_chapter(store.checksum(text), text)
'''
RELAY = '''
from api import links
links.relay_release(%(release)r, %(order)r)
'''

LINK_STATUS = Types.Variant({"Requested": Types.Null, "Active": Types.Null,
                             "Ended": Types.Record({"by": Types.Principal, "at": Types.Nat64})})
COMPLIANCE = Types.Variant({"Compliant": Types.Null, "Unknown": Types.Null,
                            "NonCompliant": Types.Record({"since": Types.Nat64, "stale": Types.Vec(Types.Text)})})
LAYER = Types.Record({"version": Types.Text, "checksum": Types.Text})
NOTICE = Types.Record({"side": Types.Variant({"SyncSource": Types.Null, "Follower": Types.Null}), "status": LINK_STATUS})
REPORT = Types.Record({"layers": Types.Vec(LAYER), "compliance": COMPLIANCE, "population": Types.Nat})
FOLLOWER = Types.Record({"id": Types.Text, "capital": Types.Principal, "status": LINK_STATUS, "layers": Types.Vec(LAYER),
                         "compliance": COMPLIANCE, "population": Types.Nat, "reported_at": Types.Opt(Types.Nat64)})
LIST = Types.Record({"cursor": Types.Opt(Types.Text), "limit": Types.Opt(Types.Nat32)})
FOLLOWERS = Types.Record({"items": Types.Vec(FOLLOWER), "next": Types.Opt(Types.Text)})
RELEASES = Types.Record({"items": Types.Vec(Types.Record({
    "source": Types.Text, "version": Types.Text, "published": Types.Nat64, "effective_from": Types.Nat64,
    "commit": Types.Text, "chapters": Types.Vec(Types.Record({"domain": Types.Text, "checksum": Types.Text})),
    "calendar": Types.Opt(Types.Text)})), "next": Types.Opt(Types.Text)})


def checksum(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def source_of(realm):
    """The realm's sync sources by capital or url, as ``realm_info`` lists them: their link status."""
    out = realm.ok("realm_info", None, query=True)["sync_sources"]
    return {(s["source"].get("Realm") or s["source"]["Record"]["url"]): s["status"] for s in out}


def followers_of(realm):
    return {f["id"]: f for f in realm.ok("follower_list", "ctl", query=True)["followers"]}


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def source(net):
    return net.realm("source")


@pytest.fixture(scope="module")
def follower(net):
    return net.realm("follower")


@pytest.fixture(scope="module")
def third(net):
    return net.realm("third")


@pytest.fixture(scope="module")
def far(net):
    return net.realm("far")


@pytest.fixture(scope="module")
def north(net):
    return net.realm("north", members=("alice",))


def test_both_votes_make_a_link_active_in_either_order(source, follower, third):
    follower.govern("sync_source_add", {"source": {"Realm": source.cid}, "name": "The source"})
    assert source_of(follower) == {source.cid: "requested"} and followers_of(source) == {}
    source.govern("follower_accept", {"follower": follower.cid})
    assert source_of(follower) == {source.cid: "active"} and followers_of(source)[follower.cid]["status"] == "active"

    source.govern("follower_accept", {"follower": third.cid})  # the source votes first
    assert followers_of(source)[third.cid]["status"] == "requested" and source_of(third) == {}
    third.govern("sync_source_add", {"source": {"Realm": source.cid}, "name": "The source"})
    assert source_of(third) == {source.cid: "active"} and followers_of(source)[third.cid]["status"] == "active"


def test_a_vote_for_a_link_that_stands_is_a_conflict(source, follower, people):
    again = follower.call("sync_source_add", {"source": {"Realm": source.cid}, "name": "Again"}, sender=people["ctl"])
    assert again["kind"] == "conflict" and "already a sync source" in again["error"]
    twice = source.call("follower_accept", {"follower": follower.cid}, sender=people["ctl"])
    assert twice["kind"] == "conflict" and "already a follower" in twice["error"]


def test_a_stand_in_record_is_active_on_the_followers_vote_and_pinned_again_by_a_second_add(follower, people):
    follower.govern("sync_source_add", {"source": STAND_IN, "name": "The EU"})
    assert source_of(follower)[URL] == "active"
    follower.govern("sync_source_add", {"source": {"Record": {"url": URL, "checksum": "sha256:" + "b" * 64}}, "name": "The EU"})
    pinned = [s for s in follower.ok("realm_info", None, query=True)["sync_sources"] if "Record" in s["source"]]
    assert [(s["source"]["Record"]["checksum"], s["status"]) for s in pinned] == [("sha256:" + "b" * 64, "active")]
    http = follower.call("sync_source_add", {"source": {"Record": {"url": "http://x.test/r.json", "checksum": CHECKSUM}},
                                             "name": "Plain"}, sender=people["ctl"])
    assert "url is https" in http["error"]


def test_a_cycle_is_refused_directly_and_through_other_realms(source, follower, far, people):
    ctl = people["ctl"]
    # the follower takes legislation from the source, so the source cannot take it from the follower
    direct = source.call("sync_source_add", {"source": {"Realm": follower.cid}, "name": "Follower"}, sender=ctl)
    assert direct["kind"] == "conflict" and "a cycle" in direct["error"]
    accepted = follower.call("follower_accept", {"follower": source.cid}, sender=ctl)
    assert accepted["kind"] == "conflict" and "a cycle" in accepted["error"]
    # nor through a realm in between: far takes it from the follower, which takes it from the source
    follower.govern("follower_accept", {"follower": far.cid})
    far.govern("sync_source_add", {"source": {"Realm": follower.cid}, "name": "The follower"})
    assert source_of(far) == {follower.cid: "active"}
    through = source.call("sync_source_add", {"source": {"Realm": far.cid}, "name": "Far"}, sender=ctl)
    assert through["kind"] == "conflict" and "a cycle" in through["error"]
    assert source_of(source) == {} and far.cid not in followers_of(source)  # a refused vote leaves nothing


def test_link_notify_is_accepted_only_from_the_other_sides_capital(source, follower, north, people):
    notice = {"side": "follower", "status": "requested"}
    now = source.now()
    forged = {"side": "follower", "status": {"Ended": {"by": source.cid, "at": now}}}
    future = {"side": "follower", "status": {"Ended": {"by": follower.cid, "at": now + 600}}}
    for stranger in (people["alice"], people["mallory"], north.cid):
        assert "needs link.sync" in source.call("link_notify", notice, sender=stranger)["error"]
    # a realm that is a link's capital, but not on this side: the follower is the source's follower, not its source
    wrong_side = source.call("link_notify", {"side": "sync_source", "status": "requested"}, sender=follower.cid)
    assert "not the capital" in wrong_side["error"]
    assert "not the capital" in source.call("link_notify", notice, sender=people["ctl"])["error"]
    assert "names its sender" in source.call("link_notify", forged, sender=follower.cid)["error"]
    assert "ahead" in source.call("link_notify", future, sender=follower.cid)["error"]
    assert followers_of(source)[follower.cid]["status"] == "active"


def test_a_territory_governed_by_the_realm_alone_joins_it_as_a_quarter_and_makes_no_call_to_another_realm(
        net, source, follower, north, people):
    net.register_quarters(follower, north)
    rows = {q["id"]: q for q in follower.ok("quarter_directory", None, query=True)["quarters"]}
    assert rows[north.cid]["status"] == "active" and rows[follower.cid]["capital"]
    for op, args in (("sync_source_add", {"source": {"Realm": source.cid}, "name": "Source"}),
                     ("follower_accept", {"follower": source.cid}),
                     ("sync_source_remove", {"source": {"Realm": source.cid}}),
                     ("follower_remove", {"follower": source.cid})):
        assert "only the capital" in north.call(op, args, sender=people["ctl"])["error"]
    # nor does another realm hear from it: the quarter is no capital of a link
    assert "needs link.sync" in source.call("link_notify", {"side": "follower", "status": "requested"},
                                            sender=north.cid)["error"]


def test_a_followers_report_is_its_own_claim_kept_with_its_time_and_read_through_candid(
        costs, source, follower, north, people):
    report = {"layers": [{"version": "1.0.0", "checksum": CHECKSUM}], "compliance": "compliant", "population": 800_000}
    assert "needs link.sync" in source.call("follower_report", report, sender=north.cid)["error"]
    source.ok("follower_report", follower.cid, **report)
    kept = followers_of(source)[follower.cid]
    assert (kept["layers"], kept["population"], kept["reported_at"]) == (report["layers"], 800_000, source.now())
    costs.measure(source, "a follower's report", "from core import op\n"
                  f"op.call('follower_report', {report!r}, user={follower.cid!r})")

    listed = source.ggg("ggg_follower_list", LIST, {"cursor": [], "limit": []}, FOLLOWERS, people["ctl"], query=True)["Ok"]
    entry = next(item for item in listed["items"] if item["id"] == follower.cid)
    assert entry["status"] == {"Active": None} and entry["compliance"] == {"Compliant": None}
    assert entry["layers"] == report["layers"] and entry["population"] == 800_000 and entry["capital"].to_str() == follower.cid
    # the same report through GGG's method, from the follower's capital
    answered = source.ggg("ggg_follower_report", REPORT, {**report, "compliance": {"Unknown": None}}, FOLLOWER, follower.cid)
    assert answered["Ok"]["compliance"] == {"Unknown": None}
    assert "Forbidden" in source.ggg("ggg_follower_report", REPORT, {**report, "compliance": {"Unknown": None}}, FOLLOWER,
                                     north.cid)["Err"]


def test_a_source_publishes_a_release_and_its_record_lists_the_relayed_layers_top_to_bottom(source, people):
    texts = ["eu tax", "town tax 1.0.0", "town calendar"]
    source.shell(STORE % {"texts": texts})
    eu, own, calendar = (checksum(t) for t in texts)
    relayed = {"source": "eu", "version": "1.0.0", "published": 5, "effective_from": 6, "commit": "e" * 40,
               "chapters": {"tax": eu}, "calendar": None, "notes": {}}
    out = source.shell(RELAY % {"release": relayed, "order": ["eu", "x/source"]})
    assert "Traceback" not in out, out
    unknown = {"source": "x/source", "version": "1.0.0", "effective_from": 1_900_000_000, "commit": "c" * 40,
               "chapters": [{"domain": "tax", "checksum": "sha256:" + "f" * 64}]}
    refused = source.call("legislation_release_publish", unknown, sender=people["ctl"])
    assert "not stored by this realm" in refused["error"]
    release = {**unknown, "chapters": [{"domain": "tax", "checksum": own}], "calendar": calendar,
               "notes": {"es": "Primera versión"}}
    source.govern("legislation_release_publish", release)
    record = source.ok("legislation_releases", None, query=True)["releases"]
    assert [(r["source"], r["version"]) for r in record] == [("eu", "1.0.0"), ("x/source", "1.0.0")]
    assert record[1]["chapters"] == {"tax": own} and record[1]["calendar"] == calendar and record[1]["published"] == source.now()
    again = source.call("legislation_release_publish", release, sender=people["ctl"])
    assert again["kind"] == "conflict"

    assert source.ok("legislation_chapter", None, query=True, chapter=own)["text"] == "town tax 1.0.0"
    assert source.call("legislation_chapter", {"chapter": "sha256:" + "f" * 64}, query=True)["kind"] == "not_found"
    candid = source.ggg("ggg_legislation_releases", LIST, {"cursor": [], "limit": []}, RELEASES, people["mallory"], query=True)["Ok"]
    assert [(r["source"], r["version"]) for r in candid["items"]] == [("eu", "1.0.0"), ("x/source", "1.0.0")]
    assert candid["items"][1]["chapters"] == [{"domain": "tax", "checksum": own}]
    chapter = source.ggg("ggg_legislation_chapter", Types.Text, own, Types.Opt(Types.Vec(Types.Nat8)), people["mallory"], query=True)
    assert bytes(chapter["Ok"][0]) == b"town tax 1.0.0"
    assert source.ggg("ggg_legislation_chapter", Types.Text, "sha256:" + "f" * 64, Types.Opt(Types.Vec(Types.Nat8)),
                      people["mallory"], query=True) == {"Ok": []}


def test_the_forbid_of_a_source_reads_what_the_follower_reported(source, follower, people):
    chapters = [("x/source", "realm", HOOK % {"founder": people["founder"]}), ("x/source", "treasury", FORBID)]
    install(source, chapters, layers=("x/source",), calendars=())
    pay = {"fund": "ROOT", "to": f"Follower:{follower.cid}", "amount": 1}
    behind = {"layers": [], "compliance": {"NonCompliant": {"since": 1, "stale": ["eu"]}}, "population": 1}
    source.ok("follower_report", follower.cid, **behind)
    refused = source.call("transfer_create", pay, sender=people["ctl"])
    assert "refused by x/source/treasury/compliant_followers_only" in refused["error"]
    source.ok("follower_report", follower.cid, **{**behind, "compliance": "compliant"})
    assert "forbidden by the legislation" not in source.call("transfer_create", pay, sender=people["ctl"])["error"]


def test_a_notice_nobody_answers_is_sent_again_when_the_realm_is_back_and_after_an_upgrade(net, source, far):
    far.govern("sync_source_add", {"source": {"Realm": source.cid}, "name": "The source"})
    far.stop()
    source.govern("follower_accept", {"follower": far.cid})  # the vote stands: the notice to the stopped realm waits
    assert followers_of(source)[far.cid]["status"] == "requested"
    far.start()
    assert source_of(far)[source.cid] == "requested"
    net.wait(2 * 60)  # the source tries again
    assert source_of(far)[source.cid] == "active" and followers_of(source)[far.cid]["status"] == "active"

    far.stop()
    source.govern("follower_remove", {"follower": far.cid})
    ended = followers_of(source)[far.cid]["status"]
    assert "Ended" in ended
    source.upgrade()  # its timers are gone: it asks again when it starts
    far.start()
    net.wait(2 * 60)
    assert source_of(far)[source.cid] == ended


STUCK = '''
from ggg import SyncSource
SyncSource(id=%(capital)r, name="stuck", status="requested", checking=True)
'''


def test_a_vote_still_being_checked_when_the_realm_is_upgraded_is_ended_and_its_key_freed(net, far, north):
    """Only a walk that succeeds clears the mark, and an upgrade drops the walk."""
    out = far.shell(STUCK % {"capital": north.cid})
    assert "Traceback" not in out, out
    far.upgrade()
    ended = source_of(far)[north.cid]
    assert ended["Ended"]["by"] == far.cid
    net.wait(2 * 60)  # the notice goes out; the quarter answers that it is no link's capital
    assert "unsent" not in far.shell("from ggg import SyncSource\nprint('unsent' if SyncSource[%r].unsent else 'sent')"
                                     % north.cid)


def test_a_follower_leaves_and_the_sources_hook_on_link_notify_sees_ended(source, third, people):
    third.govern("sync_source_remove", {"source": {"Realm": source.cid}})
    ended = source_of(third)[source.cid]
    assert ended["Ended"]["by"] == third.cid
    mine = followers_of(source)[third.cid]["status"]
    assert mine == {"Ended": {"by": third.cid, "at": ended["Ended"]["at"]}}
    titles = [n["title"] for n in source.ok("notification_list", "founder", query=True)["notifications"]]
    assert titles == ["a follower left"]
    assert source.ok("notification_list", "founder", query=True)["notifications"][0]["body"] == third.cid
    again = third.call("sync_source_remove", {"source": {"Realm": source.cid}}, sender=people["ctl"])
    assert again["kind"] == "conflict"
    late = source.call("follower_report", {"layers": [], "compliance": "unknown", "population": 0}, sender=third.cid)
    assert late["kind"] == "conflict"


def test_the_source_ends_a_link_and_the_follower_keeps_its_layers(source, follower):
    chapters = [("eu", "tax", EU_TAX), ("x/follower", "tax", OWN_TAX)]
    install(follower, chapters, layers=("eu", "x/follower"), calendars=())
    before = follower.ok("codex_status", "founder", query=True)["codex"]
    source.govern("follower_remove", {"follower": follower.cid})
    ended = followers_of(source)[follower.cid]["status"]
    assert ended["Ended"]["by"] == source.cid
    assert source_of(follower)[source.cid] == ended
    assert follower.ok("codex_status", "founder", query=True)["codex"] == before  # the layers it installed stay

    # a new pair of votes opens a new link
    follower.govern("sync_source_add", {"source": {"Realm": source.cid}, "name": "The source"})
    source.govern("follower_accept", {"follower": follower.cid})
    assert source_of(follower)[source.cid] == "active" and followers_of(source)[follower.cid]["status"] == "active"


def test_a_realm_with_a_link_that_has_not_ended_does_not_become_a_quarter(source, follower, north, people):
    refused = source.call("quarter_join_federation", {"capital": north.cid}, sender=people["ctl"])
    assert refused["kind"] == "conflict" and f"follower {follower.cid}" in refused["error"]
    assert source.ok("realm_info", None, query=True)["realm"]["is_capital"]
