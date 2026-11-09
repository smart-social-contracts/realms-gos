"""What a chapter is handed of GGG's entities (docs/codices/layered-codex.md §9.3, realms-gos#512): the row the realm
reads for it (``legislation._read``) has the fields of ``VIEWS`` and no other, a private document shows no title, and a
proposal's carried call shows none of the arguments its operation keeps secret. The entities the review added
(Procedure, Loan, Request, Dispatch) keep back what holds personal data; a member's home cell is tied to no member.
tests/ggg/test_legislation_review.py runs the view through a chapter."""

import pytest

from core import legislation, op
from core.db import Fail
from ggg import Dispatch, Document, Event, Loan, Notification, Procedure, Proposal, Request, User, Zone
from kit import call, ok, rows
from test_legislation_runtime import TOWN, activate, layered
from test_legislation_runtime import fresh_realm  # noqa: F401  the autouse fixture

CELL = "8928308280fffff"  # an h3 cell: a member's home, when a member makes one a zone


@op.op("treasury.manage", secret=("code",))
def t_view_secret(c, args):
    """A write whose ``code`` is a secret, carried by a proposal."""
    return {"done": args.get("what", "")}


@op.op("treasury.manage")
def t_view(c, args):
    """A write the chapter below guards, to read GGG's entities when it is called."""
    return {}


READER = '''
from ggg import Dispatch, Loan, Procedure, Request, User, Zone
from realms.legislation import above

class Register(above("register")):
    def check_t_view(self, args):
        if args.what == "allowed":
            return self.deny(":".join([Procedure.get("licence").silence, str(Loan.get("L1").outstanding),
                                       Request.get("R1").status, Dispatch.get("D1").title, User.get("alice").home_zone,
                                       Zone.get("centro").name]))
        if args.what == "request reply":
            return self.deny(Request.get("R1").reply)
        if args.what == "dispatch body":
            return self.deny(Dispatch.get("D1").body)
        if args.what == "home cell":
            return self.deny(str(Zone.get("%s")))
''' % CELL


def get(entity, id):
    return legislation._read({"entity": entity, "method": "get", "id": id})


def page(entity):
    return legislation._read({"entity": entity, "method": "page", "start": 1})["rows"]


def find(entity, field, value):
    return legislation._read({"entity": entity, "method": "find", "field": field, "value": value, "start": 1})["rows"]


def refused(entity, field, value):
    with pytest.raises(Fail, match=f"a chapter does not read {entity}.{field}"):
        find(entity, field, value)


# ── A member's home ──────────────────────────────────────────────────────────────────────────────────────────────


def test_a_chapter_sees_the_zones_the_realm_defined_and_never_a_members_home_cell():
    Zone(id=CELL, name="home", kind="residential", user="alice", metadata={"floor": 3})
    Zone(id="centro", name="Centro", kind="district", defined=True)
    Zone(id="norte", name="Norte", kind="residential", defined=True)
    User["alice"].home_zone = "centro"
    assert get("Zone", CELL) is None
    assert get("Zone", "centro") == {"id": "centro", "name": "Centro", "kind": "district", "land": None}
    assert [z["id"] for z in page("Zone")] == ["centro", "norte"]
    assert all(set(z) == {"id", "name", "kind", "land"} for z in page("Zone"))
    for field in ("user", "metadata", "defined", "population"):
        assert field not in legislation.VIEWS["Zone"]
    refused("Zone", "user", "alice")  # a find by member would give the same link back; no other field is indexed
    assert get("User", "alice")["home_zone"] == "centro"


ZONES = '''
from realms.legislation import above

class Register(above("register")):
    def check_zone_set(self, args):
        if args.name == "ask":
            return self.deny(str(args.zone))

    def check_zone_clear(self, args):
        return self.deny(str(args.zone))

    def on_zone_set(self, event):
        self.ggg.notification_send(recipient="User:mallory", title="zone",
                                   body=str((event.args.get("zone"), event.result.get("zone"), event.args.get("name"))))
'''


def test_a_home_cell_reaches_no_guard_hook_or_audit_entry_of_the_operations_that_take_it():
    activate(layered({(TOWN, "register"): ZONES}, layers=[TOWN]))
    assert call("zone_set", "alice", zone=CELL, name="ask")["error"] == "None"  # the guard
    ok("zone_set", "alice", zone=CELL, name="home")
    assert Zone[CELL].user == "alice"  # the operation had the cell
    assert [n.body for n in rows(Notification) if n.title == "zone"] == ["(None, None, 'home')"]  # the hook
    assert call("zone_clear", "alice", zone=CELL)["error"] == "None"
    entry = [e for e in rows(Event) if e.op == "zone_set"][-1]
    assert "zone" not in entry.args and "zone" not in entry.result and entry.args["name"] == "home"  # the audit


# ── What one member was told ─────────────────────────────────────────────────────────────────────────────────────


def test_a_notification_shows_who_was_told_about_what_and_when_and_not_what_was_said():
    Notification(id="1", recipient="User:alice", topic="tax", title="payment overdue", body="for alice only",
                 sender="realm", at=7)
    assert get("Notification", "1") == {"id": "1", "recipient": "User:alice", "topic": "tax", "sender": "realm", "at": 7}


# ── The entities the review added ────────────────────────────────────────────────────────────────────────────────

PROCEDURE = {"id": "licence", "initiated_by": "User:alice", "matter": "a licence",
             "decision_deadline": {"count": 3, "unit": "month"}, "silence": "positive", "legislation": "Ley 39/2015 Art. 21"}
LOAN = {"id": "L1", "lender": "External:bank", "amount": 1000, "symbol": "REALM", "term": {"count": 5, "unit": "year"},
        "rate": 250, "fund": "ROOT", "outstanding": 900, "status": "active"}
REQUEST = {"id": "R1", "kind": "licence", "by": "User:alice", "filed_at": 1, "due_at": 2, "extension_limit": 3,
           "status": "decided", "decided_at": 4}
DISPATCH = {"id": "D1", "to": "External:bulletin", "topic": "publication", "title": "Ordinance 1", "document": "doc-1",
            "codex": "c-1", "queued_at": 5, "status": "confirmed"}
# What each keeps back: the rest of the row, as the entity stores it.
ENTITIES = [
    (Procedure, PROCEDURE, {"facts": {"income": 5}}),
    (Loan, LOAN, {"facts": {"purpose": "a school"}}),
    (Request, REQUEST, {"body": {"note": "my address"}, "decision": "refused: not resident", "reply": "see your file"}),
    (Dispatch, DISPATCH, {"body": {"text": "for the bulletin"}, "outcome_at": 6, "receipt": "BOE-1", "failure": "none"}),
]


@pytest.mark.parametrize("cls, shown, kept_back", ENTITIES, ids=[entity[0].__name__ for entity in ENTITIES])
def test_a_new_entity_shows_the_fields_legislation_needs_and_keeps_back_the_rest(cls, shown, kept_back):
    cls(**shown, **kept_back)
    name = cls.__name__
    assert get(name, shown["id"]) == shown
    assert page(name) == [shown]
    assert set(legislation.VIEWS[name]) == set(shown)
    for field in kept_back:
        assert field not in legislation.VIEWS[name]
        refused(name, field, "x")


def test_a_dispatch_is_found_by_its_recipient_and_its_status():
    Dispatch(**DISPATCH, body={"text": "for the bulletin"})
    assert find("Dispatch", "to", "External:bulletin") == [DISPATCH]
    assert find("Dispatch", "status", "failed") == []


def test_a_dispatch_shows_its_title_only_when_it_publishes_a_document():
    Dispatch(**DISPATCH)
    Dispatch(**{**DISPATCH, "id": "D2", "topic": "notice", "title": "Your file has a debt"})
    assert [(d["id"], d["topic"], d["title"]) for d in page("Dispatch")] == [
        ("D1", "publication", "Ordinance 1"), ("D2", "notice", None)]
    assert get("Dispatch", "D2")["title"] is None
    assert [d["title"] for d in find("Dispatch", "to", "External:bulletin")] == ["Ordinance 1", None]
    refused("Dispatch", "title", "Your file has a debt")


def test_a_chapter_reads_the_new_entities_and_the_home_district_and_nothing_more():
    Procedure(**PROCEDURE, facts={"income": 5})
    Loan(**LOAN)
    Request(**REQUEST, reply="see your file")
    Dispatch(**DISPATCH, body={"text": "for the bulletin"})
    Zone(id=CELL, user="alice")
    Zone(id="centro", name="Centro", defined=True)
    User["alice"].home_zone = "centro"
    activate(layered({(TOWN, "register"): READER}, layers=[TOWN]))
    assert call("t_view", what="allowed")["error"] == "positive:900:decided:Ordinance 1:centro:Centro"
    assert call("t_view", what="request reply")["error"].endswith("AttributeError: Request has no field 'reply'")
    assert call("t_view", what="dispatch body")["error"].endswith("AttributeError: Dispatch has no field 'body'")
    assert call("t_view", what="home cell")["error"] == "None"  # no such row, though there is one


# ── A document's title ───────────────────────────────────────────────────────────────────────────────────────────


def test_a_document_shows_its_title_only_when_it_is_public():
    Document(id="private", department="Administration", title="Divorce settlement of the Garcia family", kind="minutes",
             languages=["es"], author="alice", at=3)
    Document(id="public", department="Administration", title="Ordinance 12", kind="ordinance", languages=["es"],
             public={"ref": "path", "hash": "abc"}, author="alice", at=4)
    assert get("Document", "private") == {"id": "private", "department": "Administration", "title": None, "kind": "minutes",
                                          "languages": ["es"], "public": None, "author": "alice", "at": 3}
    assert get("Document", "public")["title"] == "Ordinance 12"
    assert [(d["id"], d["title"]) for d in page("Document")] == [("private", None), ("public", "Ordinance 12")]
    assert [(d["id"], d["title"]) for d in find("Document", "department", "Administration")] == [
        ("private", None), ("public", "Ordinance 12")]
    refused("Document", "title", "Divorce settlement of the Garcia family")  # a find by title would tell it


# ── What a proposal carries ──────────────────────────────────────────────────────────────────────────────────────


def test_a_proposal_shows_the_call_it_carries_and_its_result_without_the_secrets_of_that_call():
    carried = {"op": "t_view_secret", "args": {"what": "x", "code": "S3CRET", "more": [{"code": "S3CRET2", "keep": 1}]}}
    result = {"ok": True, "invite": {"code": "INVITE-CODE", "uses": 0}, "list": [{"code": "S3CRET3", "n": 2}], "done": "x"}
    plain = {"op": "t_act", "args": {"code": "stays", "what": "y"}}
    Proposal(id="P1", title="carry", action=carried, result=result, status="voting")
    Proposal(id="P2", title="plain", action=plain, result={"ok": True, "code": "stays"}, status="voting")
    Proposal(id="P3", title="none", status="voting")
    stripped = {"op": "t_view_secret", "args": {"what": "x", "more": [{"keep": 1}]}}
    cleaned = {"ok": True, "invite": {"uses": 0}, "list": [{"n": 2}], "done": "x"}
    assert (get("Proposal", "P1")["action"], get("Proposal", "P1")["result"]) == (stripped, cleaned)
    assert [(p["action"], p["result"]) for p in page("Proposal")] == [
        (stripped, cleaned), (plain, {"ok": True, "code": "stays"}), (None, None)]
    assert [p["action"] for p in find("Proposal", "status", "voting")][0] == stripped
    assert (Proposal["P1"].action, Proposal["P1"].result) == (carried, result)  # what is stored is untouched
