"""proposal_list by tag on the real wasm, in a realm that holds 4,000 proposals, 1 % of them tagged.

A realm of Valencia's size files about 3,600 proposals a year, one for each item of the Junta de Govern Local. Until this
release a tag was found by reading the proposals a page at a time and dropping those without it, so a rare tag meant
reading thousands of rows, and a page could come back empty. Now each tag has a list of the proposals that carry it.

The realm is run two ways, as test_index_upgrade.py does:

- from the previous release's own wasm, named by ``REALM_PREVIOUS_WASM`` (skipped when unset): its proposals are what
  that release saved, and what a tag cost there is measured before the upgrade;
- from this release's wasm, whose lists under the tags are then taken away: the form the previous release left.

Either is upgraded, and ``relist_tags`` lists the proposals a page at a time, after this upgrade and every one after it:
the last test rolls the realm back, in effect, and upgrades it again. The instruction counts go to the table the session
ends with."""

import json
import os

import pytest
from ic.candid import Types, decode, encode
from ic.principal import Principal
from pic import Canister

PREVIOUS = os.environ.get("REALM_PREVIOUS_WASM")
PROPOSALS, RARE_EVERY, COMMON_EVERY, OPEN = 4_000, 100, 3, 20  # the last 20 are still being voted on
SEED_BATCH = 250
RARE, COMMON = "rare", "common"
INDEX_PAGE = 200  # proposals.TAG_INDEX_PAGE


def rare_ids():
    return [f"P-{i}" for i in range(RARE_EVERY, PROPOSALS + 1, RARE_EVERY)]


def common_ids():
    return [f"P-{i}" for i in range(COMMON_EVERY, PROPOSALS + 1, COMMON_EVERY)]


def open_ids():
    return [f"P-{i}" for i in range(PROPOSALS - OPEN + 1, PROPOSALS + 1)]


# Proposals as a realm holds them once decided: a title, a paragraph, the action it carried and its result, with one to
# three tags (an area of the town for all, and the tags a test looks for).
SEED = '''
import json
from ggg import Department, Proposal

root = Department["Administration"]
for i in range(%(first)d, %(last)d + 1):
    tags = ["area-%%d" %% (i %% 25)]
    if i %% %(rare_every)d == 0:
        tags.append("rare")
    if i %% %(common_every)d == 0:
        tags.append("common")
    Proposal(
        id=f"P-{i}", title=f"Junta de Govern Local, assumpte {i}: aprovació de l'expedient de contractació",
        description="Aprovar l'expedient de contractació dels serveis de manteniment de les instal·lacions municipals, "
                    "amb un pressupost base de licitació i un termini d'execució fixats al plec de clàusules. " * 3,
        proposer="2vxsx-fae", department=root, status="voting" if i > %(open_from)d else "executed",
        action={"op": "contract_create", "args": {"title": f"Contracte {i}", "value": 1000 * i, "supplier": "BuildCo",
                                                  "starts_at": 1790000000, "ends_at": 1800000000}},
        opens_at=1790000000, deadline=1790600000, tally={"yes": 7, "no": 1, "abstain": 1, "eligible": 9},
        result={"outcome": "accepted"}, tags=tags)
print(Proposal.count())
'''
# What the previous release left: no list under any tag, no record of what the proposals are on, and no note that
# there are lists
FORGET = '''
from ggg import Proposal, Realm
from ic_python_db import Database

db = Database.get_instance()
tags = [f"area-{k}" for k in range(25)] + ["rare", "common"]
for tag in tags:
    base = db._fi_key("Proposal", "tags", tag)
    db._ix_drain(base, db._ix_count(base))
    db._db_storage.remove(base + "|c")
for i in range(1, %(last)d + 1):
    db.delete("_tag_lists", str(i))
realm = Realm["1"]
realm.manifest_data = {k: v for k, v in realm.manifest_data.items() if k != "tag_index"}
print(len(tags))
'''
# What the release before this one does to a realm rolled back to it: it re-tags a proposal and files another with
# no list kept. Returns the id it filed.
RELEASE_BEFORE = '''
from ggg import Department, Proposal

keep = Proposal.list_tags
Proposal.list_tags = lambda self: None
try:
    Proposal["P-100"].tags = ["area-0"]  # was area-0 and rare
    Proposal["P-101"].tags = ["area-1", "rare"]  # was area-1
    filed = Proposal(id=f"P-{Proposal.max_id() + 1}", title="Filed by the release before", proposer="2vxsx-fae",
                     department=Department["Administration"], status="draft", tags=["rare"])
finally:
    Proposal.list_tags = keep
print(filed.id)
'''
STATE = '''
import json
from ggg import Realm

print(json.dumps(Realm["1"].manifest_data.get("tag_index")))
'''
# One proposal_list as the controller's shell makes it, with the instructions it used
LIST = '''
import json
from basilisk import ic
from core import op

before = ic.performance_counter(0)
reply = json.loads(op.run(op.dispatch("proposal_list", %(args)r)))
print(json.dumps({"ids": [p["id"] for p in reply["proposals"]], "next": reply["next"], "ok": reply["ok"],
                  "instructions": ic.performance_counter(0) - before}))
'''
# Proposals that tags and a status hardly share: the first ``%(executed_to)d`` are executed and carry sixteen tags; of
# the ones after them, those from ``%(rejected_from)d`` on are rejected and those from ``%(matches_from)d`` on are voting
# and carry the tags (the matches, at the far end of every list); the others are voting and carry none.
CROWDED = '''
from ggg import Department, Proposal

root = Department["Administration"]
tags = [f"c{k}" for k in range(16)]
for i in range(%(first)d, %(last)d + 1):
    carrier = i <= %(executed_to)d or i >= %(matches_from)d
    status = "executed" if i <= %(executed_to)d else "rejected" if %(rejected_from)d <= i < %(matches_from)d else "voting"
    Proposal(id=f"P-{i}", title=f"Item {i}", description="Aprovar l'expedient.", proposer="2vxsx-fae", department=root,
             status=status, tags=tags if carrier else [])
'''
# The same page as a GGG query builds it: ggg_proposal_list takes the rows the operation lists and makes a GGG record of
# each, which the caller gets as Candid. In the shell, to count the instructions up to the Candid.
GGG_LIST = '''
import json
from basilisk import ic
import ggg_api

before = ic.performance_counter(0)
out = ggg_api.read("proposal_list", "ProposalListArgs", %(arg)r, "ProposalPageReply")
spent = ic.performance_counter(0) - before
ok = out.get("Ok") or {}
print(json.dumps({"ok": "Ok" in out, "items": len(ok.get("items") or []), "next": ok.get("next"), "instructions": spent}))
'''
# What the realm's limit on checks is, as the realm has it
LIMIT = '''
from api import proposals

print(proposals.CHECK_LIMIT)
'''
GGG_LIST_ARGS = Types.Record({
    "cursor": Types.Opt(Types.Text), "limit": Types.Opt(Types.Nat32), "tags": Types.Opt(Types.Vec(Types.Text)),
    "status": Types.Opt(Types.Variant({name: Types.Null for name in (
        "Draft", "Voting", "Accepted", "ExecutionScheduled", "Rejected", "Expired", "Executed", "Failed", "Withdrawn")}))})
# The same listing with the limit on checks lifted, as the realm did before it: what it costs, whatever it costs
UNBOUNDED = '''
import json
from basilisk import ic
from api import proposals
from core import op

kept, proposals.CHECK_LIMIT = proposals.CHECK_LIMIT, 10**9
try:
    before = ic.performance_counter(0)
    reply = json.loads(op.run(op.dispatch("proposal_list", %(args)r)))
    print(json.dumps({"ids": [p["id"] for p in reply["proposals"]], "next": reply["next"], "ok": reply["ok"],
                      "instructions": ic.performance_counter(0) - before}))
finally:
    proposals.CHECK_LIMIT = kept
'''
# The first message of the pass that lists the proposals, with the instructions it used
PASS = '''
from basilisk import ic
from api import proposals

before = ic.performance_counter(0)
proposals.relist_tags()
print(ic.performance_counter(0) - before)
'''


# What filing a proposal costs the realm in rows and lists: ten proposals with ``%(tags)d`` tags, on average
ADD = '''
from basilisk import ic
from ggg import Department, Proposal

root = Department["Administration"]
tags = [f"t{k}" for k in range(%(tags)d)]
before = ic.performance_counter(0)
for _ in range(10):
    Proposal(id=f"P-{Proposal.max_id() + 1}", title="Measured", proposer="2vxsx-fae", department=root, status="draft",
             tags=tags)
print((ic.performance_counter(0) - before) // 10)
'''


def run(realm, code):
    out = realm.shell(code)
    assert "Traceback" not in out, out
    return out


def seed(realm, first, last):
    for start in range(first, last + 1, SEED_BATCH):
        end = min(start + SEED_BATCH - 1, last)
        run(realm, SEED % {"first": start, "last": end, "rare_every": RARE_EVERY, "common_every": COMMON_EVERY,
                           "open_from": PROPOSALS - OPEN})


def one_page(realm, **args):
    reply = json.loads(run(realm, LIST % {"args": json.dumps(args)}).strip().splitlines()[-1])
    assert reply["ok"], reply
    return reply


def ggg_query(realm, sender, **args):
    """``ggg_proposal_list`` as a GGG caller asks it, a real query with its limit of 5 billion instructions: it replies, or
    it traps and this raises. Returns the Candid reply, decoded without names."""
    value = {"cursor": [args["cursor"]], "limit": [args["limit"]], "tags": [args["tags"]],
             "status": [{args["status"]: None}] if args.get("status") else []}
    realm.pic.set_sender(Principal.from_str(sender))
    reply = realm.pic.query_call(realm.id, "ggg_proposal_list", encode([{"type": GGG_LIST_ARGS, "value": value}]))
    return decode(bytes(reply))[0]["value"]


def unbounded_page(realm, **args):
    reply = json.loads(run(realm, UNBOUNDED % {"args": json.dumps(args)}).strip().splitlines()[-1])
    assert reply["ok"], reply
    return reply


def every_page(realm, **args):
    """Follow ``next``: the ids found, the instructions of each page and the ids of each."""
    ids, costs, start = [], [], 1
    while start is not None:
        reply = one_page(realm, **args, **{"from": start})
        ids.append(reply["ids"])
        costs.append(reply["instructions"])
        start = reply["next"]
    return ids, costs


def filing_costs(realm, costs, release):
    for tags in (0, 16):
        cost = int(run(realm, ADD % {"tags": tags}).strip().splitlines()[-1])
        costs.report(f"a proposal row filed with {tags} tags, {release} release", cost)


def pass_is_done(realm):
    return json.loads(run(realm, STATE).strip().splitlines()[-1]) is True


def launched(realm, people):
    """``realm`` set up and launched, with Alice a member."""
    founder = people["founder"]
    for step, who, args in (("install", people["installer"], {"network": "local", "founder": founder}),
                            ("realm", founder, {"name": "València", "open_registration": True}),
                            ("trust_policy", founder, {"require_marketplace_approval": False}),
                            ("tokens", founder, {"accounting_currency": "DOM", "accounting_currency_decimals": 8})):
        out = realm.call("setup_step", {"step": step, **args}, sender=who)
        assert out["ok"], out
    assert realm.call("setup_launch", sender=founder)["ok"]
    assert realm.call("realm_join", sender=people["alice"])["ok"]
    return realm


@pytest.fixture(scope="module", params=[
    pytest.param("previous", marks=pytest.mark.skipif(not PREVIOUS, reason="REALM_PREVIOUS_WASM names no wasm")),
    "rewritten"])
def realm(request, wasm_of, people, costs):
    """A realm of 4,000 proposals, whose lists are those of the previous release (none under any tag), then upgraded."""
    current = wasm_of("production")
    variant = request.param
    realm = launched(Canister(PREVIOUS if variant == "previous" else current, installer=people["installer"]), people)
    seed(realm, 1, PROPOSALS)
    realm.variant = variant
    if variant == "previous":
        ids, spent = every_page(realm, tags=[RARE], count=500)
        assert [i for page in ids for i in page] == rare_ids()
        realm.scan_cost = sum(spent)  # all 40, by reading the proposals
        costs.report("by tag, rare (40 of 4,000), count 500, previous release: every page", sum(spent))
        costs.report("by tag, rare (40 of 4,000), count 500, previous release: one page", max(spent))
        ids, spent = every_page(realm, tags=[RARE])
        costs.report("by tag, rare (40 of 4,000), count 100, previous release: every page", sum(spent))
        costs.report("by tag, rare (40 of 4,000), count 100, previous release: one page", max(spent))
        assert len(ids) == 40  # a client follows ``next`` through all 4,000 proposals for 40
        voting = one_page(realm, tags=[RARE, COMMON], status="voting", count=100)
        costs.report("by tag (rare or common) and status voting, count 100, previous release", voting["instructions"])
        filing_costs(realm, costs, "previous")
    else:
        run(realm, FORGET % {"last": PROPOSALS})
        first_page = int(run(realm, PASS).strip().splitlines()[-1])
        costs.report(f"relist_tags, one message: {INDEX_PAGE} proposals listed", first_page)
        assert first_page < 2_000_000_000  # a message may spend 40 billion
    realm.upgrade(current)
    return realm


def test_the_realm_answers_by_tag_whether_or_not_the_pass_has_listed_the_proposals(realm):
    """Straight after the upgrade nothing is certain but the answer: complete from the proposals themselves, or from the
    lists, and the same either way."""
    ids, _ = every_page(realm, tags=[RARE], count=500)
    assert [i for page in ids for i in page] == rare_ids()
    assert one_page(realm, tags=[RARE, "nothing"], status="voting")["ids"] == ["P-4000"]


def wait_for_the_pass(realm):
    for _ in range(3 * PROPOSALS // INDEX_PAGE):
        if pass_is_done(realm):
            return
        realm.wait(1)
    assert pass_is_done(realm)


def test_the_pass_lists_every_proposal_a_message_at_a_time_and_then_stops(realm, costs):
    wait_for_the_pass(realm)
    realm.wait(1)  # nothing is left to do: the note stays as it is
    assert pass_is_done(realm)
    again = int(run(realm, PASS).strip().splitlines()[-1])  # a pass on lists that are right: the rows, and no write
    costs.report(f"relist_tags, one message: {INDEX_PAGE} proposals already listed", again)
    wait_for_the_pass(realm)


def test_a_page_of_the_rare_tag_is_full_in_one_call_and_costs_what_it_holds(realm, costs):
    reply = one_page(realm, tags=[RARE])
    assert reply["ids"] == rare_ids() and reply["next"] is None
    costs.report("by tag, rare (40 of 4,000), count 100, current release: one page", reply["instructions"])
    one = one_page(realm, tags=[RARE], count=1)
    assert one["ids"] == ["P-100"] and one["next"] == 200
    costs.report("by tag, rare (40 of 4,000), count 1, current release: one page", one["instructions"])
    small = one_page(realm, tags=[RARE], count=10)
    assert small["ids"] == rare_ids()[:10] and small["next"] == 1100
    costs.report("by tag, rare (40 of 4,000), count 10, current release: one page", small["instructions"])
    assert small["instructions"] < reply["instructions"] / 2  # what it costs is its page, not the proposals around it
    assert reply["instructions"] < 500_000_000
    if realm.variant == "previous":  # the same 40 proposals, found by reading the rows
        assert reply["instructions"] * 20 < realm.scan_cost


def test_a_page_of_a_common_tag_and_the_pages_after_it_follow_the_order_of_an_unfiltered_list(realm, costs):
    ids, spent = every_page(realm, tags=[COMMON], count=100)
    assert [i for page in ids for i in page] == common_ids()
    assert all(len(page) == 100 for page in ids[:-1]) and len(ids) == 14
    costs.report("by tag, common (1,333 of 4,000), count 100, current release: one page", max(spent))
    assert max(spent) < 1_500_000_000


def test_with_a_status_the_tags_pages_are_still_full_and_read_from_the_shorter_list(realm, costs):
    voting = one_page(realm, tags=[RARE, COMMON], status="voting", count=100)
    assert voting["ids"] == [i for i in open_ids() if i in set(rare_ids()) | set(common_ids())]
    assert voting["next"] is None
    costs.report("by tag (rare or common) and status voting, count 100, current release", voting["instructions"])
    assert voting["instructions"] < 200_000_000  # 20 are voting: its list is the shorter, and 1,373 carry the tags
    executed = every_page(realm, tags=[RARE], status="executed", count=20)[0]
    assert [i for page in executed for i in page] == rare_ids()[:-1] and len(executed[0]) == 20


def test_a_tag_of_64_four_byte_characters_is_filed_and_found(realm, people):
    """A tag is up to 256 bytes, and the key of its list is longer still: the store takes it."""
    tag = "\U0001F600" * 64
    filed = realm.call("proposal_create", {"title": "Emoji", "tags": [tag, RARE]}, sender=people["alice"])
    assert filed["ok"], filed
    mine = filed["proposal"]
    assert one_page(realm, tags=[tag])["ids"] == [mine]
    assert one_page(realm, tags=[RARE], count=500)["ids"] == rare_ids() + [mine]


def test_a_tag_that_ends_like_an_index_entry_hides_no_proposal(realm, people):
    """``rare|n0000000000`` is ``rare`` and what the index adds for an entry of it: its count key sorted among the entries
    of ``rare`` from the proposal 100 on (the rare tag is on P-100 and every hundredth after it)."""
    expected = one_page(realm, tags=[RARE], count=500)["ids"]
    filed = realm.call("proposal_create", {"title": "Look-alike", "tags": ["rare|n0000000000"]}, sender=people["alice"])
    assert filed["ok"], filed
    for count in (1, 2, 7):
        pages, _ = every_page(realm, tags=[RARE], count=count)
        assert [i for page in pages for i in page] == expected, count
        assert all(len(page) == count for page in pages[:-1]), count
    both, _ = every_page(realm, tags=[RARE, "rare|n0000000000"], count=2)
    assert [i for page in both for i in page] == sorted(expected + [filed["proposal"]], key=lambda i: int(i.split("-")[1]))


def test_filing_a_proposal_costs_the_lists_of_its_tags(realm, costs):
    filing_costs(realm, costs, "current")


def test_an_upgrade_after_a_rollback_puts_right_what_the_release_before_filed_and_retagged(realm, people):
    """It kept no lists, so those of this release are wrong for P-100 and P-101 and miss what it filed. The next upgrade
    does the pass again, so the realm answers right straight away (from the proposals) and after it (from the lists)."""
    before = one_page(realm, tags=[RARE], count=500)["ids"]
    filed = run(realm, RELEASE_BEFORE).strip().splitlines()[-1]
    expected = sorted((set(before) - {"P-100"}) | {"P-101", filed}, key=lambda i: int(i.split("-")[1]))
    assert one_page(realm, tags=[RARE], count=500)["ids"] == before  # the lists say what they said
    realm.wait(600)  # the replica wants time between installs of a wasm this size
    realm.upgrade()
    pages, _ = every_page(realm, tags=[RARE], count=500)  # by the scan until the pass is done: its pages can be short
    assert [i for page in pages for i in page] == expected
    wait_for_the_pass(realm)
    assert one_page(realm, tags=[RARE], count=500)["ids"] == expected
    assert "P-100" in one_page(realm, tags=["area-0"], count=500)["ids"]  # what it kept is still listed


@pytest.fixture(scope="module", params=[
    pytest.param(4_000, id="the ids come from the tags"), pytest.param(3_950, id="the ids come from the status")])
def crowded(request, wasm_of, people):
    """A realm of 4,000 proposals that tags and a status hardly share. Rejected from P-3950 on, the status is the shorter
    list and its ids are checked in each tag; with none rejected the tags' lists are, and their ids are checked in the
    status."""
    return crowd(wasm_of, people, total=4_000, executed_to=1_999, rejected_from=request.param, matches_from=4_000)


@pytest.fixture(scope="module")
def filled(wasm_of, people):
    """A realm in which a call can spend a long limit on checks and then fill a page. The status's ids drive the listing
    (5,001 of them, against 5,003 that carry the tag), 4,500 of them carry no tag, and then come the 501 that do: a limit
    up to 4,500 reads would be spent on the misses and no more, and a larger one would go on to fill the page."""
    return crowd(wasm_of, people, total=9_503, executed_to=4_502, rejected_from=10**9, matches_from=9_003)


def crowd(wasm_of, people, total, executed_to, rejected_from, matches_from):
    realm = launched(Canister(wasm_of("production"), installer=people["installer"]), people)
    for first in range(1, total + 1, SEED_BATCH):
        run(realm, CROWDED % {"first": first, "last": min(first + SEED_BATCH - 1, total), "executed_to": executed_to,
                              "rejected_from": rejected_from, "matches_from": matches_from})
    return realm


def test_tags_and_a_status_that_few_proposals_share_cost_a_call_under_the_limit_of_a_query(crowded, costs):
    """The one proposal they share is the last: to find it, the whole of the shorter list has to be checked, in as many
    calls as it takes. The checks of a call come to about 1 billion instructions at most; the page has no rows but the
    one. No call spends more than 2.5 billion, half of what a query may, at any page size."""
    for names in (["c0"], [f"c{k}" for k in range(16)]):
        for count in (100, 500):
            found, spent, start = [], [], 1
            while start is not None:
                reply = one_page(crowded, tags=names, status="voting", count=count, **{"from": start})
                found += reply["ids"]
                spent.append(reply["instructions"])
                start = reply["next"]
            assert found == ["P-4000"], (names, found)
            what = f"{len(names)} tag{'s' * (len(names) > 1)} and status voting, count {count}, {len(spent)} call{'s' * (len(spent) > 1)}"
            costs.report(f"{what}: the dearest call", max(spent))
            costs.report(f"{what}: all of them", sum(spent))
            assert max(spent) < 2_500_000_000, (names, count, spent)
            assert len(spent) <= 30, (names, count, spent)
        lifted = unbounded_page(crowded, tags=names, status="voting")
        assert lifted["ids"] == ["P-4000"] and lifted["next"] is None
        costs.report(f"{len(names)} tag{'s' * (len(names) > 1)} and status voting, no limit on checks: one call", lifted["instructions"])


def test_a_filled_page_by_tags_and_a_status_costs_its_checks_and_its_rows_under_what_a_query_may_spend(filled, people, costs):
    """The bound on checks (``CHECK_LIMIT`` reads of an index, about 0.4 million instructions each) is not on the rows of
    the page, about 6 million each: 3 billion for 500. A call that reaches the matches has spent the limit less what the
    page took. With a limit of 2,000 reads that is 1,500 misses and 3.75 billion; with 5,000 it would be 4,500 misses
    and 4.8 billion, and with 10,000 more than a query may spend. So the realm's limit is held to 2,000 here, which this
    realm is built for, and no call of the operation may cost 4.5 billion, a tenth under what a query may spend.

    Two paths are bounded. The operation, ``proposal_list``: its calls are measured in the shell (an update, so that what
    is over 5 billion is measured too). And ``ggg_proposal_list``, the GGG method over it, which makes a GGG record of
    each row and encodes the page as Candid on top of the operation: it returns at most 200 items, is held to 4 billion
    in the shell, and as a real query, with its limit of 5 billion, it must not trap."""
    assert int(run(filled, LIMIT).strip().splitlines()[-1]) == 2_000, "this realm is built for a limit of 2,000 reads"
    matches = [f"P-{i}" for i in range(9_003, 9_504)]
    found, spent, start = [], [], 1
    while start is not None:  # as a client does, from the first id
        reply = one_page(filled, tags=["c0"], status="voting", count=500, **{"from": start})
        found += reply["ids"]
        spent.append(reply["instructions"])
        start = reply["next"]
    assert found == matches and max(spent) < 4_500_000_000, (len(spent), spent)
    costs.report(f"1 tag and status voting, a page of 500 after the misses, {len(spent)} calls: the dearest", max(spent))
    # From where exactly the limit's worth of misses less the page come before the matches: the dearest a call can be
    after_misses = {"tags": ["c0"], "status": "voting", "count": 500, "from": 9_003 - 1_500}
    reply = one_page(filled, **after_misses)
    assert reply["ids"] == matches[:500] and reply["next"] == 9_503
    costs.report("1 tag and status voting, a page of 500 after 1,500 misses: one call", reply["instructions"])
    assert reply["instructions"] < 4_500_000_000
    # The same page through the GGG method, which returns at most 200 (ggg_api.PAGE_CAP) when 500 are asked for. Uncapped it
    # came to 4.5 billion in the shell and trapped as a query: a GGG record and its Candid on top of each row.
    arg = {"cursor": "7503", "limit": 500, "status": {"Voting": None}, "tags": ["c0"]}
    ggg = json.loads(run(filled, GGG_LIST % {"arg": arg}).strip().splitlines()[-1])
    assert ggg["ok"] and ggg["items"] == 200 and ggg["next"] == "9203", ggg
    costs.report("1 tag and status voting, 500 asked after 1,500 misses, ggg_proposal_list (200): to the Candid", ggg["instructions"])
    assert ggg["instructions"] < 4_000_000_000
    assert ggg_query(filled, people["alice"], cursor="7503", tags=["c0"], status="Voting", limit=500)


def test_a_realm_created_by_this_release_needs_no_pass(wasm_of, people):
    fresh = Canister(wasm_of("production"), installer=people["installer"])
    assert json.loads(run(fresh, STATE).strip().splitlines()[-1]) is True
