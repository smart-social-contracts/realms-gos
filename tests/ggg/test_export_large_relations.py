"""A many-to-many side that can hold a whole realm (every member holds the profile "member") is left out of the rows
of the export and of every view: ic-python-db refuses to serialize a relation of more than MAX_UNPAGED rows, which
would trap realm_export, and with it `realms export` backups, on a realm of more than a thousand members. The other
side lists the same links."""

import pytest
import world
from core.db import view
from ic_python_db import Entity
from ic_python_db.constants import MAX_UNPAGED
from ic_python_db.errors import TooLargeError
from kit import ok

from ggg import Case, Court, Judge, Permission, User, UserProfile


def exported(entity, key):
    rows = ok("realm_export", "ctl", entity=entity, count=500)["rows"]
    return next(r for r in rows if r["id"] == key)


def test_a_profile_held_by_more_members_than_a_call_can_load_is_exported_without_its_users():
    world.build()
    for n in range(MAX_UNPAGED + 1):
        world.person(f"m{n}")
    member = UserProfile["member"]
    assert len(member.users) > MAX_UNPAGED
    with pytest.raises(TooLargeError):  # what Entity.serialize does with it
        Entity.serialize(member)

    row = exported("UserProfile", "member")

    assert "users" not in row and row["allowed_to"]
    assert "users" not in view(member) and view(member, "id", "allowed_to")["id"] == "member"
    assert exported("User", "m7")["profiles"] == ["member"]  # the member's own row carries the link


def test_a_permission_held_by_more_users_than_a_call_can_load_is_exported_without_them():
    world.build()
    permission = Permission(id="proposal.vote", description="vote")
    for n in range(MAX_UNPAGED + 1):
        permission.users.add(world.person(f"p{n}"))
    assert len(permission.users) > MAX_UNPAGED

    row = exported("Permission", "proposal.vote")

    assert "users" not in row and row["description"] == "vote"
    assert exported("User", "p3")["permissions"] == ["proposal.vote"]


def test_a_judge_with_more_cases_than_a_call_can_load_is_exported_without_them():
    world.build()
    court = Court(id="C1")
    judge = Judge(id="J1", user="elena", court=court)
    for n in range(MAX_UNPAGED + 1):
        judge.cases.add(Case(id=f"K{n}", court=court))
    assert len(judge.cases) > MAX_UNPAGED

    row = exported("Judge", "J1")

    assert "cases" not in row and row["court"] == "C1" and row["user"] == "elena"
    assert exported("Case", "K5")["judges"] == ["J1"]


def test_a_row_with_no_unlisted_side_serializes_as_ic_python_db_does():
    world.build()
    user = User["alice"]
    assert user.serialize() == Entity.serialize(user) and user.serialize()["profiles"] == ["member"]


def test_a_small_row_serializes_as_ic_python_db_does_minus_the_unlisted_side():
    """Only that side differs: the fields, the links to one row and the other many-to-many sides are the same."""
    world.build()
    permission = Permission(id="proposal.vote", description="vote")
    permission.users.add(User["alice"])
    permission.profiles.add(UserProfile["member"])
    court = Court(id="C1")
    judge = Judge(id="J1", user="elena", court=court)
    judge.cases.add(Case(id="K1", court=court))

    for row, side in ((UserProfile["member"], "users"), (permission, "users"), (judge, "cases")):
        full = Entity.serialize(row)
        assert full[side], (row, side)  # the side has members here, so the comparison means something
        assert row.serialize() == {k: v for k, v in full.items() if k != side}
    assert Entity.serialize(permission)["profiles"] == permission.serialize()["profiles"] == ["member"]
    assert judge.serialize()["court"] == "C1"
