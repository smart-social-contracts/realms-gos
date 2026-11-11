"""The settings a realm keeps in sync for its quarters, on real wasms (realms-gos#528; "What the realm sets for its
quarters" in docs/architecture/quarters-and-federation.md): a capital and two quarters on one PocketIC instance, then a
capital in setup with its quarter, and quarters the capital builds (``adopt``).

The capital owns the realm's identity and shared foundations and pushes them on every change; a quarter that missed a
push catches up when it syncs; a quarter cannot set them itself. The time zone and the welcome message are the quarter's
own, copied once when it joins. The realms are built once for the module and the tests below use them in order.
"""

import json

import pytest
from network import Network
from test_propagation import rows

SETTINGS_TOPIC = "realm.settings"  # api.quarters
AGR = {"accounting_currency": "AGR", "accounting_currency_decimals": 2}
LOGO = "data:image/png;base64," + "A" * 900_000  # an image near the most a realm keeps (setup.IMAGE_MAX)
IMAGE_MAX, TEXT_MAX = 960_000, 20_000  # api.setup
# A quarter's or a capital's heap made to answer as the previous release does, and back again.
OLD_QUARTER = "from api import federation, quarters\nfederation._kept = federation.TOPICS.pop(quarters.SETTINGS_TOPIC)"
NEW_QUARTER = "from api import federation, quarters\nfederation.TOPICS[quarters.SETTINGS_TOPIC] = federation._kept"
NO_IMAGE_READ = "from core import op\nop._kept = op.OPS.pop('realm_image')"
IMAGE_READ = "from core import op\nop.OPS['realm_image'] = op._kept"
ADOPT_EVERY = 60  # api.quarters
GOVERNANCE = "import json\nfrom api import proposals\nprint(json.dumps([proposals.window(), proposals.settings()]))"
ADOPT = ("from api import quarters\nfrom core import chain\nquarters.set_pending(%r)\n"
         "chain.set_timer(0, quarters.adopt)")
STAGE_HOOK = '''
from ggg_sdk import hook, realm


@hook
def on_stage_change(args):
    realm.effect("notification_send", recipient="User:%s", title="stage " + args["stage"])
    return {}
'''


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def federation(net):
    """The capital, with a welcome message and its accounts in AGR; ``north``, which has chosen neither; ``south``,
    which has its own welcome message and the same currency."""
    capital = net.realm("capital")
    capital.ok("realm_configure", "ctl", welcome_message="Benvinguts")
    capital.ok("realm_configure_tokens", "ctl", **AGR)
    north, south = net.realm("north", members=("alice",)), net.realm("south", members=("alice",))
    south.ok("realm_configure", "ctl", welcome_message="Hola")
    south.ok("realm_configure_tokens", "ctl", **AGR)
    net.register_quarters(capital, north, south)
    return capital, north, south


def info(realm):
    return realm.ok("realm_info", None, query=True)["realm"]


def label(realm):
    """The name a realm shows in its own row of the directory: a quarter's label."""
    return next(r["name"] for r in realm.ok("quarter_directory", None, query=True)["quarters"] if r["id"] == realm.cid)


def pushes(capital):
    """The capital's records of its settings pushes, newest last: each the reply of one quarter."""
    records = rows(capital, "FederationMessage", "topic", "response")
    return [m["response"] for m in records if m["topic"] == SETTINGS_TOPIC]


def titles(realm):
    return [n["title"] for n in realm.ok("notification_list", "founder", query=True)["notifications"]]


def adopt_now(capital, built):
    """The capital adopts ``built`` (what ``quarter_add`` records, with the canister Casals built) at once, as its timer
    does once Casals reports the canister ready."""
    out = capital.shell(ADOPT % (built,))
    assert "Traceback" not in out, out


def pending(capital):
    return capital.ok("scale_status", None, query=True)["pending"]


def test_a_quarter_takes_the_realms_face_at_its_first_sync_and_keeps_its_own_label_and_local_settings(federation):
    capital, north, south = federation
    for quarter in (north, south):
        seen = info(quarter)
        currency = (seen["accounting_currency"], seen["accounting_currency_decimals"])
        assert (seen["name"], currency) == ("capital", ("AGR", 2))
        assert label(quarter) == quarter.name  # the label the capital registered it under
    assert (info(north)["welcome_message"], info(south)["welcome_message"]) == ("Benvinguts", "Hola")


def test_a_realm_with_another_currency_cannot_join(net, federation):
    capital = federation[0]
    outsider = net.realm("outsider")
    for code, decimals in (("EUR", 2), ("AGR", 8)):
        outsider.ok("realm_configure_tokens", "ctl", accounting_currency=code, accounting_currency_decimals=decimals)
        out = outsider.call("quarter_join_federation", {"capital": capital.cid}, sender=net.people["ctl"])
        assert not out["ok"] and out["kind"] == "conflict", out
        assert "change this realm's accounting currency first" in out["error"]


def test_a_realm_wide_vote_changes_the_realms_face_and_both_quarters_show_it(net, federation):
    capital, north, south = federation
    identity = {"name": "Àgora", "manifesto": "Una sola ciutat", "terms": "Respecta els veïns"}
    capital.govern("realm_configure", identity)
    capital.govern("realm_configure_branding", {"primary_color": "#1E6B52"})
    capital.govern("realm_configure_tokens", {"accounting_currency": "AGO", "accounting_currency_decimals": 2})
    capital.ok("realm_configure_branding", "ctl", logo_url=LOGO)  # too big to file in a proposal: set directly
    net.wait(5)
    for quarter in (north, south):
        seen = info(quarter)
        assert {k: seen[k] for k in identity} == identity
        assert (seen["primary_color"], seen["accounting_currency"], seen["logo_url"]) == ("#1E6B52", "AGO", LOGO)
        assert label(quarter) == quarter.name
    assert info(capital)["welcome_message"] == "Benvinguts" and info(north)["welcome_message"] == "Benvinguts"
    bodies = [m["body"] for m in rows(capital, "FederationMessage", "topic", "body") if m["topic"] == SETTINGS_TOPIC]
    assert len(bodies[-1]["images"]["logo_url"]) == 64  # an image travels as its digest, never whole


def test_a_quarter_that_is_down_holds_up_no_other_and_catches_up_at_its_next_sync(net, federation):
    capital, north, south = federation
    south.stop()
    before = len(pushes(capital))
    capital.govern("realm_configure", {"name": "Àgora Nova"})
    net.wait(5)
    assert info(north)["name"] == "Àgora Nova" and info(capital)["name"] == "Àgora Nova"
    replies = {cid: reply for record in pushes(capital)[before:] for cid, reply in record.items()}
    assert replies[north.cid]["ok"] and not replies[south.cid]["ok"], replies  # each quarter's reply, the failure too
    south.start()
    assert info(south)["name"] == "Àgora"
    net.wait(3600)  # its catch-up sync, every hour by default
    assert info(south)["name"] == "Àgora Nova"


def test_an_image_the_quarter_cannot_read_holds_up_none_of_the_rest_and_an_older_quarter_is_a_conflict(net, federation):
    capital, north, south = federation
    assert "Traceback" not in capital.shell(NO_IMAGE_READ)
    capital.ok("realm_configure_branding", "ctl", logo_url="data:image/png;base64,QUJD", primary_color="#2A2A2A")
    capital.ok("realm_configure", "ctl", name="Àgora d'imatges")
    net.wait(5)
    assert "Traceback" not in capital.shell(IMAGE_READ)
    seen = info(north)
    assert (seen["name"], seen["primary_color"], seen["logo_url"]) == ("Àgora d'imatges", "#2A2A2A", LOGO)
    told = [m["response"][north.cid] for m in rows(capital, "FederationMessage", "topic", "response")
            if m["topic"] == SETTINGS_TOPIC and north.cid in m["response"]][-1]
    assert told["ok"] and "images" not in told["configured"] and "realm_image" in told["images_error"], told
    north.ok("quarter_sync", "ctl")  # it kept its old digest, so the next sync reads the image
    assert info(north)["logo_url"] == "data:image/png;base64,QUJD"

    assert "Traceback" not in south.shell(OLD_QUARTER)
    capital.ok("realm_configure", "ctl", manifesto="Una sola ciutat, sempre")
    net.wait(5)
    assert "Traceback" not in south.shell(NEW_QUARTER)
    told = [m["response"][south.cid] for m in rows(capital, "FederationMessage", "topic", "response")
            if m["topic"] == SETTINGS_TOPIC and south.cid in m["response"]][-1]
    assert told["kind"] == "conflict" and "upgrade it to this release first" in told["error"], told
    south.ok("quarter_sync", "ctl")


def test_at_the_most_a_realm_keeps_the_images_and_texts_reach_the_quarters_and_a_realm_can_join(net, federation):
    capital, north, south = federation
    images = {k: f"data:image/png;base64,{k[0]}" + "B" * (IMAGE_MAX - 23) for k in ("logo_url", "background_image_url")}
    for name, value in images.items():
        capital.ok("realm_configure_branding", "ctl", **{name: value})  # one at a time: an ingress message is 2 MiB too
    capital.ok("realm_configure", "ctl", manifesto="à" * TEXT_MAX, terms="é" * TEXT_MAX)

    def arrived(quarter):
        seen = info(quarter)
        return {k: seen[k] for k in images} == images and len(seen["terms"]) == TEXT_MAX

    # The texts are written first and each image is read in a call of its own: an image this size takes many rounds
    net.wait_until(lambda: arrived(north) and arrived(south), step=5)
    joiner = net.realm("joiner")
    joiner.ok("quarter_join_federation", "ctl", capital=capital.cid)  # it reads the capital's realm_info
    capital.ok("quarter_register", "ctl", quarter=joiner.cid, name="Joiner")
    joiner.ok("quarter_sync", "ctl")
    assert info(joiner)["logo_url"] == images["logo_url"]
    capital.ok("quarter_deregister", "ctl", quarter=joiner.cid)
    capital.ok("realm_configure_branding", "ctl", logo_url=LOGO, background_image_url="")
    capital.ok("realm_configure", "ctl", manifesto="Una sola ciutat, i prou", terms="Respecta els veïns")
    net.wait_until(lambda: all(info(q)["logo_url"] == LOGO for q in (north, south)), step=5)


def test_a_push_that_does_not_come_from_the_capital_is_refused(net, federation):
    _, north, _ = federation
    for who in ("ctl", "alice"):
        out = north.call("federation_message", {"topic": SETTINGS_TOPIC, "body": {"name": "Fake", "status": "beta"}},
                         sender=net.people[who])
        assert not out["ok"], out
    assert info(north)["name"] == "Àgora d'imatges"


def test_a_quarter_refuses_a_synced_setting_before_any_vote_and_still_takes_its_local_ones(net, federation):
    capital, north, _ = federation
    north.ok("department_set_policy", "ctl", department="Administration", m=1, n=2)  # a call becomes a proposal
    filed = len(north.ok("proposal_list", "founder", query=True)["proposals"])
    for op, args in (("realm_configure", {"name": "North Town", "welcome_message": "Hi"}),
                     ("realm_configure_branding", {"primary_color": "#000000"}),
                     ("realm_configure_tokens", {"accounting_currency": "EUR"})):
        out = north.call(op, args, sender=net.people["ctl"])
        assert not out["ok"] and out["kind"] == "forbidden" and capital.cid in out["error"], (op, out)
    assert len(north.ok("proposal_list", "founder", query=True)["proposals"]) == filed
    assert north.ok("realm_configure", "ctl", welcome_message="Hi, north")["requires_vote"]
    north.govern("department_set_policy", {"department": "Administration", "m": 1, "n": 1})
    north.ok("realm_configure", "ctl", welcome_message="Hi, north")
    assert info(north)["welcome_message"] == "Hi, north" and info(capital)["welcome_message"] == "Benvinguts"


def test_the_voting_rules_are_not_pushed(net, federation):
    capital, north, south = federation
    before = {q.name: json.loads(q.shell(GOVERNANCE)) for q in (north, south)}
    capital.ok("governance_update", "ctl", voting_window=3600, voting_threshold=60_000_000, voting_quorum=10_000_000)
    capital.ok("realm_configure", "ctl", manifesto="Una sola ciutat, i prou")  # a push follows
    net.wait(5)
    assert info(north)["manifesto"] == "Una sola ciutat, i prou"
    assert {q.name: json.loads(q.shell(GOVERNANCE)) for q in (north, south)} == before
    capital.ok("governance_update", "ctl", voting_window=0, voting_threshold=50_000_000, voting_quorum=0)


def test_the_welcome_message_is_each_quarters_own_and_the_realm_can_set_it(net, federation):
    capital, north, south = federation
    filed = len(capital.ok("proposal_list", "founder", query=True)["proposals"])
    out = capital.call("quarter_configure", {"welcome_message": "Hola a tothom"}, sender=net.people["ctl"])
    assert not out["ok"] and out["kind"] == "invalid_argument" and "name it with quarter" in out["error"]
    assert len(capital.ok("proposal_list", "founder", query=True)["proposals"]) == filed
    capital.govern("quarter_configure", {"quarter": south.cid, "welcome_message": "Benvinguts al sud"})
    assert info(south)["welcome_message"] == "Benvinguts al sud"
    capital.ok("realm_configure", "ctl", welcome_message="Benvinguts de nou")
    net.wait(5)
    assert [info(q)["welcome_message"] for q in (north, south)] == ["Hi, north", "Benvinguts al sud"]


def test_a_federal_vote_on_the_name_takes_effect_on_the_capital_and_reaches_the_quarters_by_the_push(net, federation):
    capital, north, south = federation
    vote = capital.ok("federal_vote_propose", "founder", title="rename",
                      action={"op": "realm_configure", "args": {"name": "Àgora Federal"}})["vote"]
    for realm in federation:
        (leg,) = [r for r in rows(realm, "FederalVoteLeg", "id", "proposal_id") if r["id"] == f"{vote}:{realm.cid}"]
        for member in realm.members:
            if realm.proposal(leg["proposal_id"])["status"] == "voting":
                realm.ok("proposal_vote", member, proposal=leg["proposal_id"], choice="yes")
    for quarter in (north, south):
        assert quarter.ok("federal_vote_finalize", "founder", vote=vote)["reported"]
    assert capital.ok("federal_vote_finalize", "founder", vote=vote)["status"] == "passed"
    net.wait(5)
    for quarter in (north, south):
        assert info(quarter)["name"] == "Àgora Federal"
        (told,) = [m["response"] for m in rows(quarter, "FederationMessage", "topic", "response")
                   if m["topic"] == "federal_vote.result"]
        assert told["result"]["skipped"], told  # not replayed, so no Forbidden


# ---- A capital in setup and its quarter: the stage


@pytest.fixture(scope="module")
def founding(net, people):
    """``chief``, a capital still in setup, and ``harbour``, its quarter, also in setup, with a legacy codex that tells
    its founder each stage it enters."""
    chief, harbour = net.realm("chief", launch=False), net.realm("harbour", launch=False)
    manifest = {"id": "stages", "version": "1.0.0", "codex_api_version": 2, "hooks": ["on_stage_change"],
                "capabilities": ["notification_send"]}
    harbour.ok("codex_install", "ctl", manifest=manifest, source=STAGE_HOOK % people["founder"])
    harbour.ok("quarter_join_federation", "ctl", capital=chief.cid)
    chief.ok("quarter_register", "ctl", quarter=harbour.cid, name="Harbour")
    return chief, harbour


def test_a_quarter_launches_on_its_capitals_stage_once_it_knows_it_and_the_capital_has_launched(net, founding):
    chief, harbour = founding
    out = harbour.call("setup_launch", {}, sender=net.people["founder"])
    assert out["kind"] == "conflict" and "run quarter_sync first" in out["error"], out
    harbour.ok("quarter_sync", "ctl")
    assert info(harbour)["name"] == "chief"
    out = harbour.call("setup_launch", {}, sender=net.people["founder"])
    assert out["kind"] == "conflict" and "still in setup" in out["error"], out
    out = harbour.call("setup_step", {"step": "realm", "name": "Mine"}, sender=net.people["founder"])
    assert out["kind"] == "forbidden" and chief.cid in out["error"], out
    harbour.ok("setup_step", "founder", step="realm", open_registration=True)

    chief.ok("setup_launch", "founder")  # it pushes its stage, as any change of a synced setting
    net.wait(5)
    assert (info(chief)["status"], info(harbour)["status"]) == ("alpha", "setup")  # a push does not end a setup
    assert harbour.ok("setup_launch", "founder")["status"] == "alpha"  # the capital's, learned from its push
    net.wait(2)
    assert titles(harbour) == ["stage alpha"]  # setup_launch's hook gets the stage the quarter took


def test_a_pushed_stage_change_runs_on_stage_change_in_the_quarter(net, founding):
    chief, harbour = founding
    out = chief.shell("from ggg import Realm\nfrom api import quarters\nRealm['1'].status = 'beta'\n"
                      "quarters.push_settings()")
    assert "Traceback" not in out, out
    net.wait(5)
    assert info(harbour)["status"] == "beta" and titles(harbour) == ["stage alpha", "stage beta"]


# ---- Quarters the capital builds


def test_adopt_makes_a_built_quarter_one_with_the_realms_name_its_own_label_and_the_capitals_registration(
        net, federation):
    capital = federation[0]
    built = net.realm("built", launch=False)
    built.add_controller(capital.cid)  # as Casals does for the capital that asked for it
    adopt_now(capital, {"member": "capital-quarter-3", "name": "Harbour", "canister": built.cid})
    net.wait_until(lambda: pending(capital) is None, rounds=2)
    seen = info(built)
    assert (seen["name"], seen["open_registration"], seen["status"]) == ("Àgora Federal", True, info(capital)["status"])
    assert label(built) == "Harbour"


def test_adopt_retries_a_failed_first_sync_and_then_launches(net, federation):
    capital = federation[0]
    flaky = net.realm("flaky", launch=False)
    flaky.add_controller(capital.cid)
    flaky.ok("quarter_join_federation", "ctl", capital=capital.cid)
    flaky.ok("setup_step", "ctl", step="realm", open_registration=True)
    capital.ok("quarter_register", "ctl", quarter=flaky.cid, name="Flaky")
    flaky.stop()
    adopt_now(capital, {"member": "capital-quarter-4", "name": "Flaky", "canister": flaky.cid,
                        "done": ["join", "configure", "register"]})
    net.wait(5)
    left = pending(capital)
    assert left["done"] == ["join", "configure", "register"] and left["error"], left
    flaky.start()
    net.wait(ADOPT_EVERY + 5)
    assert pending(capital) is None
    assert (info(flaky)["name"], info(flaky)["status"]) == ("Àgora Federal", info(capital)["status"])


# ---- Leaving


def test_a_quarter_that_secedes_keeps_its_last_values_and_can_change_them(federation):
    _, north, _ = federation
    north.ok("quarter_secede", "ctl")
    assert info(north)["name"] == "Àgora Federal"
    north.ok("realm_configure", "ctl", name="North Town")
    assert info(north)["name"] == "North Town"
