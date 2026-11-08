from datetime import datetime, timezone

import pytest

import fake_chain
import world
from core import localtime
from ggg import Delegation, License, Organization, Procedure, Proposal
from kit import call, denied, direct, later, ok
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


def test_license_list_pages():
    agora()
    mine = [ok("license_apply", "alice", kind=kind)["license"] for kind in ("professional", "education", "business")]
    apply_clinic()
    pages, cursor = [], 1
    while cursor:
        out = ok("license_list", fake_chain.ANONYMOUS, holder="User:alice", count=2, **{"from": cursor})
        pages.append([x["id"] for x in out["licenses"]])
        cursor = out["next"]
    assert pages == [mine[:2], mine[2:]]
    out = ok("license_list", fake_chain.ANONYMOUS, count=3, kind="health")
    assert out["licenses"] == [] and out["next"] == 4
    assert [x["id"] for x in ok("license_list", fake_chain.ANONYMOUS, kind="health", **{"from": 4})["licenses"]] == [
        "LIC-4"]


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


HEALTH = {"kind": "health", "initiated_by": "the applicant", "matter": "licence",
          "decision_deadline": {"count": 3, "unit": "month"}, "silence": "positive", "legislation": "Licensing Act 12"}


def test_story_the_realm_defines_how_health_licences_are_decided():
    depts = agora()
    world.seat("health.drafter", "carla", depts["Infrastructure"], "procedure.define")
    assert "procedure.define" in denied("procedure_define", "alice", **HEALTH)
    assert ok("procedure_define", "carla", **HEALTH)["procedure"] == "health"
    p = Procedure["health"]
    assert (p.initiated_by, p.matter, p.decision_deadline, p.silence, p.legislation, p.facts) == (
        "the applicant", "licence", {"count": 3, "unit": "month"}, "positive", "Licensing Act 12", {})
    ok("procedure_define", "carla", kind="health", initiated_by="the department", facts={"fee": 0})
    p = Procedure["health"]
    assert (p.initiated_by, p.matter, p.decision_deadline, p.silence, p.facts) == ("the department", "", None, "", {"fee": 0})
    ok("procedure_define", "carla", kind="business", initiated_by="the applicant", silence="negative",
       decision_deadline={"count": 30, "unit": "working_day"})
    listed = ok("procedure_list", "alice")
    assert [x["id"] for x in listed["procedures"]] == ["health", "business"] and listed["next"] is None
    assert [x["id"] for x in ok("procedure_list", "alice", count=1)["procedures"]] == ["health"]
    assert "sign in" in denied("procedure_list", fake_chain.ANONYMOUS)


def test_a_procedure_with_no_period_or_a_bad_silence_is_refused_before_anything_is_written():
    """Candid cannot say most of these, so the arguments go to the operation as JSON."""
    agora()
    for bad in ({"decision_deadline": {"count": 3, "unit": "fortnight"}}, {"decision_deadline": {"count": -1, "unit": "day"}},
                {"decision_deadline": {"count": "3", "unit": "day"}}, {"decision_deadline": {"unit": "day"}},
                {"decision_deadline": "soon"}, {"decision_deadline": {"count": 3, "unit": ["day"]}},
                {"decision_deadline": {"count": True, "unit": "day"}}, {"silence": "tacit"}):
        out = direct("procedure_define", "ctl", **{**HEALTH, **bad})
        assert not out["ok"] and out["kind"] == "invalid_argument", bad
    assert Procedure["health"] is None


def test_defining_a_procedure_is_governed_by_the_root_department():
    depts = agora()
    world.policy(depts["Infrastructure"].parent, 2, 3)
    out = ok("procedure_define", "ctl", **HEALTH)
    assert out["requires_vote"] and Procedure["health"] is None
    assert Proposal[out["proposal"]].action["op"] == "procedure_define"


@pytest.fixture
def utc():
    """The reader keeps its zone in the module, which outlives the test that set it: a canister starts in UTC."""
    localtime.use("UTC")
    yield
    localtime.use("UTC")


def at(*when):
    """The second of a UTC instant, from (year, month, day, hour, minute)."""
    return int(datetime(*when, tzinfo=timezone.utc).timestamp())


def define_deadline(count, unit):
    ok("procedure_define", "ctl", kind="health", initiated_by="the applicant", silence="positive",
       decision_deadline={"count": count, "unit": unit})


def applied_at(*when):
    fake_chain.state["now"] = at(*when)
    return apply_clinic()


def test_a_decision_is_due_at_the_end_of_the_last_day_of_the_deadline(utc):
    """One day from the 21st is the 22nd, and the 22nd counts in full: positive silence applies from the 23rd, never
    during the 22nd."""
    agora()
    define_deadline(1, "day")
    lic = applied_at(2026, 9, 21, 14, 13)
    assert License[lic].decision_due == at(2026, 9, 23)
    assert License[lic].decision_due > at(2026, 9, 22, 23, 59)
    assert ok("license_list", fake_chain.ANONYMOUS, holder="Organization:clinic-b")["licenses"][0]["decision_due"] \
        == at(2026, 9, 23)


def test_a_deadline_in_months_ends_with_the_last_day_of_the_month_it_reaches(utc):
    agora()
    define_deadline(3, "month")
    assert License[applied_at(2026, 9, 21, 14, 13)].decision_due == at(2026, 12, 22)
    assert License[applied_at(2026, 11, 30, 9, 0)].decision_due == at(2027, 3, 1)  # February has no 30th: its 28th
    define_deadline(1, "year")
    assert License[applied_at(2028, 2, 29, 9, 0)].decision_due == at(2029, 3, 1)


def test_the_day_is_the_realms_own_not_utc(utc):
    """22:30 UTC on 1 July is already the 2nd in Madrid, where a day is over at 22:00 UTC."""
    agora()
    ok("realm_configure", "ctl", timezone="Europe/Madrid")
    define_deadline(1, "day")
    assert License[applied_at(2026, 7, 1, 22, 30)].decision_due == at(2026, 7, 3, 22)


def test_working_days_fail_closed_while_the_realm_has_no_calendar(utc):
    agora()
    define_deadline(15, "working_day")
    out = call("license_apply", "dr-ana", kind="health", holder="Organization:clinic-b", department="Health")
    assert not out["ok"] and out["kind"] == "unavailable" and "2026" in out["error"]
    assert License.max_id() == 0


def test_a_licence_without_a_procedure_or_a_deadline_has_no_due_date(utc):
    agora()
    assert License[apply_clinic()].decision_due == 0
    ok("procedure_define", "ctl", kind="health", initiated_by="the applicant")
    assert License[apply_clinic()].decision_due == 0
    assert ok("license_list", fake_chain.ANONYMOUS, holder="Organization:clinic-b")["licenses"][0]["decision_due"] == 0


def test_a_renewal_is_dated_by_the_procedure_in_force_and_an_earlier_licence_keeps_its_date(utc):
    agora()
    define_deadline(10, "day")
    first = applied_at(2026, 9, 21, 14, 13)
    ok("license_decide", "hector", license=first, approve=True)
    define_deadline(20, "day")
    renewal = ok("license_renew", "dr-ana", license=first)["license"]
    assert License[first].decision_due == at(2026, 10, 2)
    assert License[renewal].decision_due == at(2026, 10, 12)
