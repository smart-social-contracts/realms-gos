import fake_chain
import world
from ggg import Delegation, License, Organization
from kit import call, denied, later, ok
from api.licenses import active

YEAR = 365 * 86400


def agora():
    depts = world.build()
    world.person("hector")
    world.seat("health.officer", "hector", depts["Health"], "license.issue", "license.revoke")
    return depts


def apply_clinic(**extra):
    return ok("license_apply", "dr-ana", kind="health", holder="Organization:clinic-b", department="Health",
              evidence_ref="file:clinic-b-dossier", **extra)["license"]


def test_story_clinic_b_is_licensed_suspended_and_revoked():
    agora()
    lic = apply_clinic()
    assert License[lic].status == "applied"
    out = ok("license_decide", "hector", license=lic, approve=True)
    assert out["status"] == "active" and out["expires_at"] == fake_chain.now() + 2 * YEAR
    assert License[lic].issued_by.id == "Health"
    patient = fake_chain.ANONYMOUS
    assert ok("license_verify", patient, holder="Organization:clinic-b", kind="health")["valid"]
    assert [x["id"] for x in ok("license_list", patient, holder="Organization:clinic-b")["licenses"]] == [lic]
    ok("license_suspend", "hector", license=lic)
    assert not ok("license_verify", patient, license=lic)["valid"]
    ok("license_suspend", "hector", license=lic, resume=True)
    assert ok("license_verify", patient, license=lic)["valid"]
    ok("license_revoke", "hector", license=lic)
    assert not ok("license_verify", patient, license=lic)["valid"]
    assert "revoked" in call("license_suspend", "hector", license=lic)["error"]


def test_apply_and_renew_authorization():
    agora()
    assert "sign in" in denied("license_apply", fake_chain.ANONYMOUS, kind="business")
    assert "cannot apply" in call("license_apply", "alice", kind="health", holder="Organization:clinic-b")["error"]
    mine = ok("license_apply", "alice", kind="professional")["license"]
    assert License[mine].holder == "User:alice"
    Delegation(id="d1", grantor="alice", delegate="claude-conn", status="active",
               scope={"operations": ["license.apply"]}, expires_at=fake_chain.now() + 3600)
    ok("license_apply", "claude-conn", kind="education", on_behalf_of="alice")
    assert "is applied" in call("license_renew", "alice", license=mine)["error"]
    ok("license_decide", "hector", license=mine, approve=True, department="Health")
    assert "sign in" in denied("license_renew", fake_chain.ANONYMOUS, license=mine)
    ok("license_renew", "alice", license=mine)


def test_decide_suspend_revoke_need_their_permissions():
    agora()
    lic = apply_clinic()
    assert "license.issue" in denied("license_decide", "alice", license=lic, approve=True)
    Delegation(id="d1", grantor="hector", delegate="claude-conn", status="active",
               scope={"operations": ["license.issue"]}, expires_at=fake_chain.now() + 3600)
    denied("license_decide", "claude-conn", license=lic, approve=True, on_behalf_of="hector")
    ok("license_decide", "hector", license=lic, approve=True)
    assert "license.revoke" in denied("license_suspend", "alice", license=lic)
    assert "license.revoke" in denied("license_revoke", "alice", license=lic)
    rejected = apply_clinic()
    assert ok("license_decide", "hector", license=rejected)["status"] == "rejected"
    assert "not an application" in call("license_decide", "hector", license=rejected, approve=True)["error"]


def test_I_LIC_1_nobody_decides_for_themselves_or_their_organization():
    depts = agora()
    world.seat("health.officer", "dr-ana", depts["Health"])
    world.seat("clinic-b.nurse", "carla", Organization["clinic-b"])
    world.seat("health.officer", "carla", depts["Health"])
    world.seat("health.officer", "alice", depts["Health"])
    lic = apply_clinic()
    assert "party" in denied("license_decide", "dr-ana", license=lic, approve=True)
    assert "party" in denied("license_decide", "carla", license=lic, approve=True)
    own = ok("license_apply", "alice", kind="professional", department="Health")["license"]
    assert "party" in denied("license_decide", "alice", license=own, approve=True)
    ok("license_decide", "hector", license=lic, approve=True)
    assert "party" in denied("license_revoke", "dr-ana", license=lic)


def test_I_LIC_2_only_active_unexpired_licences_count():
    agora()
    lic = apply_clinic()
    assert active("Organization:clinic-b", "health") is None
    ok("license_decide", "hector", license=lic, approve=True, expires_at=fake_chain.now() + 100)
    assert active("Organization:clinic-b", "health").id == lic
    later(101)
    assert active("Organization:clinic-b", "health") is None
    assert not ok("license_verify", fake_chain.ANONYMOUS, license=lic)["valid"]
    renewal = ok("license_renew", "dr-ana", license=lic)["license"]
    ok("license_decide", "hector", license=renewal, approve=True)
    assert active("Organization:clinic-b", "health").id == renewal and License[lic].status == "expired"
