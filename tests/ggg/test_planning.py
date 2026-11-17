"""Planning (ggg I-AST-6, I-LIC-4): the zones a parcel lies in, the parcel a licence is for, and the zones of that
parcel as a fact of license_decide, so legislation can hold back the decisions on a plan's parcels."""

import pytest

import fake_chain
import world
from api.licenses import in_zones
from core import op
from core.db import NotFound
from ggg import Land, License
from kit import call, denied, ok
from test_forbids import codex_of, fresh_realm  # noqa: F401 (the realm each test starts with)

SEAFRONT_SUSPENSION = """
from realms.legislation import F, above, forbid

class Planning(above("planning")):
    seafront_suspension = forbid("license_decide", F.zones.contains("seafront"),
                                 legislation="the plan holds back the licences on its parcels")
"""

PLAN = {"zone": "seafront", "name": "Seafront plan", "kind": "planning"}


def agora():
    depts = world.build()
    world.person("hector")
    world.seat("health.officer", "hector", depts["Health"], "license.issue", "license.revoke")
    ok("zone_define", "ctl", **PLAN)
    return ok("land_register", "ctl", owner="alice", x=12, y=40, width=3, height=2, kind="residential")["land"]


def test_a_parcel_lies_in_the_zones_the_realm_defines():
    parcel = agora()
    ok("zone_define", "ctl", zone="harbour", name="Harbour", kind="district")
    assert "realm.configure" in denied("land_zone_set", "alice", land=parcel, zones=["seafront"])
    ok("land_zone_set", "ctl", land=parcel, zones=["seafront", "harbour"])
    assert Land[parcel].lies_in == ["seafront", "harbour"]
    assert ok("land_list", "bob")["lands"][0]["lies_in"] == ["seafront", "harbour"]
    ok("land_zone_set", "ctl", land=parcel, zones=[])
    assert Land[parcel].lies_in == []


def test_a_parcels_zones_are_refused_when_unknown_repeated_or_too_many():
    parcel = agora()
    ok("zone_set", "alice", zone="871f1d489ffffff")  # a member's home cell is no zone of the realm
    for index in range(8):
        ok("zone_define", "ctl", zone=f"z{index}", name="Z", kind="planning")
    assert call("land_zone_set", "ctl", land=parcel, zones=["nowhere"])["kind"] == "not_found"
    assert "not a zone" in call("land_zone_set", "ctl", land=parcel, zones=["nowhere"])["error"]
    assert "not a zone" in call("land_zone_set", "ctl", land=parcel, zones=["871f1d489ffffff"])["error"]
    twice = call("land_zone_set", "ctl", land=parcel, zones=["seafront", "seafront"])
    assert twice["kind"] == "invalid_argument" and "each named once" in twice["error"]
    assert "at most 8" in call("land_zone_set", "ctl", land=parcel, zones=["seafront"] + [f"z{i}" for i in range(8)])["error"]
    assert "unknown Land" in call("land_zone_set", "ctl", land="L-404", zones=[])["error"]
    assert Land[parcel].lies_in == []


def test_a_licence_is_for_a_parcel_that_exists_and_a_renewal_keeps_it():
    parcel = agora()
    unknown = call("license_apply", "alice", kind="building", land="L-404")
    assert unknown["kind"] == "not_found" and "unknown Land" in unknown["error"]
    lic = ok("license_apply", "alice", kind="building", land=parcel, department="Health")["license"]
    assert License[lic].land == parcel
    assert ok("license_list", fake_chain.ANONYMOUS, holder="User:alice")["licenses"][0]["land"] == parcel
    ok("license_decide", "hector", license=lic, approve=True)
    renewal = ok("license_renew", "alice", license=lic)["license"]
    assert License[renewal].land == parcel
    assert License[ok("license_apply", "alice", kind="professional")["license"]].land == ""


def test_the_zones_of_the_licences_parcel_are_a_fact_of_its_decision():
    parcel = agora()
    ok("land_zone_set", "ctl", land=parcel, zones=["seafront"])
    on_parcel = ok("license_apply", "alice", kind="building", land=parcel)["license"]
    on_none = ok("license_apply", "alice", kind="professional")["license"]
    assert in_zones({"license": on_parcel}) == {"zones": ["seafront"]}
    assert in_zones({"license": on_none}) == {"zones": []}
    with pytest.raises(NotFound):
        in_zones({"license": "LIC-404"})
    assert op.OPS["license_decide"]["derived"] is in_zones


def test_a_planning_chapter_holds_back_the_decisions_on_a_plans_parcels_and_no_others():
    parcel = agora()
    outside = ok("land_register", "ctl", owner="alice", x=40, y=40, kind="residential")["land"]
    ok("land_zone_set", "ctl", land=parcel, zones=["seafront"])
    on_plan = ok("license_apply", "alice", kind="building", land=parcel, department="Health")["license"]
    off_plan = ok("license_apply", "alice", kind="building", land=outside, department="Health")["license"]
    codex_of({("x/town", "planning"): SEAFRONT_SUSPENSION})
    refused = denied("license_decide", "hector", license=on_plan, approve=True)
    assert "forbidden by the legislation" in refused and "seafront_suspension" in refused
    assert License[on_plan].status == "applied"
    assert ok("license_decide", "hector", license=off_plan, approve=True)["status"] == "active"
