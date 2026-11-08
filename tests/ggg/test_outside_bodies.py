"""Outside bodies (GGG §11): the External parties, the Dispatch outbox that an operator confirms, reports that come from
outside, and the notices and publications the realm queues for them."""

import fake_chain
import ggg_api
import ggg_methods as g
import world
from core import op
from ggg import Department, Dispatch, Document, Event, External, Notification, Proposal, User
from kit import call, denied, direct, later, ok
from test_crypto import REPORT, health
from test_ratios import stored_as_version_1

NS = 1_000_000_000
GAZETTE = dict(external="gazette", name="Provincial gazette", kind="journal", channel="manual")
OFFICE = dict(external="stats-office", name="Statistics office", kind="authority", channel="https://stats.example/inbox")


def outside():
    """Agora with Carla inspecting for Health, Bob in charge of the outbox, and the gazette and the statistics office."""
    health()
    world.profile("outbox", "dispatch.confirm", "external.report")
    world.person("bob", "outbox")
    ok("external_add", "ctl", **GAZETTE)
    ok("external_add", "ctl", **OFFICE)


def tell(title="Bob registered", **more):
    return ok("notification_send", "ctl", recipient="External:stats-office", topic="registration", title=title,
              body="Bob joined Agora", **more)["dispatch"]


def confirm(dispatch, caller="bob", **outcome):
    return call("dispatch_confirm", caller, dispatch=dispatch, outcome={"Confirmed": outcome})


# --- the outside bodies --------------------------------------------------------------------------

def test_the_realm_registers_the_bodies_it_deals_with_and_a_second_registration_redefines_one():
    outside()
    assert [(e["id"], e["kind"], e["channel"]) for e in ok("external_list", "alice")["externals"]] == [
        ("gazette", "journal", "manual"), ("stats-office", "authority", "https://stats.example/inbox")]
    ok("external_add", "ctl", **{**GAZETTE, "name": "Official gazette", "channel": "https://gazette.example/in"})
    assert External.count() == 2
    assert (External["gazette"].name, External["gazette"].channel) == ("Official gazette", "https://gazette.example/in")


def test_external_list_pages():
    outside()
    first = ok("external_list", "alice", count=1)
    assert [e["id"] for e in first["externals"]] == ["gazette"] and first["next"] == 2
    assert [e["id"] for e in ok("external_list", "alice", **{"from": first["next"]})["externals"]] == ["stats-office"]


def test_an_outside_body_is_registered_whole_and_by_the_holder_of_the_permission():
    world.build()
    assert "needs external.manage" in denied("external_add", "alice", **GAZETTE)
    for change in ({"channel": "ftp://gazette.example"}, {"channel": ""}, {"kind": ""}, {"name": ""}, {"external": ""},
                   {"external": "realm"}):
        assert call("external_add", "ctl", **{**GAZETTE, **change})["kind"] == "invalid_argument", change
    assert External.count() == 0


def test_registering_an_outside_body_is_the_realms_to_decide():
    world.build()
    world.policy(Department["Administration"], 2, 3)
    p = ok("external_add", "ctl", **GAZETTE)["proposal"]
    assert External.count() == 0
    for voter in world.PEOPLE:
        ok("proposal_vote", voter, proposal=p, choice="yes")
    assert Proposal[p].status == "executed" and External["gazette"].kind == "journal"


# --- notices to them -----------------------------------------------------------------------------

def test_story_civil_registry_tells_the_statistics_office_of_bobs_registration():
    outside()
    sent = tell()
    row = Dispatch[sent]
    assert (row.to, row.topic, row.title, row.body, row.status) == ("stats-office", "registration", "Bob registered",
                                                                     {"text": "Bob joined Agora"}, "queued")
    assert row.queued_at == fake_chain.now() and Notification.count() == 0
    later(3600)
    out = ok("dispatch_confirm", "bob", dispatch=sent, outcome={"Confirmed": {"at": fake_chain.now() - 60,
                                                                               "receipt": "filing 88"}})
    assert out["dispatch"] == sent
    assert (row.status, row.outcome_at, row.receipt) == ("confirmed", fake_chain.now() - 60, "filing 88")
    assert ok("dispatch_list", "bob", status="confirmed")["dispatches"][0]["receipt"] == "filing 88"


def test_a_notice_goes_to_an_outside_body_the_realm_knows():
    outside()
    out = call("notification_send", "ctl", recipient="External:nobody", title="Hello")
    assert out["kind"] == "not_found" and Dispatch.count() == 0
    assert ok("notification_send", "ctl", recipient="User:alice", title="Hello")["notification"] == "N-1"


def test_a_dispatch_has_one_outcome_and_a_second_is_a_conflict():
    outside()
    sent = tell()
    assert confirm(sent, receipt="bulletin 214")["ok"]
    for second in (confirm(sent, receipt="again"), call("dispatch_confirm", "bob", dispatch=sent,
                                                        outcome={"Failed": "lost"})):
        assert second["kind"] == "conflict" and "already confirmed" in second["error"]
    failed = tell("Second")
    assert ok("dispatch_confirm", "bob", dispatch=failed, outcome={"Failed": "the office was closed"})
    assert (Dispatch[failed].status, Dispatch[failed].failure) == ("failed", "the office was closed")
    assert confirm(failed)["kind"] == "conflict"


def test_when_a_dispatch_was_delivered_lies_between_its_queueing_and_now():
    outside()
    queued = fake_chain.now()
    sent = tell()
    later(600)
    for at in (queued - 1, fake_chain.now() + 1):
        out = confirm(sent, at=at)
        assert out["kind"] == "invalid_argument" and Dispatch[sent].status == "queued", at
    assert confirm(sent, at=queued)["ok"] and Dispatch[sent].outcome_at == queued
    assert confirm(tell("Third"), receipt=None)["ok"]  # no time given: now, and a receipt is optional
    assert Dispatch["DSP-2"].outcome_at == fake_chain.now() and Dispatch["DSP-2"].receipt == ""


def test_an_outcome_is_confirmed_or_failed_and_a_failure_says_why():
    outside()
    sent = tell()
    for outcome in ({}, {"Confirmed": {}, "Failed": "x"}, {"Delivered": {}}, {"Failed": ""}, {"Failed": 5},
                    {"Confirmed": "yes"}, "confirmed", None):  # Candid cannot say most of these: JSON
        assert direct("dispatch_confirm", "bob", dispatch=sent, outcome=outcome)["kind"] == "invalid_argument", outcome
    assert call("dispatch_confirm", "bob", dispatch="DSP-9", outcome={"Failed": "x"})["kind"] == "not_found"
    assert Dispatch[sent].status == "queued"


def test_the_outbox_is_for_the_holders_of_dispatch_confirm():
    outside()
    sent = tell()
    for who in ("alice", "carla"):
        assert "needs dispatch.confirm" in denied("dispatch_confirm", who, dispatch=sent, outcome={"Failed": "x"})
        assert "needs dispatch.confirm" in denied("dispatch_list", who)
    assert [d["id"] for d in ok("dispatch_list", "bob")["dispatches"]] == [sent]
    assert Dispatch[sent].status == "queued"


def test_dispatch_list_pages_and_filters_by_body_and_status():
    outside()
    first, second, third = tell("one"), tell("two"), tell("three")
    ok("notification_send", "ctl", recipient="External:gazette", title="Ordinance")
    ok("dispatch_confirm", "bob", dispatch=second, outcome={"Failed": "x"})
    page = ok("dispatch_list", "bob", count=2)
    assert [d["id"] for d in page["dispatches"]] == [first, second] and page["next"] == 3
    assert [d["id"] for d in ok("dispatch_list", "bob", **{"from": page["next"]})["dispatches"]] == [third, "DSP-4"]
    assert [d["id"] for d in ok("dispatch_list", "bob", to="gazette")["dispatches"]] == ["DSP-4"]
    assert [d["id"] for d in ok("dispatch_list", "bob", status="queued", to="stats-office")["dispatches"]] == [first, third]
    assert [d["id"] for d in ok("dispatch_list", "bob", status="failed")["dispatches"]] == [second]


# --- reports from outside ------------------------------------------------------------------------

def test_what_an_outside_body_says_enters_the_realm_as_an_event_with_the_evidence():
    outside()
    out = ok("external_report", "bob", external="stats-office", topic="duplicate registration",
             body={"user": "bob", "also": "bob2"}, evidence_ref="files/stats/report-17.pdf")
    event = Event[str(Event.max_id())]
    assert event.op == "external_report" and event.user == "bob" and out["ok"]
    assert event.args == {"external": "stats-office", "topic": "duplicate registration",
                          "body": {"user": "bob", "also": "bob2"}, "evidence_ref": "files/stats/report-17.pdf"}


def test_a_report_comes_from_a_body_the_realm_knows_through_a_principal_who_may_enter_it():
    outside()
    args = dict(external="stats-office", topic="duplicate", body="x")
    assert "needs external.report" in denied("external_report", "alice", **args)
    assert call("external_report", "bob", **{**args, "external": "nobody"})["kind"] == "not_found"
    assert call("external_report", "bob", **{**args, "topic": ""})["kind"] == "invalid_argument"


# --- publishing in an outside journal -----------------------------------------------------------

def publish(doc, **more):
    return call("document_publish", "carla", document=doc, ref="pub/report.pdf", hash="sha256:cd34", **more)


def stored():
    return ok("document_store", "carla", department="Health", **REPORT)["document"]


def test_story_civil_registry_sends_the_ordinance_to_the_provincial_gazette():
    outside()
    doc = stored()
    assert publish(doc, to="gazette")["ok"]
    row = Dispatch["DSP-1"]
    assert (row.to, row.topic, row.title, row.document, row.status) == ("gazette", "publication", REPORT["title"], doc,
                                                                         "queued")
    assert row.body == {"ref": "pub/report.pdf", "hash": "sha256:cd34"}
    assert Document[doc].public == row.body
    assert Document[doc].publications == [{"journal": "gazette", "dispatch": "DSP-1", "published_at": None, "ref": None}]
    later(86400)
    ok("dispatch_confirm", "bob", dispatch="DSP-1", outcome={"Confirmed": {"at": fake_chain.now() - 3600,
                                                                            "receipt": "bulletin 214"}})
    assert Document[doc].publications == [{"journal": "gazette", "dispatch": "DSP-1",
                                           "published_at": fake_chain.now() - 3600, "ref": "bulletin 214"}]


def test_publishing_again_queues_no_second_dispatch_unless_the_first_failed():
    outside()
    doc = stored()
    publish(doc, to="gazette")
    publish(doc, to="gazette")
    assert Dispatch.count() == 1
    ok("dispatch_confirm", "bob", dispatch="DSP-1", outcome={"Failed": "the gazette was closed"})
    publish(doc, to="gazette")
    assert Dispatch.count() == 2 and [p["dispatch"] for p in Document[doc].publications] == ["DSP-1", "DSP-2"]
    ok("dispatch_confirm", "bob", dispatch="DSP-2", outcome={"Confirmed": {}})
    publish(doc, to="gazette")
    assert Dispatch.count() == 2
    assert Document[doc].publications[0]["published_at"] is None  # the failed attempt stays as it ended


def test_a_document_goes_to_a_journal_the_realm_knows_and_only_to_a_journal():
    outside()
    doc = stored()
    assert publish(doc, to="nobody")["kind"] == "not_found"
    assert publish(doc, to="stats-office")["kind"] == "invalid_argument"
    assert Document[doc].public is None and Dispatch.count() == 0
    assert publish(doc)["ok"] and Dispatch.count() == 0  # no journal: only public


def test_a_confirmed_dispatch_that_carries_no_document_changes_no_document():
    outside()
    doc = stored()
    publish(doc, to="gazette")
    ok("dispatch_confirm", "bob", dispatch=tell(), outcome={"Confirmed": {}})
    assert Document[doc].publications[0]["published_at"] is None


# --- each reader's language ---------------------------------------------------------------------

def test_translations_give_each_reader_the_notice_in_their_language():
    world.build()
    for name, tag in (("alice", "es-ES"), ("bob", "ca"), ("dana", "de"), ("carla", "")):
        User[name].language = tag
    translations = [{"language": "es", "title": "Oficina cerrada", "body": "El viernes"},
                    {"language": "ca", "title": "Oficina tancada"}]
    for who in ("alice", "bob", "dana", "carla"):
        ok("notification_send", "ctl", recipient=f"User:{who}", title="Office closed", body="On Friday",
           translations=translations)
    read = {who: ok("notification_list", who)["notifications"][0] for who in ("alice", "bob", "dana", "carla")}
    assert (read["alice"]["title"], read["alice"]["body"]) == ("Oficina cerrada", "El viernes")  # es-ES reads es
    assert (read["bob"]["title"], read["bob"]["body"]) == ("Oficina tancada", "On Friday")  # no body: the one sent
    assert (read["dana"]["title"], read["dana"]["body"]) == ("Office closed", "On Friday")  # no German
    assert read["carla"]["title"] == "Office closed"  # no language set


def test_the_staff_of_a_department_each_read_the_notice_of_the_department_in_their_language():
    world.build()
    User["bob"].language, User["dana"].language = "es", "en"
    ok("notification_send", "ctl", recipient="Department:Civil Registry", title="Office closed",
       translations=[{"language": "es", "title": "Oficina cerrada"}])
    titles = {who: ok("notification_list", who, recipient="Department:Civil Registry")["notifications"][0]["title"]
              for who in ("bob", "dana")}
    assert titles == {"bob": "Oficina cerrada", "dana": "Office closed"}


def test_translations_are_well_formed():
    world.build()
    for bad in ([{"language": "español", "title": "x"}], [{"language": "es", "title": ""}], "es",
                [{"language": "es"}], [5]):
        assert call("notification_send", "ctl", recipient="User:alice", title="x", translations=bad)["kind"] \
            == "invalid_argument", bad
    assert Notification.count() == 0


# --- through the GGG methods ---------------------------------------------------------------------

def caller(principal):
    fake_chain.state["caller"] = principal


def do(update):
    return op.run(update)


def test_the_ggg_methods_reply_with_a_delivery_a_dispatch_and_an_event():
    outside()
    caller("ctl")
    note = {"recipient": {"kind": "User", "id": "alice"}, "title": "Hello", "body": None, "href": None, "topic": None,
            "translations": [{"language": "es", "title": "Hola", "body": None}]}
    sent = do(g.ggg_notification_send(note))["Ok"]
    assert list(sent) == ["Notification"] and sent["Notification"]["title"] == "Hello"
    to_office = do(g.ggg_notification_send({**note, "recipient": {"kind": "External", "id": "stats-office"},
                                            "topic": "registration", "body": "Bob joined"}))["Ok"]
    shown = to_office["Dispatch"]
    assert (shown["id"], shown["to"], shown["topic"], shown["status"]) == ("DSP-1", "stats-office", "registration",
                                                                           {"Queued": None})
    assert shown["body"] == {"Map": [("text", {"Text": "Bob joined"})]} and shown["document"] is None and shown["codex"] is None
    caller("bob")
    queued_at = shown["queued_at"]
    done = do(g.ggg_dispatch_confirm({"dispatch": "DSP-1", "outcome": {"Confirmed": {"at": queued_at + 5 * NS,
                                                                                    "receipt": "filing 88"}}}))
    assert list(done["Err"]) == ["InvalidArgument"]  # five seconds from now
    done = do(g.ggg_dispatch_confirm({"dispatch": "DSP-1", "outcome": {"Confirmed": {"at": None, "receipt": "filing 88"}}}))
    assert done["Ok"]["status"] == {"Confirmed": {"at": fake_chain.now() * NS, "receipt": "filing 88"}}
    assert do(g.ggg_dispatch_confirm({"dispatch": "DSP-1", "outcome": {"Failed": "x"}}))["Err"].keys() == {"Conflict"}
    listed = g.ggg_dispatch_list({"cursor": None, "limit": None, "to": "stats-office", "status": {"Confirmed": None}})
    assert [d["id"] for d in listed["Ok"]["items"]] == ["DSP-1"] and listed["Ok"]["next"] is None
    caller("ctl")
    again = do(g.ggg_notification_send({**note, "recipient": {"kind": "External", "id": "stats-office"}}))["Ok"]
    caller("bob")
    do(g.ggg_dispatch_confirm({"dispatch": again["Dispatch"]["id"], "outcome": {"Failed": "closed"}}))
    failed = g.ggg_dispatch_list({"cursor": None, "limit": None, "to": None, "status": {"Failed": None}})["Ok"]["items"]
    assert [d["status"] for d in failed] == [{"Failed": "closed"}]
    report = do(g.ggg_external_report({"external": "stats-office", "topic": "duplicate", "body": {"Text": "bob"},
                                       "evidence_ref": "files/r.pdf"}))["Ok"]
    assert report["op"] == "external_report" and report["user"] == "bob"
    page = g.ggg_external_list({"cursor": None, "limit": 1})["Ok"]
    assert [e["id"] for e in page["items"]] == ["gazette"] and page["next"] == "2"


def test_the_ggg_methods_register_an_outside_body_and_refuse_a_channel_that_is_neither_manual_nor_https():
    world.build()
    caller("ctl")
    done = do(g.ggg_external_add({"external": "gazette", "name": "Gazette", "kind": "journal", "channel": "manual"}))
    assert done == {"Ok": {"Done": "gazette"}}
    refused = do(g.ggg_external_add({"external": "x", "name": "X", "kind": "journal", "channel": "ftp://x"}))
    assert list(refused["Err"]) == ["InvalidArgument"] and External.count() == 1


def test_a_notification_stored_before_translations_reads_in_the_one_language_it_has():
    world.build()
    n = ok("notification_send", "ctl", recipient="User:alice", title="Office closed")["notification"]
    stored_as_version_1(Notification, n, drop=("translations",))
    assert ok("notification_list", "alice")["notifications"][0]["title"] == "Office closed"
    assert Notification[n].translations == []
