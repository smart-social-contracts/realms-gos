"""Upgrades to this release, from a realm a release built and from a realm of an earlier build of this backend.

**A release's realm is refused.** Every release, v0.1.1 to v0.8.0-rc3, runs the realm backend this one replaced, and
its realms are end of life (docs/deployment/README.md, "Realms of the releases up to v0.8.0-rc3"). Their upgrade traps
in ``post_upgrade`` with a message that says so, before any of their rows is read; the replica rolls it back, and the
realm keeps the wasm and the data it had. The release's wasm is named by ``REALM_PREVIOUS_WASM``.

**A realm of this backend carries every row across.** It starts from the oldest build any realm runs, named by
``REALM_PREVIOUS_BUILD_WASM`` (CI packs it from ``REALM_PREVIOUS_COMMIT``, as the module those realms run), and is
seeded through that build's own API, which the seed is written for. It is upgraded over itself first, which stores its
entity schema, so that ic-python-db's schema check runs as on a realm upgraded before: a realm installed fresh stores
none, and a staging realm, upgraded many times, does. Then every row is exported through ``realm_export``, the realm is upgraded, its index lists converted, and the rows exported again:
none is missing, none differs but by what a ``migrate()`` of this release says it converts, and the lists members read
list what they did. No Json field reads as text, which the export cannot show (it parses such text back).
What it does not reach: 14 entity types change their version between that build and this release, and the seed has rows
of 5 of them (Realm, Department, Proposal, Invoice, Notification). The migrate()s of AllocationRule, Budget, Codex,
Contract, FederalVote, Land, TaxPolicy, Tender and Transfer run on no row here.

Each skips when its variable names no wasm. Without a replica, tests/ggg/test_release_realms.py checks the refusal and
tests/ggg/test_schema_upgrade.py the schema check.
"""

import json
import os

import pytest
from ic.candid import Types, encode
from pic import CONTROLLER, Canister

RELEASE = os.environ.get("REALM_PREVIOUS_WASM")
BUILD = os.environ.get("REALM_PREVIOUS_BUILD_WASM")
RATE_LIMIT = 600  # seconds the replica wants between install_code messages as dear as installing this wasm
REFUSED = "Upgrade refused, and nothing was changed: the realm keeps the release it runs and all of its data."
STORED = "import json\nfrom ic_python_db import Database\nprint(json.dumps(Database.get_instance().load('_system', '_schema')['%s']))"
ENTITIES = ("import json, ggg\nfrom ic_python_db import Entity\n"
            "print(json.dumps(sorted(n for n, c in vars(ggg).items()\n"
            "                        if isinstance(c, type) and issubclass(c, Entity))))")
# Every Json field of every row that reads as the text of a JSON value, as the realm's code reads it. A field the
# earlier build's row lacks gets the default ic-python-db injects, which for a Json field is its text: it reads as that
# text unless a migrate() sets the value (realms-gos#786). realm_export parses such text back, so only this sees it.
JSON_AS_TEXT = """import json, ggg
from ic_python_db import Entity
from core.db import Json
found = []
for name, cls in sorted(vars(ggg).items()):
    if isinstance(cls, type) and issubclass(cls, Entity):
        fields = [f for k in reversed(cls.__mro__) for f, p in vars(k).items() if isinstance(p, Json)]
        for row in cls.load_some(1, 500):
            for f in fields:
                value = getattr(row, f)
                if isinstance(value, str):
                    try:
                        json.loads(value)
                    except ValueError:
                        continue
                    found.append(f"{name}@{row._id}.{f} = {value!r}")
print(json.dumps(found))"""
PERCENT = 1_000_000
ONE = 100 * PERCENT
# The fields a migrate() of this release converts in the rows the earlier build wrote: left out of the comparison row
# by row, and checked one by one against what the seed set.
CONVERTED = {"Realm": ("manifest_data",), "Department": ("policy",),
             "Proposal": ("quorum_percent", "quorum", "threshold", "threshold_of", "tally")}
# The many-to-many sides this release's export leaves out (``Row.unlisted``): the other side lists the same links, and
# is compared like any other field (a User's profiles).
UNLISTED = {"UserProfile": ("users",), "Permission": ("users",), "Judge": ("cases",)}
# What members read, as lists: several go through the index lists ic-python-db 0.12 saved, which db_convert_indexes
# converts after the upgrade. Each must list what it listed before, as it did.
LISTS = (("proposal_list", "alice", {"status": "voting"}), ("user_list", "ctl", {}), ("department_list", "alice", {}),
         ("organization_list", "alice", {}), ("notification_list", "bob", {}), ("my_invoices", "alice", {}))


def stored_realm(realm):
    """Realm@1 as a release stores it: the text in its stable map, ``storage``, read with basilisk's ``__browse__``."""
    arg = json.dumps({"action": "get", "map": "storage", "key": "Realm@1"})
    reply = realm.raw("__browse__", encode([{"type": Types.Text, "value": arg}]))[0]["value"]
    return json.loads(json.loads(reply)["result"])


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def seed(realm, people):
    """A realm as the earlier build keeps it: launched with voting rules of its own (in whole percents, as that build
    has them), Alice, Bob and Mallory seated in a council that decides 2 of 3 with a quorum and Mallory's veto, a
    proposal of the realm and one of the council with votes on both, a token and an invoice to Alice in it, an
    organization, a home zone and a notification. The staging rehearsal of the same upgrade seeds the same. Returns the
    realm's proposal and the council's."""
    founder, ctl = people["founder"], people["ctl"]
    alice, bob, mallory = (people[n] for n in ("alice", "bob", "mallory"))
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", manifesto="Bikes first", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "governance_update", founder, threshold=60, quorum_percent=20)
    ok(realm, "setup_launch", founder)
    for member in (alice, bob, mallory):
        ok(realm, "realm_join", member)
    ok(realm, "department_create", ctl, department="Council")
    ok(realm, "position_create", ctl, department="Council", position="council.member", headcount=3)
    for member in (alice, bob, mallory):
        ok(realm, "appoint", ctl, position="council.member", user=member)
    ok(realm, "department_set_policy", ctl, department="Council", m=2, n=3, quorum_percent=50, veto=[mallory])
    realm_wide = ok(realm, "proposal_create", alice, title="Bike lane on Main St")["proposal"]
    council = ok(realm, "proposal_create", bob, title="Review the budget", department="Council")["proposal"]
    for member, choice in ((founder, "yes"), (alice, "yes"), (bob, "no")):
        ok(realm, "proposal_vote", member, proposal=realm_wide, choice=choice)
    ok(realm, "proposal_vote", alice, proposal=council, choice="yes")
    ok(realm, "token_register", ctl, symbol="AGR", ledger=realm.id.to_str(), decimals=8, fee=10)
    ok(realm, "invoice_create", ctl, payer=alice, amount=1_000_000, symbol="AGR", reason="Membership")
    ok(realm, "organization_register", ctl, organization="buildco", description="BuildCo, contractor")
    ok(realm, "zone_set", alice, zone="8a1fb46622dffff", name="Home")
    ok(realm, "notification_send", ctl, recipient=f"User:{bob}", title="Welcome", body="Your seat is ready")
    return realm_wide, council


def export(realm, entities):
    """Every row of each of ``entities``, in id order, as ``realm_export`` gives it."""
    rows = {}
    for entity in entities:
        args, rows[entity] = {"entity": entity, "from": 1, "count": 100}, []
        while args["from"]:
            page = ok(realm, "realm_export", CONTROLLER, query=True, **args)
            rows[entity] += page["rows"]
            args["from"] = page["next"]
    return rows


def lost(before, after, path=""):
    """What of ``before`` ``after`` no longer holds, as ``path: before -> after`` lines. A record may gain fields and
    keys (an upgrade adds them with their defaults) but keeps each one it had, with its value; a list, its items."""
    if isinstance(before, dict) and isinstance(after, dict):
        return [line for k, v in before.items()
                for line in (lost(v, after[k], f"{path}.{k}") if k in after else [f"{path}.{k}: {v!r} -> missing"])]
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [line for i, (b, a) in enumerate(zip(before, after)) for line in lost(b, a, f"{path}[{i}]")]
    return [] if before == after else [f"{path}: {before!r} -> {after!r}"]


@pytest.mark.skipif(not RELEASE, reason="REALM_PREVIOUS_WASM names no wasm")
def test_the_upgrade_of_a_release_realm_is_refused_and_leaves_it_as_it_was(wasm_of, people):
    realm = Canister(RELEASE, installer=people["installer"])
    realm.wait(RATE_LIMIT)
    realm.upgrade()  # the release over itself, as its realms were: it stores a schema this release's check would fail
    interface, row = realm.raw("__get_candid_interface_tmp_hack")[0]["value"], stored_realm(realm)
    assert row["_id"] == "1" and "name" in row and "id" not in row  # what tells a release's realm apart
    realm.wait(RATE_LIMIT)
    with pytest.raises(ValueError, match=REFUSED.replace(".", r"\.")):
        realm.upgrade(wasm_of("production"))
    assert realm.raw("__get_candid_interface_tmp_hack")[0]["value"] == interface  # still the release's wasm,
    realm.raw("status")  # which answers its own API
    assert stored_realm(realm) == row  # its row, unread and unchanged


@pytest.mark.skipif(not BUILD, reason="REALM_PREVIOUS_BUILD_WASM names no wasm")
def test_a_realm_of_an_earlier_build_upgraded_before_keeps_every_row(wasm_of, people):
    realm = Canister(BUILD, installer=people["installer"])
    realm_wide, council = seed(realm, people)
    realm.wait(RATE_LIMIT)
    realm.upgrade()  # the earlier build over itself: its schema is stored now
    entities = json.loads(realm.shell(ENTITIES))
    before = export(realm, entities)
    listed = [ok(realm, op, people[who], query=True, **args) for op, who, args in LISTS]
    realm.wait(RATE_LIMIT)
    realm.upgrade(wasm_of("production"))  # raises if a field was added without a default or a migrate()
    while not ok(realm, "db_convert_indexes", CONTROLLER)["done"]:  # its lists were saved by ic-python-db 0.12
        pass
    after = export(realm, entities)
    for (op, who, args), reply in zip(LISTS, listed):
        changed = lost(reply, ok(realm, op, people[who], query=True, **args))
        assert not changed, (op, changed)
    for entity, rows in before.items():
        assert len(after[entity]) >= len(rows), f"{entity}: {len(rows)} rows before, {len(after[entity])} after"
        skipped = CONVERTED.get(entity, ()) + UNLISTED.get(entity, ())
        for old, new in zip(rows, after[entity]):
            changed = lost({k: v for k, v in old.items() if k not in skipped}, new)
            assert not changed, (entity, changed)
    as_text = json.loads(realm.shell(JSON_AS_TEXT))
    assert not as_text, as_text
    # What the migrate()s convert, against what the seed set: whole percents become hundred-millionths.
    (realm_row,) = after["Realm"]
    governance = realm_row["manifest_data"]["governance"]
    assert (governance.get("voting_threshold"), governance.get("voting_quorum")) == (60 * PERCENT, 20 * PERCENT)
    assert not lost({k: v for k, v in before["Realm"][0]["manifest_data"].items() if k != "governance"},
                    realm_row["manifest_data"])
    (policy,) = [d["policy"] for d in after["Department"] if d["id"] == "Council"]
    assert policy == {"m": 2, "n": 3, "quorum": 50 * PERCENT, "veto": [people["mallory"]]}
    proposals = {p["id"]: p for p in after["Proposal"]}
    votes = {p["id"]: {c: p["tally"][c] for c in ("yes", "no", "abstain")} for p in before["Proposal"]}
    mine, theirs = proposals[realm_wide], proposals[council]
    assert "quorum_percent" not in mine and (mine["threshold"], mine["quorum"]) == (60 * PERCENT, 20 * PERCENT)
    assert (theirs["threshold"], theirs["threshold_of"], theirs["quorum"]) == (2 * ONE // 3, "eligible", 50 * PERCENT)
    assert theirs["tally"]["seats"] == 3 and "required" not in theirs["tally"]
    assert {i: {c: p["tally"][c] for c in ("yes", "no", "abstain")} for i, p in proposals.items()} == votes
    # The check ran against the earlier build's schema and stored this release's: Event.grant is new, and Event has a
    # migrate().
    assert json.loads(realm.shell(STORED % "Event"))["has_migrate"] is True
