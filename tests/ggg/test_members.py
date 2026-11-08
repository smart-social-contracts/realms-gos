import hashlib

from core import auth
import fake_chain
import world
from core.db import each
from ggg import (Appointment, Attribute, Delegation, Department, Event, Human, Identity, Invite, Member, Position,
                 Proposal, Realm, User, UserProfile, Zone)
from kit import call, denied, later, ok

DAY = 86400


def code_of(out):
    return out["invite"]["code"]


def pass_proposal(p, *voters):
    """The deciding vote settles a department's proposal and runs its action; a timed realm-wide one waits for its
    deadline, and then ``proposal_finalize`` closes it and runs it."""
    for v in voters:
        ok("proposal_vote", v, proposal=p, choice="yes")
    if Proposal[p].status == "voting":
        later(8 * DAY)
        ok("proposal_finalize", "ctl", proposal=p)
    assert Proposal[p].status == "executed", Proposal[p].result


def test_story_agora_registry():
    depts = world.build()
    ok("position_edit", "ctl", position="civil-registry.clerk", headcount=51)
    inv = ok("invite_create", "dana", profile="member", department="Civil Registry", position="civil-registry.clerk",
             max_uses=50, expires_in=30 * DAY)
    assert "code" not in str(Event[str(Event.max_id())].result)
    out = ok("realm_join", "newbie", code=code_of(inv), nickname="Newbie")
    assert (out["user"], out["status"], out["profiles"]) == ("newbie", "active", ["member"])
    assert any(a.user == "newbie" for a in Position["civil-registry.clerk"].appointments)
    assert ok("user_get", "dana", user="newbie")["user"]["nickname"] == "Newbie"
    ok("user_edit", "dana", user="newbie", nickname="Newbie Clerk")
    assert ok("user_list", "dana")["users"]
    ok("delegation_grant", "alice", delegate="claude-conn", operations=["self.update_public_profile"], expires_in=DAY)
    ok("profile_update", "claude-conn", avatar="https://agora/alice.png", on_behalf_of="alice")
    ok("private_data_update", "alice", private_data="vetkeys:ciphertext")
    assert User["alice"].avatar == "https://agora/alice.png"
    i = ok("identity_submit", "alice", kind="passport", issuer="zkpassport", ref="vault:alice-passport")["identity"]
    ok("identity_review", "dana", identity=i, status="verified")
    assert Identity[i].status == "verified" and Identity[i].verified_at
    world.policy(depts["Civil Registry"], 2, 3)
    p = ok("user_set_status", "dana", user="mallory", status="suspended", department="Civil Registry")["proposal"]
    assert Proposal[p].department.id == "Civil Registry" and Member["mallory"].status == "active"
    pass_proposal(p, "dana", "bob")
    assert Member["mallory"].status == "suspended"
    ok("profile_create", "ctl", profile="treasurer", allowed_to=["treasury.view", "transfer.create"])
    ok("role_assign", "ctl", user="bob", profile="treasurer")
    assert "transfer.create" in auth.permissions("bob")
    me = ok("me", "alice")
    assert me["private_data"] == "vetkeys:ciphertext"
    assert me["delegations"]["as_grantor"][0]["delegate"] == "claude-conn"


def test_join_needs_invite_unless_open():
    world.build()
    assert "invite" in denied("realm_join", "zoe")
    Realm["1"].open_registration = True
    ok("realm_join", "zoe")
    assert "already a member" in denied("realm_join", "zoe")
    assert "sign in" in denied("realm_join", fake_chain.ANONYMOUS)


def test_I_ID_1_nobody_acts_as_another_without_a_grant():
    world.build()
    assert "power of attorney" in denied("profile_update", "mallory", nickname="x", on_behalf_of="alice")
    ok("profile_update", "mallory", nickname="Mal")
    assert (User["alice"].nickname, User["mallory"].nickname) == ("Alice", "Mal")
    assert ok("me", "alice")["user"]["id"] == "alice"


def test_I_ID_2_invite_limits():
    world.build()
    one = code_of(ok("invite_create", "dana", max_uses=1, expires_in=DAY))
    ok("realm_join", "zoe", code=one)
    assert User["zoe"].profiles[0].id == "member" and len(User["zoe"].profiles) == 1
    assert "not valid" in denied("realm_join", "yan", code=one)
    late = code_of(ok("invite_create", "dana", max_uses=5, expires_in=DAY))
    later(DAY + 1)
    assert "not valid" in denied("realm_join", "yan", code=late)
    gone = ok("invite_create", "dana", max_uses=5)["invite"]
    ok("invite_revoke", "dana", invite=gone["id"])
    assert "not valid" in denied("realm_join", "yan", code=gone["code"])
    assert Invite[hashlib.sha256(one.encode()).hexdigest()].uses == 1
    full = code_of(ok("invite_create", "dana", position="civil-registry.clerk", max_uses=3))
    assert "no free seat" in denied("realm_join", "yan", code=full)
    assert User["yan"] is None


def test_I_ID_3_nobody_hands_out_what_they_lack():
    world.build()
    world.profile("registrar", "role.assign", "permission.grant", "invite.manage")
    world.person("bob", "registrar")
    ok("profile_create", "ctl", profile="treasurer", allowed_to=["treasury.view", "transfer.create"])
    assert "do not hold" in denied("role_assign", "bob", user="alice", profile="treasurer")
    assert "do not hold" in denied("profile_create", "bob", profile="boss", allowed_to=["all"])
    assert "do not hold" in denied("invite_create", "bob", profile="treasurer")
    ok("role_assign", "bob", user="alice", profile="registrar")
    assert "party" in denied("role_assign", "bob", user="bob", profile="member")


def test_I_ID_4_banned_member_only_reads():
    world.build()
    ok("user_set_status", "ctl", user="mallory", status="banned")
    denied("profile_update", "mallory", nickname="spam")
    denied("realm_join", "mallory")
    ok("department_list", "mallory")
    ok("status", "mallory")
    assert "cannot become" in denied("user_set_status", "ctl", user="mallory", status="active")


def test_I_ID_5_private_data_only_for_its_owner():
    world.build()
    ok("private_data_update", "alice", private_data="vetkeys:secret")
    assert "private_data" not in ok("user_get", "dana", user="alice")["user"]
    ok("delegation_grant", "alice", delegate="claude-conn", operations=["self.data_view"], expires_in=DAY)
    assert "private_data" not in ok("me", "claude-conn", on_behalf_of="alice")
    assert ok("me", "alice")["private_data"] == "vetkeys:secret"
    assert "private_data" not in Event[str(Event.max_id())].args


def test_user_admin_ops_and_auth():
    world.build()
    i = ok("identity_submit", "bob", kind="email")["identity"]
    for name, args in [("user_add", {"user": "zoe"}), ("user_import", {"users": [{"user": "zoe"}]}),
                       ("user_edit", {"user": "bob"}), ("user_set_status", {"user": "bob", "status": "inactive"}),
                       ("user_remove", {"user": "bob"}), ("user_get", {"user": "bob"}), ("user_list", {}),
                       ("identity_review", {"identity": i, "status": "verified"}),
                       ("invite_create", {}), ("invite_revoke", {"invite": "x"}), ("invite_list", {}),
                       ("profile_create", {"profile": "x"}), ("profile_edit", {"profile": "member"}),
                       ("role_assign", {"user": "bob", "profile": "admin"}),
                       ("role_revoke", {"user": "bob", "profile": "member"})]:
        assert "needs" in denied(name, "alice", **args), name
    for name in ("me", "profile_update", "private_data_update", "identity_submit"):
        assert "sign in" in denied(name, fake_chain.ANONYMOUS)
    ok("user_add", "dana", user="zoe", nickname="Zoe")
    assert "already" in denied("user_import", "dana", users=[{"user": "yan"}, {"user": "zoe"}])
    assert ok("user_import", "dana", users=[{"user": "yan"}, {"user": "xia"}])["added"] == 2
    assert Member["xia"].status == "active"
    ok("user_set_status", "dana", user="zoe", status="inactive")
    assert "holds appointments" in denied("user_remove", "ctl", user="bob")
    ok("user_remove", "ctl", user="zoe")
    assert User["zoe"] is None and Member["zoe"] is None
    assert len(ok("invite_list", "dana")["invites"]) == 0
    ok("profile_edit", "ctl", profile="member", description="Every member")
    ok("role_revoke", "ctl", user="bob", profile="member")
    assert "does not have" in denied("role_revoke", "ctl", user="bob", profile="member")
    assert "does not handle" in denied("user_add", "ctl", user="q", department="Justice")


def test_invites_are_listed_a_page_at_a_time():
    world.build()
    made = [ok("invite_create", "dana", code=f"CODE-{i}")["invite"]["id"] for i in range(3)]
    first = ok("invite_list", "bob", count=2)
    rest = ok("invite_list", "bob", count=2, **{"from": first["next"]})
    assert [i["id"] for i in first["invites"] + rest["invites"]] == made and rest["next"] is None


def test_identity_review_is_signed_by_a_human_not_the_owner():
    world.build()
    world.person("dana")
    i = ok("identity_submit", "dana", kind="email", ref="vault:dana-email")["identity"]
    assert "party" in denied("identity_review", "dana", identity=i, status="verified")
    ok("identity_review", "ctl", identity=i, status="rejected")
    assert "pending" in denied("identity_review", "ctl", identity=i, status="verified")
    Appointment(id="bot", user="registry-bot", agent=True, position=Position["civil-registry.head"])
    assert "human" in denied("identity_review", "registry-bot", identity=i, status="verified")
    assert UserProfile["member"] is not None and Delegation.count() == 0


def define_districts():
    ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
    ok("zone_define", "ctl", zone="hillside", name="Hillside", kind="district")


def test_a_member_states_language_and_home_zone_when_joining():
    world.build()
    define_districts()
    Realm["1"].open_registration = True
    ok("realm_join", "zoe", language="es-ES", home_zone="riverside")
    assert (User["zoe"].language, User["zoe"].home_zone) == ("es-ES", "riverside")
    assert Zone["riverside"].population == 1
    card = ok("user_get", "dana", user="zoe")["user"]
    assert (card["language"], card["home_zone"]) == ("es-ES", "riverside")


def test_joining_with_a_bad_language_or_zone_is_refused_before_anything_is_written():
    world.build()
    define_districts()
    ok("zone_set", "alice", zone="871f1d489ffffff")
    Realm["1"].open_registration = True
    for args in ({"language": "english please"}, {"language": ""}, {"home_zone": "nowhere"},
                 {"home_zone": "871f1d489ffffff"}):
        out = call("realm_join", "zoe", **args)
        assert not out["ok"] and out["kind"] == "invalid_argument", (args, out)
    assert User["zoe"] is None and Member["zoe"] is None
    assert Zone["riverside"].population == 0


def test_a_member_changes_their_own_language_and_a_clerk_moves_their_home_zone():
    world.build()
    define_districts()
    Realm["1"].open_registration = True
    ok("realm_join", "zoe", home_zone="riverside")
    ok("profile_update", "zoe", language="ca")
    assert User["zoe"].language == "ca"
    assert call("profile_update", "zoe", language="x")["kind"] == "invalid_argument"
    ok("user_edit", "dana", user="zoe", home_zone="hillside", language="es")
    assert (User["zoe"].home_zone, User["zoe"].language) == ("hillside", "es")
    assert (Zone["riverside"].population, Zone["hillside"].population) == (0, 1)
    ok("user_edit", "dana", user="zoe", home_zone="hillside")
    assert Zone["hillside"].population == 1
    assert call("user_edit", "dana", user="zoe", home_zone="nowhere")["kind"] == "invalid_argument"
    assert User["zoe"].home_zone == "hillside"


def test_a_removed_member_leaves_the_population_of_their_zone():
    world.build()
    define_districts()
    ok("zone_define", "ctl", zone="other", name="Other", kind="district")
    ok("user_add", "ctl", user="zoe")
    ok("user_edit", "ctl", user="zoe", home_zone="other")
    ok("user_remove", "ctl", user="zoe")
    assert Zone["other"].population == 0


def registry_with_reviewer():
    """Agora, with Dana able to review attributes and Alice holding the member profile as the example has her."""
    world.build()
    world.seat("civil-registry.reviewer", "hector", Department["Civil Registry"], "attribute.review")
    world.person("hector")


def test_an_identity_states_its_category_and_expiry():
    world.build()
    i = ok("identity_submit", "alice", kind="residence_permit", category="long_term", issuer="ES", ref="vault:p",
           expires_at=fake_chain.now() + 5 * DAY)["identity"]
    assert (Identity[i].category, Identity[i].expires_at) == ("long_term", fake_chain.now() + 5 * DAY)
    plain = ok("identity_submit", "alice", kind="passport", ref="vault:q")["identity"]
    assert (Identity[plain].category, Identity[plain].expires_at) == ("", 0)


def test_story_alice_submits_her_large_family_title_and_a_reviewer_verifies_it():
    registry_with_reviewer()
    a = ok("attribute_submit", "alice", kind="large_family", value="general", issuer="regional-government",
           ref="vault:alice-title", expires_at=fake_chain.now() + 365 * DAY)["attribute"]
    assert Attribute[a].status == "pending" and Attribute[a].human == next(each(Human, "user", "alice")).id
    assert call("attribute_review", "alice", attribute=a, status="verified")["denied"]
    out = ok("attribute_review", "hector", attribute=a, status="verified")
    assert out["status"] == "verified" and Attribute[a].verified_at == fake_chain.now()
    assert "pending" in denied("attribute_review", "hector", attribute=a, status="rejected")
    mine = ok("attribute_list", "alice")["attributes"]
    assert [(x["id"], x["kind"], x["value"], x["status"]) for x in mine] == [(a, "large_family", "general", "verified")]
    assert ok("attribute_list", "alice", kind="student")["attributes"] == []
    assert ok("attribute_list", "alice", kind="large_family")["attributes"] == mine


def test_a_rejected_attribute_stays_rejected():
    registry_with_reviewer()
    a = ok("attribute_submit", "alice", kind="disability", value="33", ref="vault:x")["attribute"]
    assert ok("attribute_review", "hector", attribute=a, status="rejected")["status"] == "rejected"
    assert Attribute[a].verified_at == 0
    assert "rejected" in denied("attribute_review", "hector", attribute=a, status="verified")
    assert "verified or rejected" in denied("attribute_review", "hector", attribute=a, status="pending")


def test_I_PRV_1_an_attribute_is_read_only_by_the_person_and_its_reviewers():
    registry_with_reviewer()
    a = ok("attribute_submit", "alice", kind="disability", value="33 percent", ref="vault:x")["attribute"]
    world.seat("civil-registry.monitor", "dana", Department["Civil Registry"], "realm.monitor")
    # nobody else's list shows it, and no other operation returns it
    assert ok("attribute_list", "bob")["attributes"] == []
    assert ok("attribute_list", "hector")["attributes"] == []
    assert "attribute.review" in denied("attribute_review", "bob", attribute=a, status="verified")
    assert "disability" not in str(ok("me", "alice")) and "33 percent" not in str(ok("user_get", "dana", user="alice"))
    # a reviewer reads it in the record they decide, and the audit log keeps neither the kind nor the value
    ok("attribute_review", "hector", attribute=a, status="verified")
    log = str([e["args"] for e in ok("events", "dana")["events"]]) + str([e["result"] for e in ok("events", "dana")["events"]])
    assert "disability" not in log and "33 percent" not in log
    assert {e["op"] for e in ok("events", "dana")["events"]} >= {"attribute_submit", "attribute_review"}


def test_attributes_are_submitted_in_person_and_a_delegate_under_self_data_view_does_not_list_them():
    registry_with_reviewer()
    refused = denied("delegation_grant", "alice", delegate="claude-conn", operations=["attribute.submit"], expires_in=DAY)
    assert "not a delegable" in refused
    ok("delegation_grant", "alice", delegate="claude-conn", operations=["self.data_view"], expires_in=DAY)
    assert "power of attorney" in denied("attribute_submit", "claude-conn", kind="student", value="yes", ref="x",
                                         on_behalf_of="alice")
    ok("attribute_submit", "alice", kind="student", value="yes", ref="vault:s")
    # A grant of self.data_view was consented as profile, notifications and invoices; attributes are personal data.
    assert "power of attorney" in denied("attribute_list", "claude-conn", on_behalf_of="alice")
    assert len(ok("attribute_list", "alice")["attributes"]) == 1
    assert "sign in" in denied("attribute_submit", fake_chain.ANONYMOUS, kind="x", value="y", ref="z")
    assert "sign in" in denied("attribute_list", fake_chain.ANONYMOUS)


def test_an_agent_cannot_review_attributes():
    registry_with_reviewer()
    world.seat("civil-registry.review-bot", "review-bot", Department["Civil Registry"], "attribute.review",
               agent=True, supervisor="dana")
    a = ok("attribute_submit", "alice", kind="student", value="yes", ref="vault:s")["attribute"]
    assert "human" in denied("attribute_review", "review-bot", attribute=a, status="verified")


def test_I_PRV_1_exporting_attributes_takes_the_reviewers_permission_too():
    registry_with_reviewer()
    world.seat("civil-registry.exporter", "carla", Department["Civil Registry"], "db.export")
    world.seat("civil-registry.reviewing-exporter", "elena", Department["Civil Registry"], "db.export",
               "attribute.review")
    ok("attribute_submit", "alice", kind="disability", value="33 percent", ref="vault:x")
    assert "attribute.review" in denied("realm_export", "carla", entity="Attribute")
    assert ok("realm_export", "carla", entity="Member")["rows"]
    rows = ok("realm_export", "elena", entity="Attribute")["rows"]
    assert [(r["kind"], r["value"]) for r in rows] == [("disability", "33 percent")]
    assert ok("realm_export", "ctl", entity="Attribute")["rows"] == rows
    assert "db.export" in denied("realm_export", "hector", entity="Attribute")
