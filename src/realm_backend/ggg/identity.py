from ic_python_db import Boolean, Entity, Integer, ManyToMany, ManyToOne, OneToMany, String

from db import Json


class User(Entity):
    __alias__ = "id"
    id = String()  # principal
    nickname = String(default="")
    avatar = String(default="")
    home_quarter = String(default="")
    private_data = String(default="")  # vetKeys ciphertext
    profiles = ManyToMany(["UserProfile"], "users")
    permissions = ManyToMany(["Permission"], "users")


class Member(Entity):
    __alias__ = "id"
    id = String()  # principal
    status = String(default="active", indexed=True)  # active | inactive | suspended | banned
    joined_at = Integer(default=0)


class GuestUser(Entity):
    __alias__ = "id"
    id = String()  # principal
    home_quarter = String(default="")
    permissions = Json([])


class Human(Entity):
    __alias__ = "id"
    id = String()
    user = String(default="", indexed=True)
    date_of_birth = String(default="")
    h3_index = String(default="")
    identities = OneToMany("Identity", "human")


class Identity(Entity):
    __alias__ = "id"
    id = String()
    human = ManyToOne("Human", "identities")
    kind = String(default="")
    issuer = String(default="")
    ref = String(default="")
    status = String(default="pending")  # pending | verified | rejected
    verified_at = Integer(default=0)


class UserProfile(Entity):
    __alias__ = "id"
    id = String()  # name
    description = String(default="")
    allowed_to = Json([])
    users = ManyToMany(["User"], "profiles")
    permissions = ManyToMany(["Permission"], "profiles")
    positions = OneToMany("Position", "profile")


class Permission(Entity):
    __alias__ = "id"
    id = String()  # the permission key, e.g. "transfer.create"
    description = String(default="")
    category = String(default="")
    scope = String(default="")
    users = ManyToMany(["User"], "permissions")
    profiles = ManyToMany(["UserProfile"], "permissions")
    departments = ManyToMany(["Department"], "permissions")


class Invite(Entity):
    __alias__ = "id"
    id = String()  # sha256 of the code
    profile = String(default="member")
    department = String(default="")
    position = String(default="")
    max_uses = Integer(default=1)
    uses = Integer(default=0)
    redeemed_by = Json([])
    expires_at = Integer(default=0)
    revoked = Boolean(default=False)
    created_by = String(default="")


class Delegation(Entity):
    __alias__ = "id"
    id = String()
    grantor = String(indexed=True)
    delegate = String(indexed=True)
    scope = Json({})
    status = String(default="pending")  # pending | active | revoked | expired | declined
    label = String(default="")
    description = String(default="")
    requires_acceptance = Boolean(default=True)
    granted_by = String(default="")
    accepted_at = Integer(default=0)
    starts_at = Integer(default=0)
    expires_at = Integer(default=0)
    revoked_at = Integer(default=0)
    revoked_by = String(default="")


class DelegationRequest(Entity):
    __alias__ = "id"
    id = String()
    grantor = String(indexed=True)
    delegate = String(indexed=True)
    scope = Json({})
    label = String(default="")
    description = String(default="")
    starts_at = Integer(default=0)
    expires_at = Integer(default=0)
    request_expires_at = Integer(default=0)
