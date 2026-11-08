"""Outside bodies, scheduled tasks and procurement notices on the real wasm (realms-gos#547).

Tenders, contracts and notifications saved before these fields existed read with their defaults after an upgrade, and a
task scheduled before the upgrade runs after it. A task scheduled by a member runs with that member's authority, and ends
failed when the guard of that day refuses it. A dispatch takes one outcome. The GGG methods answer in the types of ggg.did.
"""

import pytest
from ic.candid import Types
from pic import Canister

DAY = 86400
NS = 10**9
VALUE = Types.Rec()
VALUE.fill(Types.Variant({"Nat": Types.Nat, "Int": Types.Int, "Text": Types.Text, "Blob": Types.Vec(Types.Nat8),
                          "Array": Types.Vec(VALUE), "Map": Types.Vec(Types.Tuple(Types.Text, VALUE))}))
DISPATCH = Types.Record({
    "id": Types.Text, "to": Types.Text, "topic": Types.Text, "title": Types.Text, "body": VALUE,
    "document": Types.Opt(Types.Text), "codex": Types.Opt(Types.Text), "queued_at": Types.Nat64,
    "status": Types.Variant({"Queued": Types.Null,
                             "Confirmed": Types.Record({"at": Types.Nat64, "receipt": Types.Opt(Types.Text)}),
                             "Failed": Types.Text})})
CONFIRM_ARGS = Types.Record({"dispatch": Types.Text, "outcome": Types.Variant({
    "Confirmed": Types.Record({"at": Types.Opt(Types.Nat64), "receipt": Types.Opt(Types.Text)}), "Failed": Types.Text})})
STATUS = {name: Types.Null for name in ("Scheduled", "Run", "Failed", "Cancelled")}
TASK = Types.Record({
    "id": Types.Text, "op": Types.Text, "args": VALUE, "at": Types.Nat64, "reason": Types.Opt(Types.Text),
    "status": Types.Variant(STATUS), "scheduled_by": Types.Record({"kind": Types.Text, "id": Types.Text}),
    "event": Types.Opt(Types.Text)})
TASK_LIST_ARGS = Types.Record({"cursor": Types.Opt(Types.Text), "limit": Types.Opt(Types.Nat32),
                               "op": Types.Opt(Types.Text), "status": Types.Opt(Types.Variant(STATUS))})
TASK_PAGE = Types.Record({"items": Types.Vec(TASK), "next": Types.Opt(Types.Text)})

# The rows as the last release saved them: without the fields this release added.
SAVE_AS_BEFORE = '''
from ic_python_db import Database
import ggg

db = Database.get_instance()


def as_before(name, key, added, version=None):
    entity = getattr(ggg, name)[key]._id
    row = {k: v for k, v in db.load(name, entity).items() if k not in added}
    db.save(name, entity, {**row, **({"__version__": version} if version else {})})
    assert not set(added) & set(db.load(name, entity)), name


as_before("Tender", "T-1", ("kind", "procedure", "estimated_value", "notices"), version=2)
as_before("Contract", "C-1", ("kind", "procedure", "tender"))
as_before("Notification", "N-1", ("translations",), version=1)
print("saved as before")
'''
READ_ROW = '''
import ggg
row = ggg.{name}["{key}"]
print(({fields},))
'''


def ok(realm, name, who, /, query=False, **args):
    out = realm.call(name, args, sender=who, query=query)
    assert out["ok"], (name, out)
    return out


def refused(realm, name, who, /, **args):
    out = realm.call(name, args, sender=who)
    assert not out["ok"], (name, out)
    return out


def read(realm, name, key, *fields):
    """Fields of a row as the canister reads them from storage."""
    return eval(realm.shell(READ_ROW.format(name=name, key=key, fields=", ".join(f"row.{f}" for f in fields))))


def task(realm, who, name):
    (found,) = [t for t in ok(realm, "task_list", who, query=True)["tasks"] if t["name"] == name]
    return found


def last_event(realm, who, name):
    return next(e for e in ok(realm, "events", who, query=True)["events"] if e["op"] == name)


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A launched realm with a clerk who confirms dispatches and enters tenders, a member who schedules tasks, two
    outside bodies, a tender, a contract and a notification, then a task scheduled for after the upgrade; the rows are
    saved as the last release saved them, and the realm is upgraded."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    ctl, founder, alice, bob = people["ctl"], people["founder"], people["alice"], people["bob"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_launch", founder)
    ok(realm, "realm_join", alice, nickname="Alice")
    ok(realm, "realm_join", bob, nickname="Bob")
    ok(realm, "department_create", ctl, department="Health")
    for position, who, allowed in (
            ("health.scheduler", alice, ["task.schedule", "notification.send", "document.manage"]),
            ("health.clerk", bob, ["dispatch.confirm", "external.report", "tender.manage", "realm.monitor"])):
        ok(realm, "profile_create", ctl, profile=position, allowed_to=allowed)
        ok(realm, "position_create", ctl, department="Health", position=position, profile=position)
        ok(realm, "appoint", ctl, position=position, user=who)
    ok(realm, "external_add", ctl, external="gazette", name="Provincial gazette", kind="journal", channel="manual")
    ok(realm, "external_add", ctl, external="stats-office", name="Statistics office", kind="authority", channel="manual")
    now = realm.now()
    ok(realm, "tender_create", bob, requester="Health", title="Bike lane works", opens_at=now, closes_at=now + DAY,
       rubric={"price": 100_000_000})
    ok(realm, "contract_create", ctl, title="Cleaning")
    ok(realm, "notification_send", ctl, recipient=f"User:{bob}", title="Welcome")
    ok(realm, "task_schedule", alice, op="notification_send", args={"recipient": f"User:{bob}", "title": "Before the upgrade"},
       at=now + 2 * DAY, reason="scheduled before the upgrade")
    assert "saved as before" in realm.shell(SAVE_AS_BEFORE)
    realm.upgrade()
    return realm


def test_a_tender_a_contract_and_a_notification_saved_before_read_with_their_defaults(realm, people):
    bob = people["bob"]
    got = ok(realm, "tender_get", bob, query=True, tender="T-1")["tender"]
    assert (got["kind"], got["procedure"], got["estimated_value"], got["notices"]) == ("", "", None, [])
    assert read(realm, "Tender", "T-1", "notices", "kind", "estimated_value") == ([], "", None)
    assert read(realm, "Contract", "C-1", "kind", "procedure", "tender") == ("", "", "")
    assert ok(realm, "notification_list", bob, query=True)["notifications"][0]["title"] == "Welcome"
    assert read(realm, "Notification", "N-1", "translations") == ([],)


def test_a_migrated_tender_takes_a_notice_and_a_migrated_contract_nothing_changes(realm, people):
    bob = people["bob"]
    ok(realm, "tender_notice", bob, tender="T-1", journal="realm")
    ok(realm, "tender_notice", bob, tender="T-1", journal="gazette", ref="bulletin 214", at=realm.now() - 60)
    journals = [n["journal"] for n in ok(realm, "tender_get", bob, query=True, tender="T-1")["tender"]["notices"]]
    assert journals == ["realm", "gazette"]
    assert refused(realm, "tender_notice", bob, tender="T-1", journal="stats-office")["kind"] == "invalid_argument"
    assert refused(realm, "tender_notice", bob, tender="T-1", journal="realm", at=realm.now() + 3600)["kind"] == "invalid_argument"


def test_a_contract_made_from_a_tender_takes_its_kind_and_procedure(realm, people):
    ctl, bob = people["ctl"], people["bob"]
    now = realm.now()
    ok(realm, "tender_create", bob, requester="Health", title="Supplies", opens_at=now, closes_at=now + DAY,
       rubric={"price": 100_000_000}, kind="supplies", procedure="open", estimated_value=1_000)
    ok(realm, "contract_create", ctl, title="Supplies", tender="T-2")
    assert read(realm, "Contract", "C-2", "kind", "procedure", "tender") == ("supplies", "open", "T-2")
    assert refused(realm, "contract_create", ctl, title="Supplies", tender="T-2", kind="works")["kind"] == "invalid_argument"


def test_a_task_scheduled_before_the_upgrade_runs_after_it_as_the_member_who_scheduled_it(realm, people):
    alice, bob = people["alice"], people["bob"]
    assert task(realm, bob, "TASK-1")["status"] == "pending"
    realm.wait(2 * DAY + 60)
    assert task(realm, bob, "TASK-1")["status"] == "completed"
    assert [n["title"] for n in ok(realm, "notification_list", bob, query=True)["notifications"]][-1] == "Before the upgrade"
    event = last_event(realm, bob, "notification_send")
    assert (event["origin"], event["user"]) == ("task", alice)


def test_a_task_runs_with_the_authority_of_the_member_and_ends_failed_when_the_guard_refuses(realm, people):
    alice, bob = people["alice"], people["bob"]
    at = realm.now() + 3600
    allowed = ok(realm, "task_schedule", alice, op="notification_send",
                 args={"recipient": f"User:{bob}", "title": "Lapsed"}, at=at)["task"]
    refused_at_its_time = ok(realm, "task_schedule", alice, op="external_add",
                             args={"external": "ghost", "name": "Ghost", "kind": "authority", "channel": "manual"},
                             at=at)["task"]
    realm.wait(3600 + 5)
    assert task(realm, bob, allowed)["status"] == "completed"
    failed = task(realm, bob, refused_at_its_time)
    assert failed["status"] == "failed"
    assert "external.manage" in ok(realm, "task_logs", people["ctl"], query=True, task=refused_at_its_time)["runs"][0]["result"]
    assert "ghost" not in [e["id"] for e in ok(realm, "external_list", alice, query=True)["externals"]]


def test_the_ggg_method_lists_a_task_with_its_status_scheduler_and_event(realm, people):
    alice, bob = people["alice"], people["bob"]
    page = realm.ggg("ggg_task_list", TASK_LIST_ARGS, {"cursor": [], "limit": [], "op": [], "status": []}, TASK_PAGE, bob,
                     query=True)["Ok"]
    by_id = {t["id"]: t for t in page["items"]}
    done = by_id["TASK-1"]
    assert (done["op"], done["status"], done["scheduled_by"]) == ("notification_send", {"Run": None},
                                                                  {"kind": "User", "id": alice})
    assert done["reason"] == ["scheduled before the upgrade"] and done["event"] != []
    assert {"Failed": None} in [t["status"] for t in by_id.values()]
    only = realm.ggg("ggg_task_list", TASK_LIST_ARGS,
                     {"cursor": [], "limit": [], "op": ["external_add"], "status": [{"Failed": None}]}, TASK_PAGE, bob,
                     query=True)["Ok"]
    assert [t["op"] for t in only["items"]] == ["external_add"]


def test_a_dispatch_is_confirmed_once(realm, people):
    alice, bob = people["alice"], people["bob"]
    sent = ok(realm, "notification_send", alice, recipient="External:stats-office", topic="registration",
              title="Bob registered", body="Bob joined Agora")["dispatch"]
    other = ok(realm, "notification_send", alice, recipient="External:stats-office", title="Alice registered")["dispatch"]
    assert refused(realm, "dispatch_confirm", alice, dispatch=sent, outcome={"Failed": "x"})["kind"] == "forbidden"
    done = ok(realm, "dispatch_confirm", bob, dispatch=sent, outcome={"Confirmed": {"receipt": "filing 88"}})
    assert done["dispatch"] == sent
    assert refused(realm, "dispatch_confirm", bob, dispatch=sent, outcome={"Failed": "again"})["kind"] == "conflict"
    ask = {"dispatch": other, "outcome": {"Confirmed": {"at": [], "receipt": ["bulletin 215"]}}}
    first = realm.ggg("ggg_dispatch_confirm", CONFIRM_ARGS, ask, DISPATCH, bob)["Ok"]
    assert first["status"]["Confirmed"]["receipt"] == ["bulletin 215"] and first["body"] == {"Map": [["text", {"Text": ""}]]}
    assert "Conflict" in realm.ggg("ggg_dispatch_confirm", CONFIRM_ARGS, ask, DISPATCH, bob)["Err"]
    assert [d["status"] for d in ok(realm, "dispatch_list", bob, query=True, to="stats-office")["dispatches"]] \
        == ["confirmed", "confirmed"]


def test_a_document_is_published_in_a_journal_and_its_confirmation_dates_the_publication(realm, people):
    alice, bob = people["alice"], people["bob"]
    doc = ok(realm, "document_store", alice, department="Health", title="Ordinance", languages=["en"], ref="inline:o",
             hash="sha256:ab12")["document"]
    ok(realm, "document_publish", alice, document=doc, to="gazette", ref="pub/ordinance.pdf", hash="sha256:cd34")
    (queued,) = ok(realm, "dispatch_list", bob, query=True, to="gazette", status="queued")["dispatches"]
    assert (queued["document"], queued["topic"]) == (doc, "publication")
    at = realm.now()
    ok(realm, "dispatch_confirm", bob, dispatch=queued["id"], outcome={"Confirmed": {"at": at, "receipt": "bulletin 214"}})
    (publication,) = ok(realm, "document_get", None, query=True, document=doc)["document"]["publications"]
    assert (publication["journal"], publication["ref"], publication["published_at"]) == ("gazette", "bulletin 214", at)


def test_what_an_outside_body_says_is_entered_as_an_event_with_its_evidence(realm, people):
    bob = people["bob"]
    ok(realm, "external_report", bob, external="stats-office", topic="duplicate registration", body={"user": "bob"},
       evidence_ref="files/stats/report-17.pdf")
    event = last_event(realm, bob, "external_report")
    assert (event["user"], event["args"]["evidence_ref"]) == (bob, "files/stats/report-17.pdf")
