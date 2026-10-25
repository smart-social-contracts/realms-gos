import importlib
import sys
import types

import fake_chain
import op
import world
from ggg import Delegation, Event
from kit import call, denied, ok

DOMAINS = ("assets", "codex", "crypto", "delegation", "extensions", "federation", "justice", "licenses", "members",
           "orgs", "procurement", "proposals", "quarters", "setup", "taxes", "tasks", "treasury")


def test_story_justice_notifies_mallory_of_her_fine():
    world.build()
    n = ok("notification_send", "ctl", recipient="User:mallory", topic="fine", title="Fine of 500 REALM",
           body="Spam in the forum", href="/justice/CASE-1")["notification"]
    ok("notification_send", "ctl", recipient="User:alice", title="P-7 passed", href="/proposals/P-7")
    mine = ok("notification_list", "mallory")
    assert mine["unread"] == 1 and mine["notifications"][0]["title"] == "Fine of 500 REALM"
    assert mine["notifications"][0]["sender"] == "ctl"
    assert "not addressed to you" in call("notification_read", "alice", notification=n)["error"]
    ok("notification_read", "mallory", notification=n)
    assert ok("notification_list", "mallory", unread=True) == {"ok": True, "notifications": [], "unread": 0}


def test_department_notifications_reach_its_staff_newest_first():
    world.build()
    ok("notification_send", "ctl", recipient="Department:Civil Registry", title="Office closed Friday")
    fake_chain.state["now"] += 60
    ok("notification_send", "ctl", recipient="User:bob", title="Your shift")
    assert [n["title"] for n in ok("notification_list", "bob")["notifications"]] == ["Your shift", "Office closed Friday"]
    assert ok("notification_list", "alice")["notifications"] == []
    ok("notification_read", "bob", all=True)
    assert ok("notification_list", "bob")["unread"] == 0


def test_notifications_are_delegable_and_sending_needs_the_permission():
    world.build()
    ok("notification_send", "ctl", recipient="User:alice", title="P-7 passed")
    Delegation(id="d1", grantor="alice", delegate="claude-conn", status="active",
               scope={"operations": ["self.data_view", "self.data_manage"]}, expires_at=fake_chain.now() + 3600)
    assert ok("notification_list", "claude-conn", on_behalf_of="alice")["unread"] == 1
    ok("notification_read", "claude-conn", on_behalf_of="alice", all=True)
    assert ok("notification_list", "alice")["unread"] == 0
    denied("notification_list", fake_chain.ANONYMOUS)
    denied("notification_send", "alice", recipient="User:bob", title="hi")
    assert "unknown" in call("notification_send", "ctl", recipient="User:nobody", title="hi")["error"]


def test_events_newest_first_and_paginated():
    world.build()
    for i in range(5):
        ok("notification_send", "ctl", recipient="User:alice", title=f"n{i}")
    page = ok("events", "ctl", count=3)
    assert [e["args"]["title"] for e in page["events"]] == ["n4", "n3", "n2"]
    rest = ok("events", "ctl", **{"from": page["next"], "count": 3})
    assert [e["args"]["title"] for e in rest["events"]] == ["n1", "n0"] and rest["next"] is None
    denied("events", "alice")


def test_I_EXT_4_the_event_log_is_append_only():
    world.build()
    ok("notification_send", "ctl", recipient="User:alice", title="first")
    before = [(e["id"], e["op"], e["args"]) for e in ok("events", "ctl")["events"]]
    ok("notification_send", "ctl", recipient="User:bob", title="second")
    ok("notification_read", "alice", all=True)
    after = [(e["id"], e["op"], e["args"]) for e in ok("events", "ctl")["events"]]
    assert after[2:] == before and [e[1] for e in after[:2]] == ["notification_read", "notification_send"]
    assert not [n for n, s in op.OPS.items() if "event" in n and not s["read"]]
    assert Event.count() == 3


def test_ops_list_is_public_and_complete(monkeypatch):
    for name in DOMAINS:
        if importlib.util.find_spec(name) is None:  # a domain still being written
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    out = ok("ops_list", fake_chain.ANONYMOUS)["operations"]
    by_name = {o["name"]: o for o in out}
    assert set(by_name) == set(op.OPS)
    assert by_name["notification_list"] == {"name": "notification_list", "perm": "self.data_view", "read": True,
                                            "delegable": True, "governed": False, "signing": False, "public": False,
                                            "doc": by_name["notification_list"]["doc"]}
    assert "newest first" in by_name["notification_list"]["doc"] and by_name["ops_list"]["public"]
