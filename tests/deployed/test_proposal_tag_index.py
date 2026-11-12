"""proposal_list by tag on the real wasm, in a realm that holds 4,000 proposals, 1 % of them tagged.

A realm of Valencia's size files about 3,600 proposals a year, one for each item of the Junta de Govern Local. Until this
release a tag was found by reading the proposals a page at a time and dropping those without it, so a rare tag meant
reading thousands of rows, and a page could come back empty. Now each tag has a list of the proposals that carry it.

The realm is run two ways, as test_index_upgrade.py does:

- from the previous release's own wasm, named by ``REALM_PREVIOUS_WASM`` (skipped when unset): its proposals are what
  that release saved, and what a tag cost there is measured before the upgrade;
- from this release's wasm, whose lists under the tags are then taken away: the form the previous release left.

Either is upgraded, and ``backfill_tags`` lists the proposals a page at a time. The instruction counts go to the table the
session ends with."""

import json
import os

import pytest
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
# What the previous release left: no list under any tag, and no note that there is one
FORGET = '''
from ggg import Proposal, Realm
from ic_python_db import Database

db = Database.get_instance()
tags = [f"area-{k}" for k in range(25)] + ["rare", "common"]
for tag in tags:
    base = db._fi_key("Proposal", "tags", tag)
    db._ix_drain(base, db._ix_count(base))
    db._db_storage.remove(base + "|c")
realm = Realm["1"]
realm.manifest_data = {k: v for k, v in realm.manifest_data.items() if k != "tag_index"}
print(len(tags))
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
# The first page of the pass that lists the proposals, with the instructions it used
PASS = '''
from basilisk import ic
from api import proposals

before = ic.performance_counter(0)
proposals.backfill_tags()
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


@pytest.fixture(scope="module", params=[
    pytest.param("previous", marks=pytest.mark.skipif(not PREVIOUS, reason="REALM_PREVIOUS_WASM names no wasm")),
    "rewritten"])
def realm(request, wasm_of, people, costs):
    """A realm of 4,000 proposals, whose lists are those of the previous release (none under any tag), then upgraded."""
    current = wasm_of("production")
    variant = request.param
    realm = Canister(PREVIOUS if variant == "previous" else current, installer=people["installer"])
    founder = people["founder"]
    for step, who, args in (("install", people["installer"], {"network": "local", "founder": founder}),
                            ("realm", founder, {"name": "València", "open_registration": True}),
                            ("trust_policy", founder, {"require_marketplace_approval": False}),
                            ("tokens", founder, {"accounting_currency": "DOM", "accounting_currency_decimals": 8})):
        out = realm.call("setup_step", {"step": step, **args}, sender=who)
        assert out["ok"], out
    assert realm.call("setup_launch", sender=founder)["ok"]
    assert realm.call("realm_join", sender=people["alice"])["ok"]
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
        filing_costs(realm, costs, "previous")
    else:
        run(realm, FORGET)
        first_page = int(run(realm, PASS).strip().splitlines()[-1])
        costs.report(f"backfill_tags, the first message: {INDEX_PAGE} proposals listed", first_page)
        assert first_page < 2_000_000_000  # a message may spend 40 billion
    realm.upgrade(current)
    return realm


def test_the_realm_answers_by_tag_whether_or_not_the_pass_has_listed_the_proposals(realm):
    """Straight after the upgrade nothing is certain but the answer: complete from the proposals themselves, or from the
    lists, and the same either way."""
    ids, _ = every_page(realm, tags=[RARE], count=500)
    assert [i for page in ids for i in page] == rare_ids()
    assert one_page(realm, tags=[RARE, "nothing"], status="voting")["ids"] == ["P-4000"]


def test_the_pass_lists_every_proposal_a_message_at_a_time_and_then_stops(realm):
    for _ in range(3 * PROPOSALS // INDEX_PAGE):
        if pass_is_done(realm):
            break
        realm.wait(1)
    assert pass_is_done(realm)
    realm.wait(1)  # nothing is left to do: the note stays as it is
    assert pass_is_done(realm)


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


def test_with_a_status_the_tags_pages_are_still_full_and_read_no_row_they_do_not_return(realm, costs):
    voting = one_page(realm, tags=[RARE, COMMON], status="voting", count=100)
    assert voting["ids"] == [i for i in open_ids() if i in set(rare_ids()) | set(common_ids())]
    assert voting["next"] is None
    costs.report("by tag (rare or common) and status voting, count 100, current release", voting["instructions"])
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


def test_filing_a_proposal_costs_the_lists_of_its_tags(realm, costs):
    filing_costs(realm, costs, "current")


def test_a_realm_created_by_this_release_needs_no_pass(wasm_of, people):
    fresh = Canister(wasm_of("production"), installer=people["installer"])
    assert json.loads(run(fresh, STATE).strip().splitlines()[-1]) is True
