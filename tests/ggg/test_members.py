import hashlib

from core import auth
import fake_chain
import world
from ggg import Appointment, Delegation, Event, Identity, Invite, Member, Position, Proposal, Realm, User, UserProfile
from kit import denied, later, ok

DAY = 86400


def code_of(out):
    return out["invite"]["code"]


def pass_proposal(p, *voters):
    for v in voters:
        ok("proposal_vote", v, proposal=p, choice="yes")
    later(8 * DAY)
    ok("proposal_finalize", "ctl", proposal=p)
    return ok("proposal_execute", "ctl", proposal=p)


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
