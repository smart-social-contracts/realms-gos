import fake_chain
import world
from core import op
from ggg import Delegation, Event, Notification
from kit import call, denied, ok


def test_story_justice_notifies_mallory_of_her_fine():
    world.build()
    n = ok("notification_send", "ctl", recipient="User:mallory", topic="fine", title="Fine of 500 REALM",
           body="Spam in the forum", href="/justice/CASE-1")["notification"]
    ok("notification_send", "ctl", recipient="User:alice", title="P-7 passed", href="/proposals/P-7")
    mine = ok("notification_list", "mallory")["notifications"]
    assert [(x["title"], x["sender"], x["read"]) for x in mine] == [("Fine of 500 REALM", "ctl", False)]
    assert "not addressed to you" in call("notification_read", "alice", notification=n)["error"]
    assert ok("notification_read", "mallory", notification=n)["read"] == 1
    assert ok("notification_list", "mallory", unread=True) == {"ok": True, "notifications": [], "next": None}


def test_department_notifications_reach_its_staff():
    world.build()
    ok("notification_send", "ctl", recipient="Department:Civil Registry", title="Office closed Friday")
    ok("notification_send", "ctl", recipient="User:bob", title="Your shift")
    assert [n["title"] for n in ok("notification_list", "bob")["notifications"]] == ["Your shift"]
    staff = ok("notification_list", "bob", recipient="Department:Civil Registry")["notifications"]
    assert [n["title"] for n in staff] == ["Office closed Friday"]
    assert ok("notification_list", "alice")["notifications"] == []
    assert "do not read" in call("notification_list", "alice", recipient="Department:Civil Registry")["error"]
    assert "do not read" in call("notification_read", "alice", all=True, recipient="User:bob")["error"]
    ok("notification_read", "bob", all=True, recipient="Department:Civil Registry")
    assert ok("notification_list", "bob", recipient="Department:Civil Registry", unread=True)["notifications"] == []
    assert ok("notification_list", "dana", recipient="Department:Civil Registry", unread=True)["notifications"]


def test_inbox_pages_and_read_all_runs_in_batches():
    world.build()
    for i in range(5):
        ok("notification_send", "ctl", recipient="User:alice", title=f"n{i}")
    first = ok("notification_list", "alice", count=3)
    rest = ok("notification_list", "alice", count=3, **{"from": first["next"]})
    assert [n["title"] for n in first["notifications"] + rest["notifications"]] == [f"n{i}" for i in range(5)]
    assert rest["next"] is None
    ok("notification_read", "alice", notification=first["notifications"][1]["id"])
    batch = ok("notification_read", "alice", all=True, count=3)
    assert batch["read"] == 2 and batch["next"]
    again = ok("notification_read", "alice", all=True, count=3)
    assert again == {"ok": True, "read": 0, "next": batch["next"]}
    last = ok("notification_read", "alice", all=True, count=3, **{"from": batch["next"]})
    assert last == {"ok": True, "read": 2, "next": None}
    assert ok("notification_list", "alice", unread=True)["notifications"] == []
    assert all(n.read_by == ["alice"] for n in Notification.load_some(1, 10))


def test_notifications_are_delegable_and_sending_needs_the_permission():
    world.build()
    ok("notification_send", "ctl", recipient="User:alice", title="P-7 passed")
    Delegation(id="d1", grantor="alice", delegate="claude-conn", status="active",
               scope={"operations": ["self.data_view", "self.data_manage"]}, expires_at=fake_chain.now() + 3600)
    assert len(ok("notification_list", "claude-conn", on_behalf_of="alice", unread=True)["notifications"]) == 1
    ok("notification_read", "claude-conn", on_behalf_of="alice", all=True)
    assert ok("notification_list", "alice", unread=True)["notifications"] == []
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


def test_ops_list_is_public_and_complete():
    out = ok("ops_list", fake_chain.ANONYMOUS)["operations"]
    by_name = {o["name"]: o for o in out}
    assert set(by_name) == set(op.OPS)
    assert by_name["notification_list"] == {"name": "notification_list", "perm": "self.data_view", "read": True,
                                            "delegable": True, "governed": False, "signing": False, "public": False,
                                            "doc": by_name["notification_list"]["doc"]}
    assert "``recipient``" in by_name["notification_list"]["doc"] and by_name["ops_list"]["public"]
