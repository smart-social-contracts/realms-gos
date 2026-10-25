import auth
import fake_chain
import world
from ggg import Appointment, Authority, Department, Organization, Position, Proposal
from kit import denied, later, ok

DAY = 86400


def pass_proposal(p, *voters):
    for v in voters:
        ok("proposal_vote", v, proposal=p, choice="yes")
    later(8 * DAY)
    ok("proposal_finalize", "ctl", proposal=p)
    assert ok("proposal_execute", "ctl", proposal=p)["status"] == "executed"


def org_admin(principal):
    world.profile("org-admin", "organization.add", "organization.edit")
    world.person(principal, "org-admin")


def test_story_agora_departments_and_clinics():
    depts = world.build()
    world.policy(Department["Administration"], 2, 3)
    p = ok("department_create", "ctl", department="Roads", description="Roads and bridges")["proposal"]
    assert Department["Roads"] is None
    pass_proposal(p, "alice", "bob")
    world.policy(Department["Administration"], 1, 1)
    ok("department_edit", "ctl", department="Roads", parent="Infrastructure")
    assert Department["Roads"].parent.id == "Infrastructure"
    ok("position_create", "ctl", department="Infrastructure", position="infrastructure.inspector", title="Inspector",
       headcount=2, salary=2400)
    ok("appoint", "ctl", position="infrastructure.inspector", user="alice")
    ok("department_set_policy", "ctl", department="Infrastructure", m=2, n=3, quorum_percent=50)
    p = ok("appoint", "ctl", position="infrastructure.inspector", user="bob")["proposal"]
    pass_proposal(p, "carla", "alice")
    assert len([a for a in Position["infrastructure.inspector"].appointments if a.status == "active"]) == 2
    ok("position_edit", "ctl", position="civil-registry.head", headcount=2)
    ok("appoint_acting", "ctl", position="civil-registry.head", user="bob")
    ok("department_edit", "ctl", department="Civil Registry", head="dana")
    chart = ok("department_chart", "alice", department="Civil Registry")["chart"]
    assert (len(chart["positions"]), chart["seats"], chart["filled"]) == (2, 3, 3)
    assert ok("department_chart", "alice")["chart"]["children"]
    assert ok("department_get", "alice", department="Infrastructure")["department"]["policy"]["m"] == 2
    assert len(ok("department_list", "alice")["departments"]) == 7
    world.grant(depts["Finance"], "transfer.create")
    a = ok("authority_grant", "ctl", department="Finance", target="Department:Infrastructure",
           operations=["transfer.create"])["authority"]
    ok("authority_revoke", "ctl", authority=a)
    assert Authority.count() == 0
    org_admin("dr-ana")
    ok("organization_register", "dr-ana", organization="clinic-n", description="Clinic North")
    ok("representative_add", "dr-ana", organization="clinic-n", position="clinic-n.physician", user="dr-ana")
    rep = ok("representative_add", "dr-ana", organization="clinic-n", position="clinic-n.records_agent",
             user="records-bot-n", agent=True, supervisor="dr-ana")["appointment"]
    ok("organization_edit", "dr-ana", organization="clinic-n", admins=["dr-ana", "alice"])
    org_admin("alice")
    ok("representative_remove", "alice", organization="clinic-n", appointment=rep)
    ok("organization_set_status", "ctl", organization="buildco", status="suspended")
    assert ok("organization_list", "alice", status="suspended")["organizations"][0]["id"] == "buildco"
    assert ok("organization_get", "alice", organization="clinic-b")["positions"][1]["holders"][0]["agent"]


def test_I_ORG_1_department_permissions_only_through_an_active_seat():
    world.build()
    assert "bid.evaluate" in auth.permissions("carla")
    ok("appointment_end", "ctl", appointment="infrastructure.planning_officer:carla")
    assert "bid.evaluate" not in auth.permissions("carla")
    assert "not active" in denied("appointment_end", "ctl", appointment="infrastructure.planning_officer:carla")


def test_I_ORG_2_headcount():
    world.build()
    assert "no free seat" in denied("appoint", "ctl", position="civil-registry.clerk", user="alice")
    assert "below" in denied("position_edit", "ctl", position="civil-registry.clerk", headcount=0)
    ok("position_edit", "ctl", position="civil-registry.clerk", headcount=2)
    ok("appoint", "ctl", position="civil-registry.clerk", user="alice")
    assert "already holds" in denied("appoint_acting", "ctl", position="civil-registry.clerk", user="alice")


def test_I_ORG_3_agents_have_a_seated_human_supervisor():
    world.build()
    org_admin("dr-ana")
    add = dict(organization="clinic-b", position="clinic-b.scheduler", user="sched-bot", agent=True)
    assert "supervisor" in denied("representative_add", "dr-ana", **add)
    assert "supervisor" in denied("representative_add", "dr-ana", supervisor="bob", **add)
    assert Position["clinic-b.scheduler"] is None
    assert "supervises records-bot" in denied("representative_remove", "dr-ana", organization="clinic-b",
                                              appointment="clinic-b.physician:dr-ana")
    ok("position_edit", "ctl", position="civil-registry.clerk", headcount=2)
    assert "supervisor" in denied("appoint", "ctl", position="civil-registry.clerk", user="bot", agent=True,
                                  supervisor="alice")


def test_I_ORG_4_agent_never_signs():
    world.build()
    org_admin("dr-ana")
    i = ok("identity_submit", "alice", kind="passport")["identity"]
    world.seat("civil-registry.reviewer-bot", "review-bot", Department["Civil Registry"], "identity.review",
               agent=True, supervisor="dana")
    assert "human" in denied("identity_review", "review-bot", identity=i, status="verified")


def test_I_ORG_5_policy_bounds_and_veto_holders():
    world.build()
    assert "1 <= m <= n" in denied("department_set_policy", "ctl", department="Civil Registry", m=3, n=2)
    assert "1 <= m <= n" in denied("department_set_policy", "ctl", department="Civil Registry", m=0, n=2)
    assert "veto" in denied("department_set_policy", "ctl", department="Civil Registry", m=2, n=3, veto=["alice"])
    ok("department_set_policy", "ctl", department="Civil Registry", m=2, n=3, veto=["dana"])


def test_I_ORG_6_root_cannot_be_deleted():
    world.build()
    assert "root" in denied("department_delete", "ctl", department="Administration")
    assert "open positions" in denied("department_delete", "ctl", department="Justice")
    ok("department_create", "ctl", department="Roads", parent="Infrastructure")
    ok("department_delete", "ctl", department="Roads")
    assert Department["Roads"] is None


def test_I_ORG_7_seats_change_under_the_rules_and_reps_under_the_admins():
    world.build()
    world.policy(Department["Civil Registry"], 2, 3)
    out = ok("position_create", "ctl", department="Civil Registry", position="civil-registry.archivist")
    assert out["requires_vote"] and Proposal[out["proposal"]].department.id == "Civil Registry"
    assert Position["civil-registry.archivist"] is None
    org_admin("bob")
    assert "admins" in denied("representative_add", "bob", organization="clinic-b", position="clinic-b.physician",
                              user="bob")
    assert "admins" in denied("organization_edit", "bob", organization="clinic-b", description="mine")
    assert "organization seat" in denied("appoint", "ctl", position="clinic-b.physician", user="alice")


def test_I_ORG_8_nobody_acts_for_a_suspended_or_dissolved_organization():
    world.build()
    org_admin("dr-ana")
    ok("organization_set_status", "ctl", organization="clinic-b", status="suspended")
    assert "suspended" in denied("organization_edit", "dr-ana", organization="clinic-b", description="x")
    assert "suspended" in denied("representative_add", "dr-ana", organization="clinic-b",
                                 position="clinic-b.physician", user="alice")
    ok("organization_set_status", "ctl", organization="clinic-b", status="dissolved")
    assert all(a.status == "ended" for p in Organization["clinic-b"].positions for a in p.appointments)
    assert "cannot become" in denied("organization_set_status", "ctl", organization="clinic-b", status="active")


def test_positions_and_expulsion():
    world.build()
    ok("position_edit", "ctl", position="civil-registry.clerk", status="frozen", salary=2000)
    assert "frozen" in denied("appoint", "ctl", position="civil-registry.clerk", user="alice")
    assert "holders" in denied("position_close", "ctl", position="civil-registry.clerk")
    ok("department_expel", "ctl", department="Civil Registry", user="bob")
    assert "party" in denied("department_expel", "dana", department="Civil Registry", user="dana")
    ok("position_close", "ctl", position="civil-registry.clerk")
    assert "use position_close" in denied("position_edit", "ctl", position="civil-registry.clerk", status="open")
    assert "holds no seat" in denied("department_expel", "ctl", department="Civil Registry", user="bob")
    assert "cannot pass on" in denied("authority_grant", "ctl", department="Finance",
                                      target="Department:Infrastructure", operations=["transfer.create"])
    assert Appointment["civil-registry.clerk:bob"].status == "ended"


def test_auth():
    world.build()
    world.person("mallory")
    for name, args in [("department_create", {"department": "X"}), ("department_edit", {"department": "Health"}),
                       ("department_delete", {"department": "Health"}),
                       ("department_set_policy", {"department": "Health", "m": 1, "n": 1}),
                       ("position_create", {"department": "Health", "position": "x"}),
                       ("position_edit", {"position": "civil-registry.clerk"}),
                       ("position_close", {"position": "civil-registry.clerk"}),
                       ("appoint", {"position": "civil-registry.clerk", "user": "alice"}),
                       ("appoint_acting", {"position": "civil-registry.clerk", "user": "alice"}),
                       ("appointment_end", {"appointment": "civil-registry.clerk:bob"}),
                       ("department_expel", {"department": "Health", "user": "bob"}),
                       ("authority_grant", {"department": "Health", "target": "Department:Finance", "operations": []}),
                       ("authority_revoke", {"authority": "x"}),
                       ("organization_register", {"organization": "x"}),
                       ("organization_edit", {"organization": "clinic-b"}),
                       ("representative_add", {"organization": "clinic-b"}),
                       ("representative_remove", {"organization": "clinic-b"}),
                       ("organization_set_status", {"organization": "buildco", "status": "suspended"})]:
        assert "needs" in denied(name, "mallory", **args), name
    for name in ("department_get", "department_list", "department_chart", "organization_get", "organization_list"):
        assert "sign in" in denied(name, fake_chain.ANONYMOUS, department="Health", organization="clinic-b")
        ok(name, "mallory", department="Health", organization="clinic-b")
