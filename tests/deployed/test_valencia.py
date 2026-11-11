"""València's layered codex end to end on the real wasm (docs/codices/layered-codex.md §3.3, §5, §7, §8, §11;
realms-gos#494), from the legislation of the realms-codices tree (``codices/legislation``, the submodule this repository
pins) to the guards, hooks and forbids running in the realm.

The EU and Spain are one stand-in record, the public file of a source that runs no realm. The Comunitat Valenciana (CV),
EMSHI and València are realms of one wasm on one PocketIC instance:

- CV syncs from the stand-in record and installs its releases of the EU's and Spain's layers from the chapters' URLs
  (nothing else vouches for them), then its own layer, and publishes it;
- EMSHI syncs from CV: the EU's layer by a change list (a realm's first layered install names it), Spain's and CV's by
  "Propose install", vouched for by CV's record, whose capital serves the chapters by checksum, then the water chapter,
  which it publishes;
- València syncs from CV and EMSHI and installs the same way: the layers from above, then its own, then the IBI bonus of
  its tax chapter by the change list of the realms-codices sample.

The realms are built once for the module and the tests below use them in order. What the flow costs is printed in the
table of instruction costs at the end of the session.
"""

import calendar
import json
import sys
import time
import types
from pathlib import Path

import pytest
from network import Network
from test_forbids import CAP, call, drafted, entry, ok
from test_installs import HOUR, WEB, Site, active_id, install, settled, sha, status

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "realm_backend"))  # the library the realm runs, which ``realms legislation`` runs too

from realms.legislation import plan, resolve  # noqa: E402

TREE = ROOT / "codices" / "legislation"
CHANGES = ROOT / "codices" / "changes"

EU, SPAIN, CV, EMSHI, VALENCIA = "eu", "eu/es", "eu/es/vc", "eu/es/vc/emshi", "eu/es/vc/46250-valencia"
SPAIN_RECORD = "https://legislation.example.test/eu/es/record.json"
# The PocketIC clock starts in 2021, before any calendar of the tree: the story runs in November 2026, when its
# releases were published and 2027 is ahead.
TODAY = calendar.timegm((2026, 11, 11, 12, 0, 0))
DAY = 24 * HOUR
IBI_BONUS = "2026-11-20-ibi-bonus-2027.json"
# Spain's register chapter makes ``on_user_add`` fixed (§5.5): València tries to override it.
REGISTER = 'from realms.legislation import above\n\n\nclass Register(above("register")):\n    def on_user_add(self, event):\n        pass\n'
ORDINANCE_GUARD = "an ordinance takes effect at the earliest 15 working days after the vote closes (Art. 65.2)"


# ---- The realms-codices tree, as the sources publish it


def day(text):
    return calendar.timegm(time.strptime(text, "%Y-%m-%d"))


def noon(year, month, of_month):
    return calendar.timegm((year, month, of_month, 12, 0, 0))


def releases_of(source):
    """The entries of ``source``'s ``releases.json``, oldest first, as a record lists them: with their ``source`` and
    their times in seconds."""
    entries = json.loads((TREE / source / "releases.json").read_text())
    return [{**e, "source": source, "published": day(e["published"]), "effective_from": day(e["effective_from"])}
            for e in entries]


def newest(source):
    return releases_of(source)[-1]


def in_tree(*sources):
    """``[(source, version)]`` of the newest release of each of ``sources``."""
    return [(source, newest(source)["version"]) for source in sources]


def layer_files(release):
    """``{chapter id: bytes}`` of a release: its chapters and its calendar, read from the tree and held to the checksums
    the release lists."""
    source = release["source"]
    files = {f"{source}/{domain}": (TREE / source / f"{domain}.py").read_bytes() for domain in release["chapters"]}
    listed = {f"{source}/{domain}": checksum for domain, checksum in release["chapters"].items()}
    if release["calendar"]:
        files[f"{source}/calendar"] = (TREE / source / "calendar.json").read_bytes()
        listed[f"{source}/calendar"] = release["calendar"]
    assert {chapter: "sha256:" + sha(body) for chapter, body in files.items()} == listed, source
    return files


def own_layer_files(source, domains):
    """``{chapter id: bytes}`` of the realm's own layer as the tree has it: the chapters ``domains`` and the calendar, if
    the source has one."""
    folder = TREE / source
    files = {f"{source}/{domain}": (folder / f"{domain}.py").read_bytes() for domain in domains}
    if (folder / "calendar.json").exists():
        files[f"{source}/calendar"] = (folder / "calendar.json").read_bytes()
    return files


def publication(release):
    """``legislation_release_publish``'s arguments for an entry of a ``releases.json``."""
    return {"source": release["source"], "version": release["version"], "effective_from": release["effective_from"],
            "commit": release["commit"], "calendar": release["calendar"], "notes": release["notes"],
            "chapters": [{"domain": d, "checksum": c} for d, c in release["chapters"].items()]}


def stand_in():
    """Spain's public record: the EU's release it relays, then its own releases."""
    return json.dumps({"source": SPAIN, "releases": [newest(EU)] + releases_of(SPAIN)}).encode()


def sample_change_list(net, replaces):
    """The change list of the realms-codices sample, the IBI bonus of València's tax chapter, served at its own URL. The
    sample names the codex València ran when Spain's newest release was 2.4.0: ``replaces`` is the one it runs now."""
    sample = json.loads((CHANGES / VALENCIA / IBI_BONUS).read_text())
    for chapter in sample["set"]:
        body = (TREE / f"{chapter['chapter']}.py").read_bytes()
        assert (sha(body), len(body)) == (chapter["sha256"], chapter["size"])  # the bytes the sample pins
        net.web.serve(chapter["url"], body)
    change = json.dumps({**sample, "replaces": replaces}).encode()
    url = f"{WEB}changes/{VALENCIA}/{IBI_BONUS}"
    net.web.serve(url, change)
    return {"url": url, "checksum": "sha256:" + sha(change)}


# ---- What the realm runs, read in its heap. The install task's steps are counted where they run: the step is wrapped
# in the heap for the test (an upgrade would undo it), and the instructions of each message a step runs are kept, under
# the tag of the install. The switch of a scheduled install is not a step: it runs when its proposal executes.

METER = '''
from api import installs
if not hasattr(installs, "spent"):
    installs.spent, installs.tag, original = {}, "", installs.step

    def step(job_id):
        """The job's step as before, with the instructions of each message it runs added to ``spent``."""
        key, inner, sent, thrown, total = (installs.tag, installs.InstallJob[job_id].step), original(job_id), None, None, 0
        while True:
            start = ic.performance_counter(0)
            try:
                request = inner.throw(thrown) if thrown else inner.send(sent)
            except StopIteration as done:
                installs.spent.setdefault(key, []).append(total + ic.performance_counter(0) - start)
                return done.value
            total += ic.performance_counter(0) - start
            sent, thrown = None, None
            try:
                sent = yield request
            except BaseException as e:  # noqa: BLE001  the call it waited for was rejected: the step sees it
                thrown = e

    installs.step = step
installs.tag = %(tag)r
'''
SPENT = "import json\nfrom api import installs\nprint(json.dumps([[tag, step, n] for (tag, step), n in installs.spent.items()]))"
VIEWS = "import json\nfrom core import legislation\nlegislation._ensure()\nprint(json.dumps(legislation._call('views')))"
GUARD = '''
import json
from core import legislation
legislation._ensure()
args = {"action": {"op": "codex_install", "args": {"url": "https://example.org/c.json", "checksum": "sha256:x"}}, "effective_at": %(at)r}
found = legislation._call("guard", op="proposal_create", args=args, now=%(now)d, approvals=[])
print(json.dumps([o.get("denied") for o in found]))
'''


def last_line(out):
    assert "Traceback" not in out, out
    return out.strip().splitlines()[-1]


def measured(realm, tag):
    """From now on the install task's steps in ``realm`` are counted under ``tag``."""
    out = realm.shell(METER % {"tag": tag})
    assert "Traceback" not in out, out


def spent(realm):
    return json.loads(last_line(realm.shell(SPENT)))


def views_of(realm):
    """The flattened view of every domain as the realm's codex computes it (§5.9), by domain."""
    return json.loads(last_line(realm.shell(VIEWS)))


def member(views, domain, name):
    return next(m for m in views[domain]["members"] if m["name"] == name)


def guard_outcome(realm, now, effective_at):
    """What Spain's guard on ``proposal_create`` says of an ordinance (a change list to install, §5.2) that takes effect at
    ``effective_at`` when it is ``now``: the reason it denies, or None."""
    denied = [reason for reason in json.loads(last_line(realm.shell(GUARD % {"now": now, "at": effective_at}))) if reason]
    return denied[0] if denied else None


# ---- Steps of the flow


def link(follower, *sources):
    """``follower`` votes to sync from each of ``sources`` and each votes to accept it."""
    for source in sources:
        follower.govern("sync_source_add", {"source": {"Realm": source.cid}, "name": source.name})
        source.govern("follower_accept", {"follower": follower.cid})


def installed(job):
    assert (job["status"], job["error"]) == ("done", ""), job
    return job


def install_layer(net, site, realm, release):
    """A release of a layer from above, installed by a change list that gives the chapters' URLs."""
    measured(realm, "a release by a change list, chapters by URL")
    args = site.change_list(layer_files(release), realm=realm.source, replaces=active_id(realm),
                            release={"source": release["source"], "version": release["version"]})
    return installed(install(net, realm, args))


def file_ordinance(net, realm, args):
    """A change list of the realm's own layer, adopted for a date 30 days out: Spain's guard on an ordinance asks for at
    least 15 working days (§5.2). Its steps 1 to 5 run at once and the codex waits, ``pending``. Returns the proposal."""
    proposal = realm.govern("codex_install", args, effective_at=realm.now() + 30 * DAY)
    job = settled(net, realm)
    assert (job["status"], job["step"], job["error"]) == ("running", "wait", ""), job
    return proposal


def install_own(net, site, realm, files):
    """The realm's own chapters, ``files``, installed by an ordinance that takes effect a month later."""
    measured(realm, "the realm's own layer by a change list")
    proposal = file_ordinance(net, realm, site.change_list(files, realm=realm.source, replaces=active_id(realm)))
    realm.run_at_effective_at(proposal)
    return installed(status(realm)["job"])


def propose_install(net, realm, release):
    """Alice presses "Propose install" for ``release``: a proposal whose action installs it whole, voted by everyone."""
    measured(realm, "a release by Propose install, chapters from the capital")
    realm.govern("codex_install", {"source": release["source"], "version": release["version"]}, by="alice")
    return installed(settled(net, realm))


def layers_of(realm):
    return status(realm)["codex"]["layers"]


def released(realm):
    """``[(source, version)]`` of the record of ``realm``: the layers it relays, then its own."""
    return [(r["source"], r["version"]) for r in realm.ok("legislation_releases", None, query=True)["releases"]]


@pytest.fixture(scope="module")
def net(wasm_of, people):
    network = Network(wasm_of("production"), people)
    network.pic.set_time(TODAY * 10**9)
    return network


@pytest.fixture(scope="module")
def site(net):
    return Site(net.web)


def named(net, name, source):
    """A realm of the story. It knows the outside bodies the legislation tells: Spain's tells AGE and GVA of an
    ordinance, EMSHI's tells EMSHI of a water tariff."""
    realm = net.realm(name)
    realm.source = source
    for body in ("AGE", "GVA", "EMSHI"):
        ok(realm, "external_add", "ctl", external=body, name=body, kind="authority", channel="manual")
    return realm


@pytest.fixture(scope="module")
def cv(net):
    return named(net, "cv", CV)


@pytest.fixture(scope="module")
def emshi(net):
    return named(net, "emshi", EMSHI)


@pytest.fixture(scope="module")
def valencia(net):
    return named(net, "valencia", VALENCIA)


# ---- §3.3, §8, §11: the realms and what they publish


def test_a_cv_installs_the_eus_and_spains_releases_from_the_stand_in_record_then_its_own_layer_and_publishes_it(
        net, site, cv):
    record = stand_in()
    net.web.serve(SPAIN_RECORD, record)
    cv.govern("sync_source_add", {"source": {"Record": {"url": SPAIN_RECORD, "checksum": "sha256:" + sha(record)}},
                                  "name": "Spain"})
    install_layer(net, site, cv, newest(EU))
    install_layer(net, site, cv, newest(SPAIN))
    install_own(net, site, cv, own_layer_files(CV, ["language", "local_regime"]))
    assert layers_of(cv) == [EU, SPAIN, CV]
    cv.govern("legislation_release_publish", publication(newest(CV)))
    assert released(cv) == in_tree(EU, SPAIN, CV)  # what it relays, then its own


def test_b_emshi_takes_the_layers_from_cvs_record_and_publishes_the_water_chapter(net, site, cv, emshi):
    link(emshi, cv)
    install_layer(net, site, emshi, newest(EU))  # the first layered install names the realm: a change list
    for source in (SPAIN, CV):
        propose_install(net, emshi, newest(source))
    install_own(net, site, emshi, own_layer_files(EMSHI, ["water"]))
    assert layers_of(emshi) == [EU, SPAIN, CV, EMSHI]
    emshi.govern("legislation_release_publish", publication(newest(EMSHI)))
    assert released(emshi) == in_tree(EU, SPAIN, CV, EMSHI)


def test_c_valencia_takes_the_layers_from_cv_and_emshi_and_installs_its_own_on_top(net, site, cv, emshi, valencia):
    link(valencia, cv, emshi)
    install_layer(net, site, valencia, newest(EU))
    # A draft saved while no legislation forbids anything: the forbids of Spain's layer will refuse it later (§5.6).
    valencia.legacy = ok(valencia, "tax_policy_draft", "ctl", **drafted(entry("ibi_urban", 1_300_000)))["policy"]
    for source in (SPAIN, CV, EMSHI):
        propose_install(net, valencia, newest(source))
    install_own(net, site, valencia, own_layer_files(VALENCIA, ["data_protection", "local_regime", "procedure"]))
    codex = status(valencia)["codex"]
    assert codex["layers"] == [EU, SPAIN, CV, EMSHI, VALENCIA]
    assert codex["layer_paths"] == {  # §3.3: València has no chapter in tax or water of its own yet, and EMSHI writes water
        "data_protection": [EU, SPAIN, VALENCIA], "language": [SPAIN, CV], "register": [SPAIN],
        "local_regime": [SPAIN, CV, VALENCIA], "procedure": [SPAIN, VALENCIA], "tax": [SPAIN], "water": [EMSHI]}
    routes = {c: v["route"] for c, v in codex["chapters"].items() if v["source"] != VALENCIA}
    assert set(routes.values()) == {"capital"}  # the capitals serve every chapter by checksum, ahead of the URLs


# ---- §5.1: model legislation


def test_d_valencia_decides_in_two_months_a_town_with_no_procedure_chapter_in_three_and_the_six_month_forbid_holds(
        valencia, emshi):
    deadline = member(views_of(valencia), "procedure", "default_decision_deadline")
    assert deadline["value"] == {"count": 2, "unit": "Month"}  # written in months, as Spain's forbid reads months
    assert [layer["source"] for layer in deadline["layers"]] == [SPAIN, VALENCIA]
    assert member(views_of(emshi), "procedure", "default_decision_deadline")["value"] == {"count": 3, "unit": "Month"}

    def define(months=None, unit="month", **more):
        period = {"decision_deadline": {"count": months, "unit": unit}} if months is not None else {}
        return call(valencia, "procedure_define", "ctl", kind="permit", initiated_by="applicant", **period, **more)

    for refused in (define(7), define(180, "day")):  # over six months; another unit fails closed (§6)
        assert not refused["ok"] and "procedure_define is forbidden by the legislation" in refused["error"], refused
        assert "refused by eu/es/procedure/over_six_months (Ley 39/2015 Art. 21.2)" in refused["error"]
    assert define(6)["ok"] and define(None)["ok"] and define(7, legislation="Ley 39/2015 Art. 21.2 (by law)")["ok"]


# ---- §5.5: refused, overriding fixed legislation


def test_e_a_chapter_that_overrides_fixed_legislation_is_refused_at_install_naming_the_chapter(net, site, valencia):
    before = active_id(valencia)
    args = site.change_list({f"{VALENCIA}/register": REGISTER.encode()}, realm=VALENCIA, replaces=before)
    valencia.govern("codex_install", args, effective_at=valencia.now() + 30 * DAY)
    job = settled(net, valencia)
    assert job["status"] == "failed" and job["step"] == "build", job  # after every chapter was fetched and checked
    assert f"{VALENCIA}/register.py:5: on_user_add is fixed by eu/es/register.py (Ley 7/1985 Art. 17.3)" in job["error"]
    assert active_id(valencia) == before and status(valencia)["pending"] is None


# ---- §5.2, §5.6: the change list of the sample, the forbids and the settings it fills


def test_f_before_the_tax_chapter_of_valencia_spains_cap_is_the_plain_one(valencia):
    plain = 1_100_000  # 1.10 %: València is not yet a capital with surface public transport
    for rate, allowed in ((plain, True), (plain + 1, False), (CAP, False)):
        out = call(valencia, "tax_policy_draft", "ctl", **drafted(entry("ibi_urban", rate)))
        assert out["ok"] is allowed, (rate, out)
    assert member(views_of(valencia), "tax", "capital")["value"] is False  # Spain's default, still


def test_g_the_ordinance_guard_refuses_an_ordinance_that_takes_effect_too_soon(valencia, people):
    action = {"op": "codex_install", "args": {"url": f"{WEB}change.json", "checksum": "sha256:" + "0" * 64}}
    for at in (None, valencia.now() + 10 * DAY):  # no date; 10 calendar days are not 15 working days
        filing = {"title": "ordinance", "action": action, **({"effective_at": at} if at else {})}
        refused = valencia.call("proposal_create", filing, sender=people["founder"])
        assert not refused["ok"] and ORDINANCE_GUARD in refused["error"], refused
    allowed = valencia.call("proposal_create", {"title": "ordinance", "action": action,
                                                "effective_at": valencia.now() + 30 * DAY}, sender=people["founder"])
    assert allowed["ok"], allowed
    ok(valencia, "proposal_withdraw", "founder", proposal=allowed["proposal"])
    motion = valencia.call("proposal_create", {"title": "a motion"}, sender=people["founder"])  # not an ordinance
    assert motion["ok"], motion


def notices(realm, who):
    return [n["title"] for n in realm.ok("notification_list", who, query=True)["notifications"] if n["topic"] == "ordinance"]


def test_h_the_sample_change_list_adds_valencias_tax_chapter_and_the_ordinance_hooks_run_down_the_layers(net, valencia):
    measured(valencia, "the realm's own layer by a change list")
    before, told = active_id(valencia), len(ok(valencia, "dispatch_list", "ctl", query=True)["dispatches"])
    heard = {who: len(notices(valencia, who)) for who in valencia.members}
    proposal = file_ordinance(net, valencia, sample_change_list(net, replaces=before))
    pending = status(valencia)["pending"]
    assert pending["status"] == "pending" and active_id(valencia) == before

    valencia.run_at_effective_at(proposal)
    now = status(valencia)["codex"]
    assert (now["id"], now["previous"]) == (pending["id"], before)
    assert now["layer_paths"]["tax"] == [SPAIN, VALENCIA]  # §3.3: Spain → València
    sample = json.loads((CHANGES / VALENCIA / IBI_BONUS).read_text())["set"][0]
    assert now["chapters"][sample["chapter"]]["url"] == sample["url"]

    # Spain's hook tells the authorities, CV's sets the languages, València's tells every member (§5.2).
    sent = ok(valencia, "dispatch_list", "ctl", query=True)["dispatches"][told:]
    assert [(d["to"], d["topic"], d["title"]) for d in sent] == [("AGE", "ordinance", "Comunicació d'acord"),
                                                                ("GVA", "ordinance", "Comunicació d'acord")]
    assert {who: len(notices(valencia, who)) for who in valencia.members} == {who: n + 1 for who, n in heard.items()}


def test_i_the_taxes_forbids_fold_in_valencias_settings_at_draft_and_at_propose(valencia):
    tax = views_of(valencia)
    assert [member(tax, "tax", n)["value"] for n in ("capital", "surface_public_transport")] == [True, True]
    assert member(tax, "tax", "services_beyond_art26")["value"] is False  # Spain asks, València leaves it
    for refused in (entry("ibi_urban", CAP + 1), entry("ibi_urban"), entry("ibi_urban", 399_999), entry("ibi_rustic")):
        out = call(valencia, "tax_policy_draft", "ctl", **drafted(entry("water_supply", 5), refused))
        assert not out["ok"] and "tax_policy_draft is forbidden by the legislation: taxes[1]" in out["error"], out
    valencia.saved = ok(valencia, "tax_policy_draft", "ctl",
                        **drafted(entry("ibi_urban", CAP), entry("water_supply", 5)))["policy"]

    refused = call(valencia, "tax_policy_propose", "ctl", policy=valencia.legacy)  # saved before the legislation: 1.30 %
    assert not refused["ok"] and "refused by eu/es/tax/ibi_urban_cap (TRLRHL Art. 72.3)" in refused["error"], refused
    filed = call(valencia, "proposal_create", "founder", title="adopt",
                 action={"op": "tax_policy_propose", "args": {"policy": valencia.legacy}})
    assert not filed["ok"] and "taxes[0]: refused by eu/es/tax/ibi_urban_cap" in filed["error"], filed


# ---- §5.7: a body beside the line


def told_emshi(realm):
    """``[(topic, title)]`` of the dispatches queued for EMSHI."""
    sent = ok(realm, "dispatch_list", "ctl", query=True, to="EMSHI")["dispatches"]
    return [(d["topic"], d["title"]) for d in sent]


def test_j_a_water_tariff_proposed_in_valencia_is_told_to_emshi_by_its_own_chapter(valencia):
    assert status(valencia)["codex"]["layer_paths"]["water"] == [EMSHI]  # València has no water chapter: EMSHI's alone
    bulk = member(views_of(valencia), "water", "bulk_supplier")
    assert (bulk["mark"], [layer["source"] for layer in bulk["layers"]]) == ("fixed", [EMSHI])

    assert ok(valencia, "tax_policy_propose", "ctl", policy=valencia.saved)["status"] == "adopted"
    assert told_emshi(valencia) == [("tariff", "Water tariff proposed")]

    plain = ok(valencia, "tax_policy_draft", "ctl", **drafted(entry("ibi_urban", CAP)))["policy"]
    ok(valencia, "tax_policy_propose", "ctl", policy=plain)  # no water entry: EMSHI is told nothing more
    assert len(told_emshi(valencia)) == 1


# ---- §5.3: working days counted on the union of each level's holidays


def test_k_the_working_days_of_an_ordinance_skip_the_holidays_of_every_layer_the_realm_runs(valencia):
    # Spain's 8 December (Inmaculada) is a holiday in every realm that runs Spain's calendar ...
    assert guard_outcome(valencia, noon(2026, 12, 1), noon(2026, 12, 22)) == ORDINANCE_GUARD
    assert guard_outcome(valencia, noon(2026, 12, 1), noon(2026, 12, 23)) is None
    # ... and València's 22 January (Sant Vicent Màrtir) only in València: Spain's count alone ends on 5 February.
    assert guard_outcome(valencia, noon(2027, 1, 15), noon(2027, 2, 5)) == ORDINANCE_GUARD
    assert guard_outcome(valencia, noon(2027, 1, 15), noon(2027, 2, 8)) is None
    assert guard_outcome(valencia, noon(2027, 1, 15), None) == ORDINANCE_GUARD  # no date at all


# ---- §5.4: guards stacking


def share(realm, recipient, legal_basis, **basis):
    """``document_share`` of a document the realm does not hold: the guards run first, so a call they let through
    ends in NotFound."""
    basis = {"legal_basis": legal_basis, "purpose": "an application", **basis}
    return call(realm, "document_share", "ctl", document="D-0", recipient=recipient, keys={}, basis=basis)


def test_l_the_guards_of_the_eu_spain_and_valencia_run_in_that_order_and_the_first_deny_stops(valencia):
    ok(valencia, "external_add", "ctl", external="BUILDCO", name="BuildCo", kind="company", channel="manual")
    assert "consent is not a basis" in share(valencia, "External:AGE", "gdpr-6-1-a")["error"]  # the EU's guard
    assert "name the legislation" in share(valencia, "External:AGE", "gdpr-6-1-c")["error"]  # Spain's
    task = {"legislation": "Ley 7/1985"}
    assert "Junta de Govern Local" in share(valencia, "External:BUILDCO", "gdpr-6-1-c", **task)["error"]  # València's
    through = share(valencia, "External:AGE", "gdpr-6-1-c", **task)  # no guard refuses it: the document is missing
    assert not through["ok"] and through["kind"] == "not_found", through


# ---- §5.9: what a member sees, and the tools


def listed(realm):
    """The record of a sync source in the shape ``plan`` takes (§8): the release each of its layers runs, top to bottom."""
    record = realm.ok("legislation_releases", None, query=True)
    newest_of = {}
    for release in record["releases"]:
        newest_of[release["source"]] = {**release, "calendar": release["calendar"] or None}  # a newer one replaces
    return {"source": record["source"], "layers": list(newest_of.values())}


def ggg_placeholders():
    """What a chapter's ``from ggg import ...`` resolves to when only the names matter, as in ``realms legislation
    resolve``: a class for any name."""
    module = types.ModuleType("ggg")
    module.__getattr__ = lambda name: type(name, (), {})
    return module


def tree_texts():
    """Every chapter and calendar of the tree by checksum."""
    files = [p for p in TREE.rglob("*") if p.suffix in (".py", ".json") and p.name != "releases.json"]
    return {"sha256:" + sha(p.read_bytes()): p.read_text() for p in files}


def test_m_valencias_flattened_view_is_what_the_resolve_tool_computes_for_the_same_records(cv, emshi, valencia):
    """``realms legislation resolve`` (§12.4) takes the records of the sync sources, plans the layers and resolves the
    codex with the library the realm runs. Here it takes the records CV and EMSHI publish, and València's own layer."""
    codex = status(valencia)["codex"]
    own = {"chapters": {c.rpartition("/")[2]: v["sha256"] for c, v in codex["chapters"].items()
                        if v["source"] == VALENCIA and not c.endswith("/calendar")},
           "calendar": codex["chapters"][f"{VALENCIA}/calendar"]["sha256"]}
    planned = plan(VALENCIA, [listed(cv), listed(emshi)], own)
    assert (planned.layers, planned.paths) == (codex["layers"], codex["layer_paths"])
    planned_sums = {f"{c['source']}/{c['domain']}": c["sha256"] for c in planned.chapters}
    planned_sums |= {f"{c['source']}/calendar": c["sha256"] for c in planned.calendars}
    assert planned_sums == {chapter: v["sha256"] for chapter, v in codex["chapters"].items()}  # the codex id's content

    texts = tree_texts()
    resolution = resolve(planned, {c["sha256"]: texts[c["sha256"]] for c in planned.chapters},
                         {c["sha256"]: json.loads(texts[c["sha256"]]) for c in planned.calendars},
                         operations=[o["name"] for o in valencia.ok("ops_list", None, query=True)["operations"]],
                         modules={"ggg": ggg_placeholders()})
    in_the_realm = views_of(valencia)
    assert in_the_realm == json.loads(json.dumps(resolution.views()))
    assert sorted(in_the_realm) == sorted(planned.paths)  # a view for every domain València has a chapter in
    cap, bonus = member(in_the_realm, "tax", "ibi_urban_cap"), member(in_the_realm, "tax", "ibi_bonus")  # §5.9's rows
    assert (cap["kind"], [layer["source"] for layer in cap["layers"]]) == ("forbid", [SPAIN])
    assert (bonus["kind"], [layer["source"] for layer in bonus["layers"]]) == ("helper", [SPAIN, VALENCIA])


FORBIDS = '''
from core import auth, chain, op
args = {"document": {"symbol": "DOM"}, "taxes": [{"code": "ibi_urban", "kind": "tax", "subject": "Land", "rate": %(rate)d, "facts": {}}]}
spec, me = op.ops()["tax_policy_draft"], chain.caller()
try:
    auth.refuse_forbidden(me, "tax_policy_draft", spec, args, "")
except auth.Denied:
    pass
'''
HOOK = '''
from core import chain, legislation
event = {"user": "alice", "args": {"url": "https://example.org/c.json", "checksum": "sha256:x"}, "result": {"codex": "sha256:x"}, "at": chain.now()}
legislation._call("hook", op="codex_install", domain="local_regime", event=event, now=chain.now(), approvals=[])
'''


def test_n_no_chapter_of_any_realm_failed_and_the_flow_cost(cv, emshi, valencia, costs):
    for realm in (cv, emshi, valencia):
        seen = status(realm)
        assert (seen["healthy"], seen["failures"]) == (True, {}), (realm.name, seen["failures"])
        for tag, step, counts in spent(realm):
            for instructions in counts:  # one install each
                costs.report(f"install task, {tag}: {step}", instructions)
    costs.measure(valencia, "València: the flattened view of every domain, the sandbox's answer",
                  "from core import legislation\nlegislation._call('views')")
    costs.measure(valencia, "València: the guards of proposal_create, three layers' check_ methods (sandbox call)",
                  GUARD % {"now": TODAY, "at": TODAY + 30 * DAY})
    costs.measure(valencia, "València: the on_codex_install hooks of local_regime, Spain, CV and València (sandbox call)", HOOK)
    costs.measure(valencia, "València: the forbids on a tax_policy_draft of one entry, refused", FORBIDS % {"rate": CAP + 1})
