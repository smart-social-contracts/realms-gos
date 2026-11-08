"""Identity, documents, zones and languages on the real wasm (realms-gos#547).

Rows saved before these fields existed are read with their defaults after an upgrade, and the new operations work
on them. A member's `home_zone` is set at join and by `user_edit`, and `Zone.population` follows it. An attribute's
kind and value are in no event of the audit log.
"""

import json

import pytest
from ic.candid import Types
from pic import Canister

ATTRIBUTE = Types.Record({
    "id": Types.Text, "human": Types.Text, "kind": Types.Text, "value": Types.Text, "issuer": Types.Text,
    "ref": Types.Text, "status": Types.Variant({name: Types.Null for name in ("Pending", "Verified", "Rejected")}),
    "verified_at": Types.Opt(Types.Nat64), "expires_at": Types.Opt(Types.Nat64)})
SUBMIT_ARGS = Types.Record({"kind": Types.Text, "value": Types.Text, "issuer": Types.Opt(Types.Text),
                            "ref": Types.Text, "expires_at": Types.Opt(Types.Nat64)})
LIST_ARGS = Types.Record({"kind": Types.Opt(Types.Text), "cursor": Types.Opt(Types.Text),
                          "limit": Types.Opt(Types.Nat32), "on_behalf_of": Types.Opt(Types.Principal)})
PAGE = Types.Record({"items": Types.Vec(ATTRIBUTE), "next": Types.Opt(Types.Text)})

CELL = "871f1d489ffffff"
# The fields this release added to what the last one stored: its rows do not have them.
STRIP_ADDED = '''
from ic_python_db import Database
import ggg

db = Database.get_instance()
ADDED = {
    "User": ("%(alice)s", ("language", "home_zone")),
    "Identity": ("ID-1", ("category", "expires_at")),
    "Document": ("DOC-1", ("kind", "languages", "facts", "public", "publications")),
    "Realm": ("1", ("languages",)),
    "License": ("LIC-1", ("decision_due",)),
    "Department": ("Health", ("kind",)),
    "Zone": ("%(cell)s", ("defined", "population")),
}
for name, (key, added) in ADDED.items():
    entity = getattr(ggg, name)[key]._id
    row = db.load(name, entity)
    db.save(name, entity, {k: v for k, v in row.items() if k not in added})
    assert not set(added) & set(db.load(name, entity)), name
print("stripped")
'''
READ_ROW = '''
import json
from ic_python_db import Database
import ggg

print(json.dumps(Database.get_instance().load("{name}", getattr(ggg, "{name}")["{key}"]._id)))
'''


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def stored(realm, name, key):
    """The row as it is in storage, which an upgrade has emptied the heap of."""
    return json.loads(realm.shell(READ_ROW.format(name=name, key=key)))


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A launched realm with a member, an identity, a licence, a department document and a home cell, saved as the last
    release saved them (without this release's fields), then upgraded."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    ctl, founder, alice = people["ctl"], people["founder"], people["alice"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_launch", founder)
    ok(realm, "realm_join", alice, nickname="Alice")
    ok(realm, "department_create", ctl, department="Health")
    ok(realm, "profile_create", ctl, profile="inspector", allowed_to=["document.manage"])
    ok(realm, "position_create", ctl, department="Health", position="health.inspector", profile="inspector")
    ok(realm, "appoint", ctl, position="health.inspector", user=alice)
    ok(realm, "identity_submit", alice, kind="passport", issuer="ES", ref="vault:alice-passport")
    ok(realm, "license_apply", alice, kind="professional")
    ok(realm, "zone_set", alice, zone=CELL, name="Riverside", kind="residential")
    ok(realm, "document_store", alice, department="Health", title="Inspection report", languages=["en"],
       ref="inline:x", hash="sha256:ab12")
    assert "stripped" in realm.shell(STRIP_ADDED % {"alice": alice, "cell": CELL})
    realm.upgrade()
    return realm


def test_rows_saved_before_these_fields_read_with_their_defaults(realm, people):
    alice, ctl = people["alice"], people["ctl"]
    card = ok(realm, "user_get", ctl, query=True, user=alice)["user"]
    assert (card["nickname"], card["language"], card["home_zone"]) == ("Alice", "", "")
    assert ok(realm, "realm_info", alice, query=True)["realm"]["languages"] == []
    assert ok(realm, "department_get", ctl, query=True, department="Health")["department"]["kind"] == ""
    doc = ok(realm, "document_get", alice, query=True, document="DOC-1")["document"]
    assert (doc["kind"], doc["languages"], doc["facts"], doc["public"], doc["publications"]) == ("", [], {}, None, [])
    licence = ok(realm, "license_list", alice, query=True, holder=f"User:{alice}")["licenses"][0]
    assert licence["id"] == "LIC-1" and licence.get("decision_due", 0) == 0
    assert ok(realm, "identity_review", ctl, identity="ID-1", status="verified")["status"] == "verified"
    identity = stored(realm, "Identity", "ID-1")
    assert (identity.get("category", ""), identity.get("expires_at", 0), identity["status"]) == ("", 0, "verified")


def test_an_old_home_cell_is_a_cell_and_not_a_zone_of_the_realm(realm, people):
    ctl, alice = people["ctl"], people["alice"]
    assert "home cell" in realm.call("zone_define", {"zone": CELL, "name": "x", "kind": "district"}, sender=ctl)["error"]
    assert realm.call("user_edit", {"user": alice, "home_zone": CELL}, sender=ctl)["kind"] == "invalid_argument"
    cells = {z["id"]: z for z in ok(realm, "land_map", alice, query=True)["zones"]}
    assert cells[CELL]["population"] == 0


def test_home_zone_is_set_at_join_and_by_user_edit_and_the_population_follows(realm, people):
    ctl, alice, bob = people["ctl"], people["alice"], people["bob"]
    for zone in ("riverside", "hillside"):
        ok(realm, "zone_define", ctl, zone=zone, name=zone.title(), kind="district")
    bad = realm.call("realm_join", {"home_zone": "nowhere"}, sender=bob)
    assert not bad["ok"] and bad["kind"] == "invalid_argument"
    assert realm.call("user_get", {"user": bob}, sender=ctl, query=True)["ok"] is False  # nothing was written
    ok(realm, "realm_join", bob, language="es-ES", home_zone="riverside")
    ok(realm, "user_edit", ctl, user=alice, home_zone="riverside", language="ca")

    def population():
        zones = ok(realm, "land_map", alice, query=True)["zones"]
        return {z["id"]: z["population"] for z in zones if z["id"] in ("riverside", "hillside")}

    assert population() == {"riverside": 2, "hillside": 0}
    ok(realm, "user_edit", ctl, user=alice, home_zone="hillside")
    assert population() == {"riverside": 1, "hillside": 1}
    card = ok(realm, "user_get", ctl, query=True, user=alice)["user"]
    assert (card["home_zone"], card["language"]) == ("hillside", "ca")
    realm.upgrade()
    assert population() == {"riverside": 1, "hillside": 1}


def test_a_document_is_published_and_anyone_reads_its_public_copy(realm, people):
    alice = people["alice"]
    ok(realm, "document_publish", alice, document="DOC-1", ref="pub/report.pdf", hash="sha256:cd34")
    anyone = realm.call("document_get", {"document": "DOC-1"}, sender=None, query=True)
    assert anyone["ok"], anyone
    assert anyone["document"]["public"] == {"ref": "pub/report.pdf", "hash": "sha256:cd34"}
    assert (anyone["document"]["ref"], anyone["document"]["hash"]) == ("", "")
    assert ok(realm, "document_get", alice, query=True, document="DOC-1")["document"]["ref"] == "inline:x"


def test_an_attribute_is_submitted_listed_and_reviewed_through_the_ggg_methods(realm, people):
    alice, bob, ctl = people["alice"], people["bob"], people["ctl"]
    arg = {"kind": "large_family", "value": "general", "issuer": ["regional-government"], "ref": "vault:title",
           "expires_at": []}
    got = realm.ggg("ggg_attribute_submit", SUBMIT_ARGS, arg, ATTRIBUTE, alice)["Ok"]
    assert (got["kind"], got["value"], got["status"]) == ("large_family", "general", {"Pending": None})
    ask = {"kind": [], "cursor": [], "limit": [], "on_behalf_of": []}
    mine = realm.ggg("ggg_attribute_list", LIST_ARGS, ask, PAGE, alice, query=True)["Ok"]
    assert [a["id"] for a in mine["items"]] == [got["id"]]
    assert realm.ggg("ggg_attribute_list", LIST_ARGS, ask, PAGE, bob, query=True)["Ok"]["items"] == []
    ok(realm, "attribute_review", ctl, attribute=got["id"], status="verified")
    events = ok(realm, "events", ctl, query=True)["events"]
    assert {"attribute_submit", "attribute_review"} <= {e["op"] for e in events}
    assert "large_family" not in json.dumps(events) and "general" not in json.dumps(events)


def test_a_licence_application_is_dated_by_its_procedure_and_working_days_fail_closed(realm, people):
    """The legislation library compiles on first use in the canister: the deadline counts there as in the unit tests."""
    ctl, alice = people["ctl"], people["alice"]
    ok(realm, "procedure_define", ctl, kind="business", initiated_by="the applicant", silence="positive",
       decision_deadline={"count": 1, "unit": "day"})
    ok(realm, "license_apply", alice, kind="business")
    licences = ok(realm, "license_list", alice, query=True, holder=f"User:{alice}", kind="business")["licenses"]
    applied = stored(realm, "License", licences[0]["id"])["applied_at"]
    # one day from the day of the application, and that day counts in full: the second midnight after it, in UTC
    assert licences[0]["decision_due"] == (applied // 86400 + 2) * 86400
    ok(realm, "procedure_define", ctl, kind="business", initiated_by="the applicant",
       decision_deadline={"count": 15, "unit": "working_day"})
    refused = realm.call("license_apply", {"kind": "business"}, sender=alice)
    assert not refused["ok"] and refused["kind"] == "unavailable", refused
