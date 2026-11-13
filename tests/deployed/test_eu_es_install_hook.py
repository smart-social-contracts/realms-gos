"""Spain's layer (realms-codices ``legislation/eu/es``, the ``codices`` submodule this repository pins) keeps the realm's
codex healthy on what a caller can send the realm, on the real wasm.

The realm hands a hook what the operation returned, and a guard the arguments as they came. A direct ``codex_install``
starts a job and returns ``{"job": ...}`` with no codex, which Spain's ``on_codex_install`` hook read as if it had one
(``KeyError: 'codex'``); its ordinance guard, and the data protection guards of the EU, Spain and València, read a
proposal's action or date, a share's basis or recipient as if they were records or text. Each raised, the realm refused
the call with "legislation error" and counted the failure, and ``codex_status`` showed the codex unhealthy.

A guard judges what is well formed and returns nothing for the rest, which the realm's own checks refuse (or, for an
install action with no args, file and fail when it runs: the same as an empty record). The realm is a Spain realm: the
EU's release from above, then eu/es by an ordinance a month out; a second realm has only València's data protection
chapter, for the recipient.
"""

import calendar
import hashlib
import json
import time
from pathlib import Path

import pytest
from network import Network
from test_forbids import ok
from test_installs import HOUR, Site, settled, status

ROOT = Path(__file__).resolve().parents[2]
TREE = ROOT / "codices" / "legislation"
EU, SPAIN, VALENCIA = "eu", "eu/es", "eu/es/vc/46250-valencia"
EU_RECORD = "https://legislation.example.test/eu/record.json"
TODAY = calendar.timegm((2026, 11, 11, 12, 0, 0))  # the PocketIC clock starts in 2021, before any calendar of the tree
DAY = 24 * HOUR
MISSING = object()  # an argument left out, as against one that is null

ORDINANCE = {"op": "codex_install", "args": {"url": "https://example.org/change.json", "checksum": "sha256:" + "0" * 64}}
# An install action that is no record of arguments. The realm files one with no args or null args, as it does one with an
# empty record, and fails it when it runs; it refuses a list or a text for args; an action that is itself a text traps in
# the realm's own check_action (not the legislation's: a follow-up of the realm).
NO_RECORD_ACTION = {"no args": {"op": "codex_install"}, "null args": {"op": "codex_install", "args": None},
                    "args a list": {"op": "codex_install", "args": []},
                    "args a text": {"op": "codex_install", "args": "checksum"}, "an action that is a text": "codex_install"}
NOT_A_TIME = {"a text": "tomorrow", "a list": [], "a record": {}}
BASIS = {"legal_basis": "gdpr-6-1-e", "purpose": "a public task", "legislation": "Ley 1"}
NOT_A_BASIS = {"a text": "gdpr-6-1-e", "a list": ["gdpr-6-1-e"], "a number": 5, "a flag": True}
NOT_A_RECIPIENT = {"missing": MISSING, "null": None, "a list": ["External:AGE"], "a number": 5, "a record": {},
                   "a text with no kind": "AGE", "a text that is empty": ""}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def day(text):
    return calendar.timegm(time.strptime(text, "%Y-%m-%d"))


def eu_record():
    """The EU's public file: a source that runs no realm, whose releases Spain takes its layer from above from."""
    releases = [{**e, "source": EU, "published": day(e["published"]), "effective_from": day(e["effective_from"])}
                for e in json.loads((TREE / EU / "releases.json").read_text())]
    return json.dumps({"source": EU, "releases": releases}).encode()


def newest_release(source):
    return {**json.loads((TREE / source / "releases.json").read_text())[-1], "source": source}


def layer_files(release):
    source = release["source"]
    return {f"{source}/{domain}": (TREE / source / f"{domain}.py").read_bytes() for domain in release["chapters"]}


def own_layer_files(source):
    folder = TREE / source
    files = {f"{source}/{p.stem}": p.read_bytes() for p in folder.glob("*.py")}
    files[f"{source}/calendar"] = (folder / "calendar.json").read_bytes()
    return files


def tried(realm, op, args, who):
    """The realm's answer to ``op``; a trap of the realm's own comes back as a refusal."""
    try:
        return realm.call(op, args, sender=who)
    except ValueError as trapped:
        return {"ok": False, "error": str(trapped)}


def stays_healthy(realm, out=None):
    """The call was not refused by the legislation failing, and the realm's codex shows no failure."""
    assert "legislation error" not in (out or {}).get("error", ""), out
    seen = status(realm)
    assert (seen["healthy"], seen["failures"]) == (True, {}), seen["failures"]


def with_a_document(realm, people):
    """Alice, a member of the department that manages documents, stores one: what a share needs to get past the
    legislation to the realm's own checks. ``realm.share`` is a share of it that is well formed."""
    ok(realm, "department_create", "ctl", department="Health")
    ok(realm, "profile_create", "ctl", profile="inspector", allowed_to=["document.manage"])
    ok(realm, "position_create", "ctl", department="Health", position="health.inspector", profile="inspector")
    ok(realm, "appoint", "ctl", position="health.inspector", user=people["alice"])
    document = ok(realm, "document_store", "alice", department="Health", title="Report", languages=["en"],
                  ref="inline:x", hash="sha256:ab12")["document"]
    realm.share = {"document": document, "recipient": "External:AGE", "keys": {}, "basis": BASIS}
    return realm


@pytest.fixture(scope="module")
def net(wasm_of, people):
    network = Network(wasm_of("production"), people)
    network.pic.set_time(TODAY * 10**9)
    return network


@pytest.fixture(scope="module")
def site(net):
    return Site(net.web)


@pytest.fixture(scope="module")
def spain(net, site, people):
    """A realm whose own layer is Spain's, with a document to share and the outside bodies Spain's legislation tells."""
    realm = net.realm("spain")
    realm.source = SPAIN
    for body in ("AGE", "GVA", "INE"):
        ok(realm, "external_add", "ctl", external=body, name=body, kind="authority", channel="manual")
    record = eu_record()
    net.web.serve(EU_RECORD, record)
    realm.govern("sync_source_add", {"source": {"Record": {"url": EU_RECORD, "checksum": "sha256:" + sha(record)}},
                                     "name": "EU"})
    eu = newest_release(EU)
    realm.govern("codex_install", site.change_list(layer_files(eu), realm=SPAIN, replaces=None,
                                                   release={"source": EU, "version": eu["version"]}))
    job = settled(net, realm)
    assert job["status"] == "done", job
    replaces = status(realm)["codex"]["id"]
    ordinance = realm.govern("codex_install", site.change_list(own_layer_files(SPAIN), realm=SPAIN, replaces=replaces),
                             effective_at=realm.now() + 30 * DAY)
    job = settled(net, realm)
    assert job["step"] == "wait", job
    realm.run_at_effective_at(ordinance)
    assert status(realm)["codex"]["layers"] == [EU, SPAIN]
    realm.told = [d["to"] for d in ok(realm, "dispatch_list", "ctl", query=True)["dispatches"]]
    return with_a_document(realm, people)


@pytest.fixture(scope="module")
def valencia(net, site, people):
    """A realm whose only layer is València's data protection chapter, with an authority and a company to share with."""
    realm = net.realm("valencia")
    realm.source = VALENCIA
    ok(realm, "external_add", "ctl", external="AGE", name="AGE", kind="authority", channel="manual")
    ok(realm, "external_add", "ctl", external="ACME", name="ACME", kind="company", channel="manual")
    chapter = {f"{VALENCIA}/data_protection": (TREE / VALENCIA / "data_protection.py").read_bytes()}
    realm.govern("codex_install", site.change_list(chapter, realm=VALENCIA, replaces=None))
    job = settled(net, realm)
    assert job["status"] == "done", job
    return with_a_document(realm, people)


# ---- on_codex_install: only an install that switches the codex has one to name


def test_the_switch_of_a_scheduled_ordinance_tells_the_state_and_the_region(spain):
    assert spain.told == ["AGE", "GVA"]
    stays_healthy(spain)


def test_a_direct_install_that_starts_a_job_returns_no_codex_and_leaves_the_realm_healthy(site, spain, people):
    replaces = status(spain)["codex"]["id"]
    args = site.change_list(own_layer_files(SPAIN), realm=SPAIN, replaces=replaces, name="direct")
    started = spain.call("codex_install", args, sender=people["ctl"])
    assert started["ok"] and started["job"] and "codex" not in started, started
    stays_healthy(spain)


# ---- The guards judge what is well formed and leave the rest to the realm


@pytest.mark.parametrize("action", NO_RECORD_ACTION.values(), ids=NO_RECORD_ACTION.keys())
def test_a_proposal_with_an_install_that_is_no_record_does_not_fail_the_ordinance_guard(spain, people, action):
    out = tried(spain, "proposal_create", {"title": "an install", "action": action}, people["founder"])
    stays_healthy(spain, out)


@pytest.mark.parametrize("at", NOT_A_TIME.values(), ids=NOT_A_TIME.keys())
def test_an_ordinance_whose_date_is_no_time_does_not_fail_the_ordinance_guard(spain, people, at):
    filing = {"title": "an ordinance", "action": ORDINANCE, "effective_at": at}
    stays_healthy(spain, tried(spain, "proposal_create", filing, people["founder"]))


@pytest.mark.parametrize("basis", NOT_A_BASIS.values(), ids=NOT_A_BASIS.keys())
def test_a_share_whose_basis_is_no_record_passes_the_guards_of_the_eu_and_spain_and_the_realm_refuses_it(
        spain, people, basis):
    out = tried(spain, "document_share", {**spain.share, "basis": basis}, people["alice"])
    assert not out["ok"] and "a basis states its legal_basis and its purpose" in out["error"], out
    stays_healthy(spain, out)


def test_valencias_guard_still_asks_the_junta_for_a_recipient_that_is_not_an_authority(valencia, people):
    out = tried(valencia, "document_share", {**valencia.share, "recipient": "External:ACME"}, people["alice"])
    assert not out["ok"] and "a recipient that is not an authority needs the Junta de Govern Local" in out["error"], out
    stays_healthy(valencia, out)


@pytest.mark.parametrize("recipient", NOT_A_RECIPIENT.values(), ids=NOT_A_RECIPIENT.keys())
def test_a_share_whose_recipient_is_no_text_passes_valencias_guard_and_the_realm_refuses_it(valencia, people, recipient):
    args = {k: v for k, v in valencia.share.items() if k != "recipient"}
    if recipient is not MISSING:
        args["recipient"] = recipient
    out = tried(valencia, "document_share", args, people["alice"])
    refused = "a document is shared with a User, an Organization or an External"
    assert not out["ok"] and (refused in out["error"] or "missing 'recipient'" in out["error"]), out
    stays_healthy(valencia, out)
