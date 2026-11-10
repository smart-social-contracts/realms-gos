"""The install task of a layered codex on the real wasm (docs/codices/layered-codex.md §9.2, realms-gos#487): a realm
installs its own layer from a URL the harness serves, with and without an ``effective_at``; a failure at any step
leaves the codex unchanged and says why, and the same change list installs once mended; one job at a time; an
upgrade pauses a job for an admin; a stopped scheduled install leaves nothing pending. The realm is ``arcadia``
(tests/fixtures/legislation). The instructions each step costs are reported at the end of the session.
"""

import hashlib
import json
import os

import pytest
from fixtures import legislation as arcadia
from ic.candid import Types, decode, encode
from network import Network

HOUR = 3600
WEB = "https://legislation.example.test/"
LISTS = WEB + "changes/arcadia/"
SMALL_LIMIT = 64 * 1024
REALM_CHAPTER = '''
from realms.legislation import above

class Realm(above("realm")):
    def on_codex_activate(self, event):
        self.ggg.notification_send(recipient="User:%(founder)s", topic="codex", title="activated", body=event.args["codex"])
'''
BROKEN_CHAPTER = "from realms.legislation import above\n\nclass Tax(above('procedure')):\n    pass\n"
# A water chapter padded to the most a chapter may be, for the cost of receiving one.
BIG_HEAD = 'from realms.legislation import above\n\nclass Water(above("water")):\n    pass\n\n# '
BIG_CHAPTER = (BIG_HEAD + "x" * (256 * 1024 - len(BIG_HEAD) - 1) + "\n").encode()
HEADER = Types.Record({"name": Types.Text, "value": Types.Text})
HTTP_RESPONSE = Types.Record({"status": Types.Nat, "headers": Types.Vec(HEADER), "body": Types.Vec(Types.Nat8)})
TRANSFORM_ARGS = Types.Record({"response": HTTP_RESPONSE, "context": Types.Vec(Types.Nat8)})


def sha(data):
    return hashlib.sha256(data).hexdigest()


def chapter_text(domain, version="1.0.0"):
    return arcadia.chapter(next(r for r in arcadia.releases() if r["version"] == version)["chapters"][domain])


def own_layer(founder, version="1.0.0"):
    """``{chapter id: bytes}`` of arcadia's own layer: its release's chapters and the realm domain's."""
    return {f"arcadia/{d}": chapter_text(d, version) for d in ("tax", "procedure")} | {
        "arcadia/realm": (REALM_CHAPTER % {"founder": founder}).encode()}


def expected_id(chapters, realm="arcadia"):
    """The codex id of ``chapters`` as the realm's own layer (core.layered_codex.codex_id), computed here."""
    paths = {domain: [realm] for domain in sorted(c.rpartition("/")[2] for c in chapters)}
    sums = {c: "sha256:" + sha(body) for c, body in chapters.items()}
    canonical = json.dumps({"chapters": sums, "layer_paths": paths, "layers": [realm]}, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + sha(canonical.encode())


class Site:
    """Change lists and chapters served by the harness's web, and the ``codex_install`` arguments that name them."""

    def __init__(self, web):
        self.web, self.lists = web, 0

    def chapters(self, chapters):
        """Serve ``chapters`` (``{chapter id: bytes}``) and return their change-list entries."""
        entries = []
        for chapter, body in chapters.items():
            url = WEB + chapter + ".py"
            self.web.serve(url, body)
            entries.append({"chapter": chapter, "url": url, "mirrors": [], "sha256": sha(body), "size": len(body)})
        return entries

    def change_list(self, chapters, realm="arcadia", replaces=None, remove=(), release=None, name=None):
        entries = self.chapters(chapters)
        self.lists += 1
        url = LISTS + (name or f"list-{self.lists}") + ".json"
        text = json.dumps({"kind": "realms-gos/legislation-change", "format": 1, "realm": realm,
                           "replaces": replaces, "set": entries, "remove": list(remove), "release": release,
                           "summary": {"en": "test"}}).encode()
        self.web.serve(url, text)
        return {"url": url, "checksum": "sha256:" + sha(text)}


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def town(net):
    return net.realm("town")


@pytest.fixture(scope="module")
def site(net):
    return Site(net.web)


def status(realm):
    return realm.ok("codex_status", "founder", query=True)


def active_id(realm):
    codex = status(realm)["codex"]
    return codex["id"] if codex else None


def settled(net, realm):
    """Run the job to its end: ``done``, ``failed`` or staged (``wait``). Returns the job."""
    net.wait_until(lambda: (status(realm)["job"] or {}).get("status") in ("done", "failed")
                   or (status(realm)["job"] or {}).get("step") == "wait", rounds=2)
    return status(realm)["job"]


def propose(realm, args, effective_at=None):
    """File an install proposal as the founder and have everyone vote yes; returns the proposal, whatever its end."""
    filing = {"title": "legislation", "action": {"op": "codex_install", "args": args}}
    if effective_at:
        filing["effective_at"] = effective_at
    proposal_id = realm.ok("proposal_create", "founder", **filing)["proposal"]
    for member in realm.members:
        if realm.proposal(proposal_id)["status"] == "voting":
            realm.ok("proposal_vote", member, proposal=proposal_id, choice="yes")
    return realm.proposal(proposal_id)


def install(net, realm, args):
    """A governed install, executed at once: the job it starts, run to its end."""
    proposal = realm.govern("codex_install", args)
    assert realm.proposal(proposal)["result"]["job"]
    return settled(net, realm)


def failing(net, realm, args, reason):
    before = active_id(realm)
    job = install(net, realm, args)
    assert job["status"] == "failed" and reason in job["error"], job
    assert active_id(realm) == before and status(realm)["pending"] is None
    return job


def test_a_the_realm_installs_its_own_layer_from_a_url_and_the_realm_hook_runs_at_the_switch(net, town, site, people):
    layer = own_layer(people["founder"])
    args = site.change_list(layer)
    job = install(net, town, args)
    assert (job["status"], job["step"], job["error"]) == ("done", "switch", "")
    seen = status(town)
    assert seen["codex"]["id"] == expected_id(layer) and seen["codex"]["status"] == "active"
    assert seen["codex"]["layers"] == ["arcadia"] and sorted(seen["codex"]["layer_paths"]) == ["procedure", "realm", "tax"]
    tax = seen["codex"]["chapters"]["arcadia/tax"]
    assert (tax["route"], tax["url"], tax["vouched_by"], tax["size"]) == ("https", WEB + "arcadia/tax.py", "arcadia",
                                                                          len(layer["arcadia/tax"]))
    assert seen["pending"] is None and seen["healthy"]
    net.wait(1)  # the realm hook runs in the message after the switch
    assert [n["title"] for n in town.ok("notification_list", "founder", query=True)["notifications"]] == ["activated"]
    assert [url for _, url in net.web.asked] == [args["url"]] + [WEB + c + ".py" for c in layer]


def test_b_the_transform_query_strips_the_headers_of_a_reply(town):
    response = {"status": 200, "headers": [{"name": "Date", "value": "now"}], "body": list(b"legislation")}
    reply = town.pic.query_call(town.id, "http_transform", encode([{"type": TRANSFORM_ARGS,
                                                                    "value": {"response": response, "context": []}}]))
    transformed = decode(bytes(reply), HTTP_RESPONSE)[0]["value"]
    assert (transformed["status"], transformed["headers"], bytes(transformed["body"])) == (200, [], b"legislation")


def test_c_a_failure_at_each_step_leaves_the_codex_unchanged_and_the_same_list_installs_once_mended(net, town, site,
                                                                                                      people):
    founder = people["founder"]
    first = active_id(town)
    newer = {**own_layer(founder), "arcadia/tax": chapter_text("tax", "2.0.0")}

    # step 1: the bytes are not the proposal's checksum; nobody serves the URL; the reply is above 64 KB
    args = site.change_list(newer, replaces=first)
    net.web.serve(args["url"], b'{"kind": "realms-gos/legislation-change", "format": 1}')
    failing(net, town, args, f"the change list at {args['url']}: the text is sha256:")
    net.web.responses.pop(args["url"])
    failing(net, town, args, f"{args['url']}: HTTP 404")
    net.web.serve(args["url"], b"x" * (SMALL_LIMIT + 1))
    failing(net, town, args, f"{args['url']}: {SMALL_LIMIT + 1} bytes is above 64 KB")
    # step 2: another realm's list; a stale replaces; a release from a source the realm does not sync from
    failing(net, town, site.change_list(newer, realm="elsewhere", replaces=first), "for elsewhere; this realm is arcadia")
    failing(net, town, site.change_list(newer), "stale change list: it replaces no codex")
    failing(net, town, site.change_list({"eu/es/tax": chapter_text("tax")}, replaces=first,
                                        release={"source": "eu/es", "version": "1.0.0"}), "no sync source relays eu/es")
    failing(net, town, site.change_list({"eu/es/tax": chapter_text("tax")}, replaces=first), "installed whole, as a release")
    # step 3: a chapter whose bytes changed; then the same list, once the bytes are back
    args = site.change_list(newer, replaces=first)
    net.web.serve(WEB + "arcadia/tax.py", newer["arcadia/tax"][:-1] + b"#")
    failing(net, town, args, f"{WEB}arcadia/tax.py: the text is sha256:")
    # step 5: a chapter the in-canister checks refuse
    failing(net, town, site.change_list({**newer, "arcadia/tax": BROKEN_CHAPTER.encode()}, replaces=first),
            "arcadia/tax.py:3: above('procedure') in the tax chapter")
    net.web.serve(WEB + "arcadia/tax.py", newer["arcadia/tax"])
    job = install(net, town, args)
    assert job["status"] == "done" and active_id(town) == expected_id(newer)
    assert status(town)["codex"]["previous"] == first


def test_d_a_second_install_while_one_runs_is_refused(net, town, site, people):
    current = active_id(town)
    stuck = site.change_list(own_layer(people["founder"]), replaces=current)
    net.web.no_answer(stuck["url"])
    town.govern("codex_install", stuck)
    net.wait(1, rounds=3)
    assert status(town)["job"]["status"] == "running"
    refused = propose(town, site.change_list(own_layer(people["founder"]), replaces=current))
    assert refused["status"] == "failed" and refused["result"]["error"] == "an install is running"
    net.wait(61)  # PocketIC rejects the outcall nobody answered, as mainnet does after a while
    job = settled(net, town)
    assert job["status"] == "failed" and f"{stuck['url']}: GET" in job["error"], job
    assert active_id(town) == current


def test_e_a_scheduled_install_stages_at_acceptance_and_switches_at_its_time_not_before(net, town, site, people):
    current = active_id(town)
    layer = {**own_layer(people["founder"]), "arcadia/water": BIG_CHAPTER}
    args = site.change_list(layer, replaces=current)
    effective_at = town.now() + HOUR
    proposal = town.govern("codex_install", args, effective_at=effective_at)
    job = settled(net, town)
    assert (job["status"], job["step"], job["proposal"]) == ("running", "wait", proposal), job["error"]
    seen = status(town)
    assert seen["codex"]["id"] == current and seen["pending"]["id"] == expected_id(layer)
    assert (seen["pending"]["status"], seen["pending"]["activates_at"]) == ("pending", effective_at)
    net.wait(HOUR - 60)
    assert active_id(town) == current and town.proposal(proposal)["status"] == "execution_scheduled"
    town.run_at_effective_at(proposal)
    seen = status(town)
    assert seen["codex"]["id"] == expected_id(layer) and seen["pending"] is None
    assert seen["job"]["status"] == "done" and town.proposal(proposal)["result"]["codex"] == expected_id(layer)
    assert town.ok("notification_list", "founder", query=True)["notifications"][-1]["title"] == "activated"


def test_f_a_job_still_running_at_the_switch_time_fails_the_proposal_and_never_finishes(net, town, site, people):
    current = active_id(town)
    args = site.change_list(own_layer(people["founder"]), replaces=current)
    net.web.no_answer(args["url"])
    proposal = town.govern("codex_install", args, effective_at=town.now() + 30)
    net.wait(31)
    assert town.proposal(proposal)["status"] == "failed"
    assert "not done at the switch time: it was running in fetch_change_list" in town.proposal(proposal)["result"]["error"]
    net.web.serve(args["url"], b"anything")  # the answer comes after all: the job is over and ignores it
    net.wait(61)
    job = status(town)["job"]
    assert job["status"] == "failed" and "not done at the switch time" in job["error"]
    assert active_id(town) == current and status(town)["pending"] is None


def test_g_a_stopped_scheduled_install_leaves_no_pending_codex(net, town, site, people):
    current = active_id(town)
    listed = site.change_list(own_layer(people["founder"]), replaces=current, remove=["arcadia/water"])
    proposal = town.govern("codex_install", listed, effective_at=town.now() + HOUR)
    assert settled(net, town)["step"] == "wait" and status(town)["pending"] is not None
    town.govern("proposal_stop", {"proposal": proposal})
    seen = status(town)
    assert seen["pending"] is None and seen["job"]["status"] == "failed" and seen["job"]["error"] == f"{proposal} was stopped"
    net.wait(HOUR + 60)
    assert active_id(town) == current and town.proposal(proposal)["status"] == "withdrawn"


def test_h_an_upgrade_pauses_the_job_and_only_an_admin_resumes_it_to_the_same_result(net, town, site, people):
    current = active_id(town)
    layer = {**own_layer(people["founder"]), "arcadia/tax": chapter_text("tax", "2.0.0"), "arcadia/water": BIG_CHAPTER}
    town.govern("codex_install", site.change_list(layer, replaces=current))
    net.upgrade_when(town, lambda: (status(town)["job"] or {}).get("step") == "fetch_chapters", rounds=1)
    job = status(town)["job"]
    assert (job["status"], job["step"]) == ("paused", "fetch_chapters")
    net.wait(60)
    assert status(town)["job"]["status"] == "paused"  # nothing resumes it by itself
    denied = town.call("codex_install_resume", {"job": job["id"]}, sender=people["alice"])
    assert not denied["ok"] and denied["kind"] == "forbidden" and "codex.install" in denied["error"]
    assert town.ok("codex_install_resume", "ctl", job=job["id"]) == {"ok": True, "job": job["id"], "step": "fetch_chapters"}
    job = settled(net, town)
    assert job["status"] == "done" and active_id(town) == expected_id(layer)


def test_i_a_job_paused_for_more_than_24_hours_fails_when_resumed(net, town, site, people):
    current = active_id(town)
    town.govern("codex_install", site.change_list(own_layer(people["founder"]), replaces=current))
    net.upgrade_when(town, lambda: (status(town)["job"] or {}).get("step") == "check_change_list", rounds=1)
    assert status(town)["job"]["status"] == "paused"
    net.wait(24 * HOUR + 1)
    town.ok("codex_install_resume", "ctl", job=status(town)["job"]["id"])
    job = settled(net, town)
    assert (job["status"], job["error"]) == ("failed", "not done within 24 hours") and active_id(town) == current


PREVIOUS = os.environ.get("REALM_PREVIOUS_WASM")


@pytest.mark.skipif(not PREVIOUS, reason="REALM_PREVIOUS_WASM names no wasm")
def test_l_an_install_begun_on_the_previous_release_finishes_after_the_upgrade(wasm_of, people):
    """What the previous release staged for a job has chapters fetched without a route and no layers: the upgrade keeps
    the job, and an admin's resume finishes it, with the chapters fetched by URL. The last chapter is held back (the
    outcall nobody answers), so that the job is still fetching when the upgrade comes."""
    old = Network(PREVIOUS, people)
    realm, held = old.realm("old"), WEB + "arcadia/realm.py"
    layer = own_layer(people["founder"])
    realm.govern("codex_install", Site(old.web).change_list(layer))
    old.web.no_answer(held)
    old.wait_until(lambda: held in [url for _, url in old.web.asked], rounds=1)
    realm.upgrade(wasm_of("production"))
    job = status(realm)["job"]
    assert (job["status"], job["step"]) == ("paused", "fetch_chapters")
    old.web.serve(held, layer["arcadia/realm"])
    realm.ok("codex_install_resume", "ctl", job=job["id"])
    assert settled(old, realm)["status"] == "done"
    assert active_id(realm) == expected_id(layer)
    assert {c["route"] for c in status(realm)["codex"]["chapters"].values()} == {"https"}


# ---- costs: each step's work, run by hand in a realm of its own on the data the install above used

LAB = '''
import json
import ggg
from api import installs
from core import layered_codex as store
job = ggg.InstallJob["J-1"] or store.start_job("", {"url": %(url)r, "checksum": %(checksum)r})
if not job.step:
    job.step = "fetch_change_list"
'''
RECEIVE_CHAPTER = LAB + '''
entry = installs.prepared(job)[0]["set"][%(index)d]
installs.receive_chapter(job, entry, 0, 200, %(body)r)
'''


def test_j_what_each_step_costs(net, site, people, costs):
    lab = net.realm("lab", members=("alice",))
    layer = {**own_layer(people["founder"]), "arcadia/water": BIG_CHAPTER}
    args = site.change_list(layer, name="lab")
    listed = json.loads(net.web.responses[args["url"]].body)
    costs.measure(lab, "install step 1: receive the change list (hash, store)",
                  LAB % args + f"installs.receive_change_list(job, 200, {net.web.responses[args['url']].body!r})")
    costs.measure(lab, "install step 2: check the change list (first use: the library compiles)", LAB % args + "installs.check_change_list(job)")
    costs.measure(lab, "install step 2: check the change list (again)", LAB % args + "installs.check_change_list(job)")
    costs.measure(lab, "install step 3: the records (an own layer reads none), the relays and the early check",
                  LAB % args + "from core.op import run\nrun(installs.read_records(job, installs.prepared(job)[0]))")
    for index, entry in enumerate(listed["set"]):
        body = net.web.responses[entry["url"]].body
        size = "256 KB" if len(body) > 100_000 else "small"
        costs.measure(lab, f"install step 3: receive a {size} chapter (hash, store)",
                      RECEIVE_CHAPTER % {**args, "index": index, "body": body})
    costs.measure(lab, "install step 4: check the sources (plan, records)", LAB % args + "installs.check_sources(job)")
    costs.measure(lab, "install step 5: build (fresh sandbox, 4 chapters, the pending row)", LAB % args + "installs.build(job)")
    costs.measure(lab, "install step 6: switch (load, the statuses)", LAB % args + "installs.finish(job)")
    costs.measure(lab, "install step 6: the realm hook, in its own message",
                  LAB % args + "from api import codex\nfrom core.op import run\nrun(codex.hooks(job.staged['codex']))")
    assert active_id(lab) == expected_id(layer) and status(lab)["job"]["status"] == "done"


# The hook notes that it ran, then asks for the realm's vetKD key: PocketIC has no such key, the management canister
# rejects the call after the await, and the effect traps. The note, written before the await, stays.
TRAPPING_REALM_CHAPTER = '''
from realms.legislation import above

class Realm(above("realm")):
    def on_codex_activate(self, event):
        self.ggg.notification_send(recipient="User:%(founder)s", topic="codex", title="about to call", body="")
        self.ggg.crypto_public_key()
'''


def test_k_the_switch_stands_when_the_realm_hook_traps_after_a_call_it_made(net, town, site, people):
    current = active_id(town)
    layer = {**own_layer(people["founder"]), "arcadia/realm": (TRAPPING_REALM_CHAPTER % {"founder": people["founder"]}).encode()}
    before = len(town.ok("notification_list", "founder", query=True)["notifications"])
    town.govern("codex_install", site.change_list(layer, replaces=current, remove=["arcadia/water"]))
    job = settled(net, town)
    assert (job["status"], job["step"]) == ("done", "switch"), job
    seen = status(town)
    assert seen["codex"]["id"] == expected_id(layer) and seen["codex"]["previous"] == current and seen["pending"] is None
    net.wait(5)  # the hook's message: the note is written, the call made, the reply a rejection, the message trapped
    titles = [n["title"] for n in town.ok("notification_list", "founder", query=True)["notifications"][before:]]
    assert titles == ["about to call"]  # written before the await, so the call was made
    seen = status(town)
    assert seen["codex"]["id"] == expected_id(layer) and seen["failures"] == {} and seen["healthy"]  # no trace of the trap
    with pytest.raises(Exception, match="vetkd_public_key") as rejected:  # what the hook's call met: the same, directly
        town.call("crypto_public_key", {}, sender=people["founder"])
    assert "trap" in str(rejected.value).lower()
