import hashlib

import auth
import chain
import delegation
import orgs
from db import Fail, each, find, next_id, page, view
from ggg import Appointment, Department, Human, Identity, Invite, Member, Position, Realm, User, UserProfile
import codex
from op import op
from proposals import root

MOVES = {"active": {"inactive", "suspended", "banned"}, "inactive": {"active", "banned"},
         "suspended": {"active", "banned"}, "banned": set()}


def within(c, perms):
    """I-ID-3: nobody hands out a permission they do not hold. A codex or an accepted proposal acts for the realm."""
    if c.origin in ("codex", "replay"):
        return
    held = auth.permissions(c.user)
    missing = [] if "all" in held else sorted(set(perms) - held)
    if missing:
        raise Fail(f"you do not hold {', '.join(missing)}")


def powers(department):
    perms = {p.id for p in department.permissions}
    for pos in department.positions:
        perms |= set(pos.profile.allowed_to or []) if pos.profile else set()
    return perms


def in_charge(perm):
    """Governed by the department named in ``args`` (it must handle ``perm``), else by the root."""
    def governing(args):
        if not args.get("department"):
            return root()
        d = find(Department, args["department"])
        if perm not in powers(d):
            raise Fail(f"{d.id} does not handle {perm}")
        return d
    return governing


def subject(args):
    return {args["user"]}


def card(user):
    m = Member[user.id]
    return {**view(user, "id", "nickname", "avatar", "home_quarter"), "status": m.status if m else "",
            "profiles": [p.id for p in user.profiles]}


def has(user, profile):
    return profile.id in {p.id for p in user.profiles}


def give(user, profile):
    if not has(user, profile):
        user.profiles = list(user.profiles) + [profile]


def enroll(principal, nickname=""):
    user = User[principal] or User(id=principal, nickname=nickname)
    Member[principal] or Member(id=principal, status="active", joined_at=chain.now())
    give(user, find(UserProfile, "member"))
    return user


def digest(code):
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def redeemable(code, principal):
    inv = Invite[digest(code)]
    if not inv or inv.revoked or inv.uses >= inv.max_uses or (inv.expires_at and chain.now() >= inv.expires_at):
        raise Fail("this invite code is not valid")
    if principal in inv.redeemed_by:
        raise Fail("you already used this invite")
    return inv


@op("self.join")
def realm_join(c, args):
    member, realm = Member[c.user], Realm["1"]
    inv = redeemable(args["code"], c.user) if args.get("code") else None
    if member and member.status != "active":
        raise Fail(f"your membership is {member.status}")
    if member and inv is None:
        raise Fail("you are already a member")
    if inv is None and not (realm and realm.open_registration) and not chain.is_controller(c.user):
        raise Fail("this realm needs an invite code to join")
    if inv and inv.position:
        orgs.fill(find(Position, inv.position), c.user)
    user = enroll(c.user, args.get("nickname", ""))
    if inv:
        give(user, find(UserProfile, inv.profile))
        inv.uses, inv.redeemed_by = inv.uses + 1, inv.redeemed_by + [c.user]
    yield from codex.hook("on_user_register", {"user": c.user})
    return {"user": c.user, "status": Member[c.user].status, "profiles": [p.id for p in user.profiles]}


@op("user.view", read=True)
def user_get(c, args):
    return {"user": card(find(User, args["user"]))}


@op("user.view", read=True)
def user_list(c, args):
    rows, nxt = page(User, args)
    return {"users": [card(u) for u in rows], "next": nxt}


@op("self.data_view", read=True, delegable=True)
def me(c, args):
    user = User[c.user]
    out = {"user": card(user) if user else {"id": c.user}, "permissions": sorted(auth.permissions(c.user)),
           "delegations": delegation.mine(c.user)}
    if user and not c.delegated:
        out["private_data"] = user.private_data
    return out


@op("self.update_public_profile", delegable=True)
def profile_update(c, args):
    user = find(User, c.user)
    user.nickname, user.avatar = args.get("nickname", user.nickname), args.get("avatar", user.avatar)


@op("self.update_private_data")
def private_data_update(c, args):
    find(User, c.user).private_data = args["private_data"]


@op("user.add", governed=in_charge("user.add"))
def user_add(c, args):
    if User[args["user"]]:
        raise Fail(f"{args['user']} is already a user")
    enroll(args["user"], args.get("nickname", ""))
    return {"user": args["user"]}


@op("user.add", governed=in_charge("user.add"))
def user_import(c, args):
    ids = [u["user"] for u in args["users"]]
    taken = sorted(i for i in set(ids) if User[i]) or sorted({i for i in ids if ids.count(i) > 1})
    if taken:
        raise Fail(f"already known or repeated: {', '.join(taken)}")
    for u in args["users"]:
        enroll(u["user"], u.get("nickname", ""))
    return {"added": len(ids)}


@op("user.edit")
def user_edit(c, args):
    user = find(User, args["user"])
    for field in ("nickname", "avatar", "home_quarter"):
        if field in args:
            setattr(user, field, args[field])


@op("user.update_status", governed=in_charge("user.update_status"), parties=subject)
def user_set_status(c, args):
    m = find(Member, args["user"])
    if args["status"] not in MOVES[m.status]:
        raise Fail(f"a {m.status} member cannot become {args['status']}")
    m.status = args["status"]
    return {"status": m.status}


@op("user.delete", governed=in_charge("user.delete"), parties=subject)
def user_remove(c, args):
    user = find(User, args["user"])
    if any(a.status == "active" for a in each(Appointment, "user", user.id)):
        raise Fail(f"{user.id} still holds appointments; end them first")
    if Member[user.id]:
        Member[user.id].delete()
    user.delete()


@op("identity.submit", delegable=True)
def identity_submit(c, args):
    human = next(each(Human, "user", c.user), None) or Human(id=next_id(Human, "H"), user=c.user)
    i = Identity(id=next_id(Identity, "ID"), human=human, kind=args["kind"], issuer=args.get("issuer", ""),
                 ref=args.get("ref", ""))
    return {"identity": i.id}


def identity_owner(args):
    return {find(Identity, args["identity"]).human.user}


@op("identity.review", signing=True, parties=identity_owner)
def identity_review(c, args):
    i = find(Identity, args["identity"])
    if args["status"] not in ("verified", "rejected") or i.status != "pending":
        raise Fail(f"{i.id} is {i.status}; it can only go from pending to verified or rejected")
    i.status, i.verified_at = args["status"], chain.now() if args["status"] == "verified" else 0
    return {"status": i.status}


def fresh_code(c):
    h = hashlib.sha256(f"{chain.self_id()}:{chain.now()}:{Invite.max_id()}:{c.by}".encode()).hexdigest().upper()
    return f"{h[:4]}-{h[4:8]}-{h[8:12]}"


@op("invite.manage")
def invite_create(c, args):
    profile = find(UserProfile, args.get("profile") or "member")
    pos = find(Position, args["position"]) if args.get("position") else None
    department = args.get("department") or (pos.department.id if pos and pos.department else "")
    if pos and (pos.department is None or pos.department.id != department):
        raise Fail(f"{pos.id} is not a seat of {department or 'a department'}")
    within(c, (profile.allowed_to or []) + (pos.profile.allowed_to if pos and pos.profile else []))
    code = (args.get("code") or fresh_code(c)).strip().upper()
    if Invite[digest(code)]:
        raise Fail("this code is taken")
    lasts = int(args.get("expires_in") or 0)
    expires = int(args.get("expires_at") or 0) or (chain.now() + lasts if lasts else 0)
    Invite(id=digest(code), profile=profile.id, department=department, position=pos.id if pos else "",
           max_uses=int(args.get("max_uses", 1)), expires_at=expires, created_by=c.user)
    # Shown once, nested so the audit log never keeps it.
    return {"invite": {"id": digest(code), "code": code}}


@op("invite.manage")
def invite_revoke(c, args):
    inv = find(Invite, args["invite"])
    if inv.revoked:
        raise Fail("this invite is already revoked")
    inv.revoked = True


@op("invite.manage")
def invite_list(c, args):
    return {"invites": [view(i, "id", "profile", "department", "position", "max_uses", "uses", "expires_at",
                             "revoked", "created_by") for i in each(Invite)]}


@op("permission.grant", governed=in_charge("permission.grant"))
def profile_create(c, args):
    if UserProfile[args["profile"]]:
        raise Fail(f"profile {args['profile']} exists")
    within(c, args.get("allowed_to", []))
    UserProfile(id=args["profile"], description=args.get("description", ""), allowed_to=args.get("allowed_to", []))
    return {"profile": args["profile"]}


@op("permission.grant", governed=in_charge("permission.grant"))
def profile_edit(c, args):
    p = find(UserProfile, args["profile"])
    within(c, args.get("allowed_to", []))
    p.description, p.allowed_to = args.get("description", p.description), args.get("allowed_to", p.allowed_to)


@op("role.assign", governed=in_charge("role.assign"), parties=subject)
def role_assign(c, args):
    user, profile = find(User, args["user"]), find(UserProfile, args["profile"])
    if has(user, profile):
        raise Fail(f"{user.id} already has {profile.id}")
    within(c, profile.allowed_to or [])
    give(user, profile)


@op("role.revoke", governed=in_charge("role.revoke"), parties=subject)
def role_revoke(c, args):
    user, profile = find(User, args["user"]), find(UserProfile, args["profile"])
    if not has(user, profile):
        raise Fail(f"{user.id} does not have {profile.id}")
    user.profiles = [p for p in user.profiles if p.id != profile.id]
