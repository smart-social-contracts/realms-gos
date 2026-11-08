"""A realm upgraded from a release before ic-python-db 0.13 keeps its Member, Vote and Proposal lists, on the real wasm
(realms-gos, ic-python-db#36).

Before 0.13 every index value was one JSON array: a vote list of 20,000 ids is one array, rewritten whole by every
vote. 0.13 stores an entry per id and converts an array the first time its index is read; a query's conversion is
discarded, so every query pays for it again until an update touches the list. ``db_convert_indexes`` converts them
a batch at a time. The realm is run two ways:

- from the previous release's own wasm, named by ``REALM_PREVIOUS_WASM`` (skipped when unset): its lists are what
  that release saved;
- from this release's wasm, whose lists are rewritten as arrays through the controller's ``__shell__``: the form
  an earlier release saved, which CI can always build.

The vote lists of ``P-1`` are given PHANTOM more ids than the realm has votes, written the way the old release wrote
them: only ``Vote.find_by`` and ``len`` see them, so the rows in view stay the real ones.
"""

import json
import os

import pytest
from pic import Canister

PHANTOM = 20_000
PREVIOUS = os.environ.get("REALM_PREVIOUS_WASM")

# Rewrites the lists of P-1, and the members', as arrays of ids: the real ones first, then PHANTOM more.
LEGACY = f'''
import json
from ic_python_db import Database
import ggg

db = Database.get_instance()
parent = ggg.Proposal["P-1"]._id
LISTS = {{
    "_fi:Member:status:active": "Member status active",
    "_fi:Proposal:status:voting": "Proposal status voting",
    "_fi:Vote:proposal_id:P-1": "Vote proposal_id P-1",
    f"_ri:Proposal:{{parent}}:votes": "Proposal votes",
}}


def ids_of(base):
    if hasattr(db, "_ix_all"):  # a release that stores one entry per id: read them, then undo that
        ids = db._ix_all(base)
        db._ix_drain(base, 10**9)
        if db._db_storage.get(base + "|c") is not None:
            db._db_storage.remove(base + "|c")
        return ids
    return json.loads(db._db_storage.get(base) or "[]")


for base in LISTS:
    ids = ids_of(base)
    if "votes" in base or "Vote" in base:
        ids += [str(i) for i in range(100, 100 + {PHANTOM})]
    db._db_storage.insert(base, json.dumps(ids))
realm = ggg.Realm["1"]  # a release that saved arrays kept no note of a conversion
data = dict(realm.manifest_data)
data.pop("index_conversion", None)
realm.manifest_data = data
print(json.dumps({{base: db._db_storage.get(base)[:1] for base in LISTS}}))
'''
STATE = '''
import json
from ic_python_db import Database
import ggg

db = Database.get_instance()
parent = ggg.Proposal["P-1"]._id
bases = ["_fi:Member:status:active", "_fi:Proposal:status:voting", "_fi:Vote:proposal_id:P-1", f"_ri:Proposal:{parent}:votes"]
legacy = [b for b in bases if (db._db_storage.get(b) or "")[:1] == "["]
print(json.dumps({"legacy": legacy, "bases": bases}))
'''
TIMED = '''
from basilisk import ic
from ic_python_db import Database
db = Database.get_instance()
before = ic.performance_counter(0)
count = db.field_index_count("Vote", "proposal_id", "P-1")
page = db.field_index_page("Vote", "proposal_id", "P-1", 1, 50)
print(count, len(page), ic.performance_counter(0) - before)
'''
# One db_convert_indexes call as the controller's shell makes it, with the instructions it used
OP_COST = '''
import json
from basilisk import ic
from core import op
before = ic.performance_counter(0)
reply = json.loads(op.run(op.dispatch("db_convert_indexes", "{}")))
print(json.dumps({"reply": reply, "instructions": ic.performance_counter(0) - before}))
'''
# The ids of P-1's votes: from the array as stored (a read that converts nothing), or through the index
RAW_VOTES = '''
import json
from ic_python_db import Database
ids = [int(i) for i in json.loads(Database.get_instance()._db_storage.get("_fi:Vote:proposal_id:P-1"))]
print(json.dumps({"n": len(ids), "ascending": ids == sorted(ids), "head": ids[:3], "tail": ids[-2:]}))
'''
READ_VOTES = '''
import json
from ic_python_db import Database
db = Database.get_instance()
ids = [int(i) for i in db.field_index_page("Vote", "proposal_id", "P-1", 1, 100000)]
print(json.dumps({"n": db.field_index_count("Vote", "proposal_id", "P-1"), "ascending": ids == sorted(ids),
                  "head": ids[:3], "tail": ids[-2:]}))
'''


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def run(realm, code):
    out = realm.shell(code)
    assert "Traceback" not in out, out
    return out


def convert(realm, who, **args):
    """db_convert_indexes until it is done; returns the replies."""
    replies = []
    while not replies or not replies[-1]["done"]:
        replies.append(ok(realm, "db_convert_indexes", who, **args))
        assert len(replies) < 50, replies
    return replies


@pytest.fixture(scope="module", params=[
    pytest.param("previous", marks=pytest.mark.skipif(not PREVIOUS, reason="REALM_PREVIOUS_WASM names no wasm")),
    "rewritten"])
def realm(request, wasm_of, people):
    """A realm with an open proposal, a vote and members, whose lists are those of an earlier release, then upgraded."""
    current = wasm_of("production")
    realm = Canister(PREVIOUS if request.param == "previous" else current, installer=people["installer"])
    founder, alice = people["founder"], people["alice"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_step", founder, step="tokens", accounting_currency="DOM", accounting_currency_decimals=8)
    ok(realm, "setup_launch", founder)
    for member in (alice, people["bob"]):
        ok(realm, "realm_join", member)
    ok(realm, "proposal_create", alice, title="Bike lane")
    ok(realm, "proposal_vote", alice, proposal="P-1", choice="yes")  # one of three: the proposal stays open
    run(realm, LEGACY)
    realm.upgrade(current)
    return realm


def test_the_lists_start_in_the_form_of_the_earlier_release(realm):
    """post_upgrade reads the proposals by status, in an update: that list is converted by then."""
    state = json.loads(run(realm, STATE))
    assert set(state["bases"]) - set(state["legacy"]) <= {"_fi:Proposal:status:voting"}
    assert len(state["legacy"]) >= 3, state


def test_a_query_before_the_conversion_runs_out_of_instructions(realm, people):
    """It converts the list it reads, in the query: 20,000 ids are past its 5 billion instructions, and a conversion
    that fits is discarded with the query and paid again by the next. That is why the op exists."""
    with pytest.raises(ValueError, match="exceeded the limit of 5000000000 instructions"):
        ok(realm, "proposal_get", people["alice"], query=True, proposal="P-1")
    assert "_fi:Vote:proposal_id:P-1" in json.loads(run(realm, STATE))["legacy"]


def test_the_op_converts_every_list_in_batches_and_loses_no_id(realm, people):
    before = json.loads(run(realm, RAW_VOTES))
    assert before["n"] == PHANTOM + 1
    batches = convert(realm, people["ctl"], ids=10_000)
    # The two lists of P-1 hold more than one batch between them
    assert len([b for b in batches if b["lists"]]) >= 2 and batches[-1]["done"]
    assert sum(b["ids"] for b in batches) >= 2 * (PHANTOM + 1)
    assert json.loads(run(realm, STATE))["legacy"] == []
    after = json.loads(run(realm, READ_VOTES))
    assert after == before and after["ascending"]


def test_a_converted_list_is_read_and_paged_in_few_instructions(realm):
    count, page, instructions = run(realm, TIMED).split()
    assert (int(count), int(page)) == (PHANTOM + 1, 50) and int(instructions) < 50_000_000, instructions


def test_members_votes_and_proposals_are_the_same_after_the_conversion(realm, people):
    alice, founder = people["alice"], people["founder"]
    out = ok(realm, "proposal_get", alice, query=True, proposal="P-1")
    assert [v["voter"] for v in out["votes"]] == [alice] and out["tally"]["yes"] == 1 and out["eligible"] == 3
    assert ok(realm, "realm_monitor", people["ctl"], query=True)["rows"]["Member"] == 3
    ok(realm, "proposal_vote", founder, proposal="P-1", choice="no")  # a vote into the converted list
    out = ok(realm, "proposal_get", alice, query=True, proposal="P-1")
    assert [v["voter"] for v in out["votes"]] == [alice, founder] and out["tally"]["no"] == 1
    assert json.loads(run(realm, READ_VOTES))["n"] == PHANTOM + 2


def test_a_finished_realm_answers_at_once_and_only_the_host_runs_it(realm, people):
    assert convert(realm, people["ctl"]) == [{"ok": True, "lists": 0, "ids": 0, "done": True}]
    done = json.loads(run(realm, OP_COST))
    assert done["reply"]["done"] and done["instructions"] < 60_000_000, done
    out = realm.call("db_convert_indexes", {}, sender=people["alice"])
    assert not out["ok"] and out.get("denied"), out


def test_a_new_realm_is_done_from_the_start(wasm_of, people):
    """Nothing in a new database is an array, so the op reads no index entry: it costs one ordinary call (about 30
    million instructions for the dispatch, the authorization and the audit event), where a pass costs billions."""
    fresh = Canister(wasm_of("production"), installer=people["installer"])
    assert ok(fresh, "db_convert_indexes", people["ctl"]) == {"ok": True, "lists": 0, "ids": 0, "done": True}
    cost = json.loads(run(fresh, OP_COST))
    assert cost["reply"]["done"] and cost["instructions"] < 60_000_000, cost
