"""The running example of docs/GGG.md, as test data.

Agora: departments Civil Registry, Infrastructure, Finance, Health and Justice under the root
(Administration); members Alice, Bob, Dana, Carla, Dr. Ana, Judge Elena and Mallory; external
organizations Clinic B and BuildCo; agents `claude-conn` (Alice's AI connection, no seat) and
`records-bot` (Clinic B's records agent, supervised by Dr. Ana).
"""

import fake_chain
from api import setup
from ggg import (Appointment, Department, Fund, Member, Organization, Permission, Position, Token, User,
                 UserProfile)

PEOPLE = ("alice", "bob", "dana", "carla", "dr-ana", "elena", "mallory")
DEPARTMENTS = ("Civil Registry", "Infrastructure", "Finance", "Health", "Justice")


def person(principal, *profiles):
    user = User[principal] or User(id=principal, nickname=principal.title())
    user.profiles = [UserProfile[p] for p in ("member",) + profiles]
    Member[principal] or Member(id=principal, joined_at=fake_chain.now())
    return user


def profile(name, *perms):
    return UserProfile[name] or UserProfile(id=name, allowed_to=list(perms))


def seat(key, holder, owner, *perms, agent=False, supervisor="", title=""):
    """Give ``holder`` an active appointment to the position ``key`` of ``owner`` (department or organization)."""
    pos = Position[key]
    if pos is None:
        pos = Position(id=key, title=title or key.split(".")[-1], profile=profile(key, *perms))
        if isinstance(owner, Department):
            pos.department = owner
        else:
            pos.organization = owner
    pos.headcount = max(pos.headcount, len([a for a in pos.appointments if a.status == "active"]) + 1)
    return Appointment(id=f"{key}:{holder}", position=pos, user=holder, agent=agent, supervisor=supervisor,
                       started_at=fake_chain.now())


def grant(department, *perms):
    for key in perms:
        p = Permission[key] or Permission(id=key)
        p.departments = list(p.departments) + [department]


def build():
    setup.start()
    root = Department["Administration"]
    depts = {name: Department(id=name, parent=root) for name in DEPARTMENTS}
    for p in PEOPLE:
        person(p)
    depts["Civil Registry"].head = "dana"
    seat("civil-registry.head", "dana", depts["Civil Registry"], "user.add", "user.edit", "user.update_status",
         "user.view", "identity.review", "invite.manage")
    seat("civil-registry.clerk", "bob", depts["Civil Registry"], "user.view", "invite.manage")
    seat("infrastructure.planning_officer", "carla", depts["Infrastructure"], "bid.evaluate", "tender.manage")
    seat("justice.judge", "elena", depts["Justice"], "dispute.accept", "dispute.reject", "evidence.evaluate",
         "resolution.draft", "resolution.issue", "resolution.finalize", "appeal.allow")
    clinic = Organization(id="clinic-b", description="Clinic B, health provider", admins=["dr-ana"])
    Organization(id="buildco", description="BuildCo, contractor", admins=["bob"])
    seat("clinic-b.physician", "dr-ana", clinic, "credential.sign")
    seat("clinic-b.records_agent", "records-bot", clinic, agent=True, supervisor="dr-ana")
    Fund(id="ROOT", name="General fund", department=root)
    Fund(id="ROADS", name="Roads", kind="special_revenue", department=depts["Infrastructure"])
    Token(id="REALM", ledger="realm-ledger", indexer="realm-indexer", decimals=8, fee=10)
    return depts


def policy(department, m, n, quorum_percent=0, veto=()):
    department.policy = {"m": m, "n": n, "quorum_percent": quorum_percent, "veto": list(veto)}
