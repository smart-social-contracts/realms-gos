import fake_chain
import world
from ggg import Delegation, Department, Event, Request
from kit import call, denied, direct, later, ok

DAY = 86400
BODY = {"asks_for": "the minor contracts of last year"}


def registry():
    """Agora with a clerk who decides requests."""
    depts = world.build()
    world.person("clerk")
    world.seat("civil-registry.requests", "clerk", depts["Civil Registry"], "request.decide")
    return depts


def file_request(who="bob", **extra):
    return ok("request_file", who, kind="access_to_information", body=BODY, **extra)["request"]


def test_story_bob_asks_for_information_and_the_clerk_grants_it():
    registry()
    r = file_request()
    row = Request[r]
    assert (row.kind, row.by, row.body, row.status, row.filed_at) == (
        "access_to_information", "User:bob", BODY, "filed", fake_chain.now())
    assert row.due_at == 0 and row.extension_limit == 0
    got = ok("request_get", "bob", request=r)["request"]
    assert (got["id"], got["kind"], got["by"], got["body"], got["status"]) == (
        r, "access_to_information", "User:bob", BODY, "filed")
    due = fake_chain.now() + 30 * DAY
    ok("request_set_due", "clerk", request=r, due_at=due)
    later(10 * DAY)
    ok("request_set_due", "clerk", request=r, due_at=due + 30 * DAY, extension_limit=due + 60 * DAY)
    assert (Request[r].due_at, Request[r].extension_limit) == (due + 30 * DAY, due + 60 * DAY)
    ok("request_decide", "clerk", request=r, decision="granted", reply="DOC-14")
    row = Request[r]
    assert (row.status, row.decision, row.reply, row.decided_at) == ("decided", "granted", "DOC-14", fake_chain.now())


def test_a_request_is_filed_by_the_caller_or_an_organization_they_administer():
    registry()
    assert Request[file_request("dr-ana", organization="clinic-b")].by == "Organization:clinic-b"
    assert "do not administer" in denied("request_file", "bob", kind="petition", body={}, organization="clinic-b")
    assert call("request_file", "bob", kind="petition", body={}, organization="nowhere")["kind"] == "not_found"
    assert call("request_file", "bob", kind="", body={})["kind"] == "invalid_argument"
    assert "sign in" in denied("request_file", fake_chain.ANONYMOUS, kind="petition", body={})
    assert "request.file" in denied("request_file", "stranger", kind="petition", body={})


def test_a_member_reads_only_their_own_requests():
    registry()
    mine, theirs = file_request("bob"), file_request("alice")
    assert ok("request_get", "bob", request=mine)["request"]["id"] == mine
    assert call("request_get", "bob", request=theirs)["kind"] == "not_found"
    assert call("request_get", "bob", request="REQ-99")["kind"] == "not_found"
    admin = file_request("dr-ana", organization="clinic-b")
    assert ok("request_get", "dr-ana", request=admin)["request"]["by"] == "Organization:clinic-b"
    assert call("request_get", "bob", request=admin)["kind"] == "not_found"
    assert "sign in" in denied("request_get", fake_chain.ANONYMOUS, request=mine)


def test_a_delegate_files_and_reads_for_the_grantor():
    registry()
    Delegation(id="d1", grantor="bob", delegate="claude-conn", status="active",
               scope={"operations": ["request.file", "self.data_view"]}, expires_at=fake_chain.now() + 3600)
    r = ok("request_file", "claude-conn", kind="petition", body=BODY, on_behalf_of="bob")["request"]
    assert Request[r].by == "User:bob"
    assert ok("request_get", "claude-conn", request=r, on_behalf_of="bob")["request"]["id"] == r
    assert call("request_decide", "claude-conn", request=r, decision="granted", on_behalf_of="clerk")["denied"]


def test_only_deciders_list_requests_a_page_at_a_time():
    registry()
    a, b, c = file_request("alice"), file_request("bob"), file_request("carla")
    ok("request_file", "carla", kind="complaint", body={})
    ok("request_decide", "clerk", request=b, decision="refused")
    assert "request.decide" in denied("request_list", "alice")
    ids = lambda out: [r["id"] for r in out["requests"]]  # noqa: E731
    assert ids(ok("request_list", "clerk")) == [a, b, c, "REQ-4"]
    assert ids(ok("request_list", "clerk", kind="complaint")) == ["REQ-4"]
    assert ids(ok("request_list", "clerk", status="filed", count=100)) == [a, c, "REQ-4"]
    first = ok("request_list", "clerk", count=3)
    assert ids(first) == [a, b, c] and first["next"] == 4
    assert ids(ok("request_list", "clerk", count=3, **{"from": first["next"]})) == ["REQ-4"]
    assert ok("request_list", "clerk")["requests"][0]["body"] == BODY


def test_I_LIC_3_the_extension_limit_is_set_once_and_the_deadline_never_passes_it():
    registry()
    r = file_request()
    now = fake_chain.now()
    too_late = denied("request_set_due", "clerk", request=r, due_at=now + 20 * DAY, extension_limit=now + 10 * DAY)
    assert "pass its extension limit" in too_late
    assert (Request[r].due_at, Request[r].extension_limit) == (0, 0)
    ok("request_set_due", "clerk", request=r, due_at=now + 10 * DAY, extension_limit=now + 30 * DAY)
    assert call("request_set_due", "clerk", request=r, due_at=now + 12 * DAY,
                extension_limit=now + 60 * DAY)["kind"] == "conflict"
    assert call("request_set_due", "clerk", request=r, due_at=now + 12 * DAY,
                extension_limit=now + 30 * DAY)["kind"] == "conflict"
    assert call("request_set_due", "clerk", request=r, due_at=now + 31 * DAY)["kind"] == "invalid_argument"
    ok("request_set_due", "clerk", request=r, due_at=now + 30 * DAY)
    assert (Request[r].due_at, Request[r].extension_limit) == (now + 30 * DAY, now + 30 * DAY)


def test_I_LIC_3_a_decided_request_is_final():
    registry()
    r = file_request()
    assert call("request_decide", "clerk", request=r, decision="")["kind"] == "invalid_argument"
    ok("request_decide", "clerk", request=r, decision="refused_by_silence")
    assert call("request_decide", "clerk", request=r, decision="granted")["kind"] == "conflict"
    assert call("request_set_due", "clerk", request=r, due_at=fake_chain.now() + DAY)["kind"] == "conflict"
    assert (Request[r].decision, Request[r].reply) == ("refused_by_silence", "")


def test_I_ALL_3_nobody_decides_their_own_request():
    depts = registry()
    world.seat("civil-registry.requests", "bob", depts["Civil Registry"])
    world.grant(depts["Civil Registry"], "request.decide")
    mine = file_request("bob")
    assert "party" in denied("request_decide", "bob", request=mine, decision="granted")
    assert "party" in denied("request_set_due", "bob", request=mine, due_at=fake_chain.now() + DAY)
    ok("request_decide", "clerk", request=mine, decision="granted")
    org = file_request("dr-ana", organization="clinic-b")
    world.grant(depts["Health"], "request.decide")
    world.seat("health.requests", "dr-ana", depts["Health"])
    assert "party" in denied("request_decide", "dr-ana", request=org, decision="granted")


def test_I_PRV_1_the_audit_log_keeps_neither_the_body_nor_the_reply_of_a_request():
    registry()
    world.seat("civil-registry.monitor", "dana", Department["Civil Registry"], "realm.monitor")
    r = ok("request_file", "bob", kind="personal_data", body={"document": "DOC-3", "note": "my address is 5 Elm St"})["request"]
    ok("request_decide", "clerk", request=r, decision="granted", reply="DOC-4 and 5 Elm St")
    log = [e for e in ok("events", "dana")["events"] if e["op"].startswith("request_")]
    assert {e["op"] for e in log} == {"request_file", "request_decide"}
    assert "Elm" not in str([(e["args"], e["result"]) for e in log])
    assert Event[str(Event.max_id())].args == {"request": r, "decision": "granted"}


def test_json_arguments_of_the_wrong_type_are_refused_not_trapped():
    """Candid cannot say these, so the arguments go to the operations as JSON."""
    registry()
    r = file_request()
    calls = (("request_file", "bob", {"kind": 5, "body": {}}), ("request_file", "bob", {"kind": ["x"], "body": {}}),
             ("request_set_due", "clerk", {"request": r, "due_at": "soon"}),
             ("request_set_due", "clerk", {"request": r, "due_at": 5, "extension_limit": [1]}),
             ("request_decide", "clerk", {"request": r, "decision": 5}))
    for name, who, args in calls:
        out = direct(name, who, **args)
        assert not out["ok"] and out["kind"] == "invalid_argument", (name, args)
    assert Request[r].status == "filed" and Request.max_id() == 1
