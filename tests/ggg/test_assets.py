import pytest

import fake_chain
import world
from api.assets import quote, value
from ggg import Case, Codex, Delegation, Dispute, Land, NFTToken, Organization, Verdict, Zone
from kit import call, denied, later, ok

OK = "(variant { Ok = 4_412 : nat })"
RX = "health.prescription/1"


def metadata_reply(header, text="Text"):
    """``icrc7_token_metadata``'s reply for a token carrying ``header``, its Text label as named or hashed."""
    pairs = "; ".join(f"record {{ {quote(k)}; {value(v).replace('Text =', text + ' =', 1)} }}" for k, v in header.items())
    return f"(opt vec {{ {pairs} }})"


def order(land, status="issued", plaintiff="alice", defendant="User:mallory", n=1):
    """A court verdict ``V-n`` in a case about ``land`` between ``plaintiff`` and ``defendant``."""
    Dispute(id=f"D-{n}", claimant=plaintiff, respondent=defendant, subject=f"Land:{land}", status="accepted",
            case=f"CASE-{n}")
    case = Case(id=f"CASE-{n}", plaintiff=f"User:{plaintiff}", defendant=defendant, dispute=f"D-{n}")
    return Verdict(id=f"V-{n}", case=case, status=status).id


def agora(after_license="void"):
    depts = world.build()
    world.person("hector")
    world.seat("health.officer", "hector", depts["Health"], "license.issue", "license.revoke")
    NFTToken(id="LAND", canister="land-ledger", transferable=True)
    NFTToken(id="CRED", canister="cred-ledger", transferable=False, required_license="health")
    Codex(id="agora", status="active", manifest={"credentials": {RX: {
        "license": "health", "positions": ["clinic-b.physician"], "collection": "CRED", "public": ["class"],
        "after_license": after_license}}})
    for cid in ("land-ledger", "cred-ledger"):
        for method in ("mint", "force_transfer", "freeze_token", "unfreeze_token"):
            fake_chain.state["replies"][(cid, method)] = OK
    return depts


def license_clinic():
    lic = ok("license_apply", "dr-ana", kind="health", holder="Organization:clinic-b", department="Health")["license"]
    ok("license_decide", "hector", license=lic, approve=True)
    return lic


def prescribe(caller="dr-ana", **extra):
    args = {"schema": RX, "holder": "alice", "organization": "clinic-b", "prepared_by": "records-bot",
            "subject": "Contract:C-88", "payload_hash": "sha256:9f2c", "payload_ref": "user:alice:rx-88",
            "expires_at": fake_chain.now() + 30 * 86400, **extra}
    return call("credential_issue", caller, **args)


def ledger_calls(method):
    return [arg for _, m, arg in fake_chain.state["calls"] if m == method]


def register(owner="alice", x=12, y=40, w=3, h=2):
    return ok("land_register", "ctl", owner=owner, x=x, y=y, width=w, height=h, kind="residential")["land"]


def test_story_land_registered_minted_disputed_and_moved_by_court_order():
    agora()
    land = register("mallory")
    assert Land[land].owner == "User:mallory"
    assert ok("land_mint_nft", "ctl", land=land)["token_id"] == 4412
    (mint,) = ledger_calls("mint")
    assert 'principal "mallory"' in mint and '"ggg:subject"; variant { Text = "Land:L-1" }' in mint
    assert "already minted" in call("land_mint_nft", "ctl", land=land)["error"]
    ok("land_freeze", "ctl", land=land, reason="ownership dispute")
    assert Land[land].status == "disputed" and ledger_calls("freeze_token")
    ok("land_transfer", "ctl", land=land, to="alice", order=f"Verdict:{order(land)}")
    assert Land[land].owner == "User:alice" and 'principal "alice"' in ledger_calls("force_transfer")[0]
    ok("land_unfreeze", "ctl", land=land)
    assert Land[land].status == "active" and ledger_calls("unfreeze_token")
    assert [p["id"] for p in ok("land_list", "bob", owner="alice")["lands"]] == [land]
    assert [p["id"] for p in ok("land_map", "bob", x=10, y=35, w=10, h=10)["parcels"]] == [land]
    assert ok("land_map", "bob", x=0, w=5)["parcels"] == []


def test_land_operations_need_their_permissions():
    agora()
    assert "nft.mint" in denied("land_register", "alice", owner="alice", x=1, y=1)
    land = register()
    assert "nft.mint" in denied("land_mint_nft", "alice", land=land)
    assert "nft.freeze" in denied("land_freeze", "bob", land=land)
    assert "nft.freeze" in denied("land_unfreeze", "bob", land=land)
    order(land)
    assert "nft.force_transfer" in denied("land_transfer", "bob", land=land, to="carla", order="Verdict:V-1")
    assert "sign in" in denied("land_list", fake_chain.ANONYMOUS)
    assert "sign in" in denied("land_map", fake_chain.ANONYMOUS)
    world.person("alice", "admin")
    assert "party" in denied("land_freeze", "alice", land=land)


def test_zones_are_chosen_by_their_user():
    agora()
    ok("zone_set", "alice", zone="871f1d489ffffff", name="Riverside", kind="residential")
    assert Zone["871f1d489ffffff"].user == "alice"
    assert "someone else's" in call("zone_set", "bob", zone="871f1d489ffffff")["error"]
    assert "not your home" in call("zone_clear", "bob", zone="871f1d489ffffff")["error"]
    Delegation(id="d1", grantor="alice", delegate="claude-conn", status="active",
               scope={"operations": ["self.data_manage"]}, expires_at=fake_chain.now() + 3600)
    ok("zone_set", "claude-conn", zone="871f1d48bffffff", on_behalf_of="alice")
    assert Zone["871f1d489ffffff"].user == "" and Zone["871f1d48bffffff"].user == "alice"
    assert "sign in" in denied("zone_set", fake_chain.ANONYMOUS, zone="x")
    assert "sign in" in denied("zone_clear", fake_chain.ANONYMOUS, zone="x")
    ok("zone_clear", "alice", zone="871f1d48bffffff")
    assert [z["id"] for z in ok("land_map", "bob")["zones"]] == ["871f1d489ffffff", "871f1d48bffffff"]


def test_story_dr_ana_prescribes_and_a_pharmacy_verifies():
    agora()
    lic = license_clinic()
    out = prescribe(fields={"class": "antibiotic"})
    assert out["ok"] and out["collection"] == "CRED" and out["token_id"] == 4412
    h = out["header"]
    assert (h["ggg:issuer"], h["ggg:license"], h["ggg:signer"], h["ggg:prepared_by"]) == (
        "Organization:clinic-b", lic, "dr-ana", "records-bot")
    pharmacy = fake_chain.ANONYMOUS
    assert ok("credential_verify", pharmacy, header=h)["valid"]
    later(31 * 86400)
    assert ok("credential_verify", pharmacy, header=h)["checks"]["expired"]


def test_credential_revoke():
    agora()
    license_clinic()
    h = prescribe()["header"]
    fake_chain.state["replies"][("cred-ledger", "icrc7_token_metadata")] = metadata_reply(h)
    assert "credential.sign" in denied("credential_revoke", "alice", schema=RX, organization="clinic-b", token_id=4412)
    assert ok("credential_revoke", "dr-ana", schema=RX, organization="clinic-b", token_id=4412,
              reason="issued in error")["status"] == "revoked"
    assert ledger_calls("icrc7_token_metadata") == ["(4412 : nat)"]
    assert "ggg:revoked" in ledger_calls("freeze_token")[0]
    assert not ok("credential_verify", fake_chain.ANONYMOUS, header=h, frozen=True)["valid"]


def test_credential_revoke_needs_a_token_the_organization_issued_under_that_schema():
    agora()
    license_clinic()
    h = prescribe()["header"]
    replies = fake_chain.state["replies"]
    for theirs in ({**h, "ggg:issuer": "Organization:clinic-c"}, {**h, "ggg:schema": "edu.diploma/1"}, {}):
        replies[("cred-ledger", "icrc7_token_metadata")] = metadata_reply(theirs) if theirs else "(null)"
        assert "is not a" in call("credential_revoke", "dr-ana", schema=RX, organization="clinic-b",
                                  token_id=4412)["error"]
    assert not ledger_calls("freeze_token")
    tricky = {**h, "ggg:subject": 'say "ggg:issuer"; variant { Text = "x" }'}
    replies[("cred-ledger", "icrc7_token_metadata")] = metadata_reply(tricky, text="936_573_133")
    ok("credential_revoke", "dr-ana", schema=RX, organization="clinic-b", token_id=4412)
    assert len(ledger_calls("freeze_token")) == 1


def test_I_AST_1_credentials_need_licence_seat_and_codex():
    agora()
    world.seat("clinic-b.receptionist", "bob", Organization["clinic-b"], "credential.sign")
    world.profile("clinic-b.records_agent").allowed_to = ["credential.sign"]
    assert "no active health licence" in prescribe()["error"]
    lic = license_clinic()
    assert "no seat" in prescribe("bob")["error"]
    assert "human" in prescribe("records-bot")["error"]
    assert "credential.sign" in prescribe("alice")["error"]
    Delegation(id="d1", grantor="dr-ana", delegate="claude-conn", status="active",
               scope={"operations": ["credential.sign"]}, expires_at=fake_chain.now() + 3600)
    assert not prescribe("claude-conn", on_behalf_of="dr-ana")["ok"]
    assert "codex" in prescribe(schema="edu.diploma/1")["error"]
    assert "does not represent" in prescribe(prepared_by="carla")["error"]
    ok("license_suspend", "hector", license=lic)
    assert "no active health licence" in prescribe()["error"]
    ok("license_suspend", "hector", license=lic, resume=True)
    Codex["agora"].status = "inactive"
    assert "codex" in prescribe()["error"]
    Codex["agora"].status = "active"
    assert prescribe()["ok"] and len(ledger_calls("mint")) == 1


def test_I_AST_2_tokens_change_owner_only_by_court_order():
    agora()
    land = register("mallory")
    ok("land_mint_nft", "ctl", land=land)
    assert "missing 'order'" in call("land_transfer", "ctl", land=land, to="alice")["error"]
    order(land, status="draft")
    assert "court verdict" in call("land_transfer", "ctl", land=land, to="alice", order="Verdict:V-1")["error"]
    world.person("mallory", "admin")
    world.person("alice", "admin")
    assert "party" in denied("land_transfer", "mallory", land=land, to="alice", order="Verdict:V-1")
    assert "party" in denied("land_transfer", "alice", land=land, to="alice", order="Verdict:V-1")
    Verdict["V-1"].status = "issued"
    ok("land_transfer", "ctl", land=land, to="alice", order="Verdict:V-1")
    assert len(ledger_calls("force_transfer")) == 1


def test_land_transfer_needs_a_verdict_about_this_parcel_awarding_a_party():
    agora()
    land, other = register("mallory"), register("bob", x=0, y=0)
    elsewhere = order(other, defendant="User:bob")
    assert "does not settle" in call("land_transfer", "ctl", land=land, to="alice", order=f"Verdict:{elsewhere}")["error"]
    here = order(land, n=2)
    assert "does not settle" in call("land_transfer", "ctl", land=land, to="carla", order=f"Verdict:{here}")["error"]
    assert Land[land].owner == "User:mallory"
    ok("land_transfer", "ctl", land=land, to="alice", order=f"Verdict:{here}")
    assert (Land[land].owner, Land[land].status) == ("User:alice", "active")


def test_parcels_are_found_through_their_cells():
    agora()
    a = register(x=14, y=14, w=4, h=4)
    assert Land[a].cell == "0:0"
    assert "overlaps L-1" in call("land_register", "ctl", owner="bob", x=17, y=17)["error"]
    assert "overlaps L-1" in call("land_register", "ctl", owner="bob", x=-1, y=10, width=16, height=16)["error"]
    assert "1 to 16" in call("land_register", "ctl", owner="bob", x=100, y=0, width=17)["error"]
    b = register("bob", x=18, y=14, w=16, h=16)
    register("carla", x=-16, y=-16, w=16, h=16)
    assert [p["id"] for p in ok("land_map", "bob", x=16, y=16, w=2, h=2)["parcels"]] == [a]
    assert {p["id"] for p in ok("land_map", "bob", x=16, y=16, w=3, h=3)["parcels"]} == {a, b}
    assert [p["id"] for p in ok("land_map", "bob", x=-1, y=-1, w=1, h=1)["parcels"]] == ["L-3"]
    assert "1 to 32" in call("land_map", "bob", w=33)["error"]


def test_land_map_pages_zones():
    agora()
    for n in range(3):
        ok("zone_set", world.PEOPLE[n], zone=f"cell-{n}")
    first = ok("land_map", "bob", count=2)
    second = ok("land_map", "bob", count=2, **{"from": first["next"]})
    assert [z["id"] for z in first["zones"] + second["zones"]] == ["cell-0", "cell-1", "cell-2"]
    assert second["next"] is None


@pytest.mark.parametrize("rule, still_valid", [("void", False), ("keep", True)])
def test_I_AST_3_licence_revocation_stops_mints_and_codex_decides_the_rest(rule, still_valid):
    agora(rule)
    lic = license_clinic()
    h = prescribe()["header"]
    ok("license_revoke", "hector", license=lic)
    assert "no active health licence" in prescribe()["error"]
    assert ok("credential_verify", fake_chain.ANONYMOUS, header=h)["valid"] is still_valid


def test_I_AST_4_no_personal_data_on_tokens():
    agora()
    license_clinic()
    assert "public fields" in prescribe(fields={"patient_name": "Alice Smith"})["error"]
    assert "payload_hash" in prescribe(payload_hash="aspirin 3x/day")["error"]
    out = prescribe(fields={"class": "antibiotic"})
    keys = {"ggg:" + k for k in ("v", "realm", "quarter", "network", "schema", "subject", "issuer", "license",
                                 "signer", "prepared_by", "issued_at", "expires_at", "payload_hash", "payload_ref")}
    assert set(out["header"]) == keys | {"class"}
    (mint,) = ledger_calls("mint")
    assert "Alice" not in mint and all(f'"{k}"' in mint for k in keys | {"class"})


def test_I_AST_5_a_parcel_has_exactly_one_owner():
    agora()
    register()
    assert "overlaps L-1" in call("land_register", "ctl", owner="bob", x=14, y=41)["error"]
    assert "cannot own land" in call("land_register", "ctl", owner="Department:Health", x=0, y=0)["error"]
    assert "unknown" in call("land_register", "ctl", owner="nobody", x=0, y=0)["error"]
    other = ok("land_register", "ctl", owner="Organization:buildco", x=15, y=40)["land"]
    ok("land_mint_nft", "ctl", land=other)
    assert f'principal "{fake_chain.SELF}"' in ledger_calls("mint")[0]
    assert {p.owner for p in Land.load_some(1, 10)} == {"User:alice", "Organization:buildco"}
