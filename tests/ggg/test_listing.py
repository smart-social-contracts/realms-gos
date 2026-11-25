"""What the realm tells the outside: its counts, and its listing in a realm registry (realms-gos#736, #119)."""

import json

import pytest

import fake_chain
import world
from api import listing, quarters, setup
from ggg import Member, Quarter, Realm, Registry
from core import op
from kit import call, denied, later, ok

REGISTRY = "realm-registry"
TAKEN = {"success": True, "id": fake_chain.SELF, "revision": 0}


@pytest.fixture(autouse=True)
def fresh_heap():
    """The look is armed once per heap: each test is a canister that has just started."""
    listing._timer["armed"] = False


def sent(registry):
    return [json.loads(arg) for arg in registry.sent("listing_publish")]


def fire(picked):
    """Run the timers ``picked`` chooses, and keep the others armed: a test's clock moves by ``later``, not by the timers."""
    timers = fake_chain.state["timers"]
    fake_chain.state["timers"] = [t for t in timers if not picked(t)]
    for _, cb in [t for t in timers if picked(t)]:
        out = cb()
        if out is not None:
            op.run(out)


def publish_now():
    """The publish that a launch or the host's word sets going, which runs at once."""
    fire(lambda timer: timer[1] is listing.publish)


def next_look():
    """A day passes and the daily look runs; it arms the next."""
    later(listing.DAY)
    fire(lambda timer: timer[1] is listing.look)


@pytest.fixture
def registry():
    """A launched realm called Agora, and a registry that takes every listing."""
    world.build()
    ok("setup_step", "ctl", step="realm", name="Agora")
    ok("setup_launch", "ctl")
    publish_now()  # the launch's own publish: no registry is told yet
    return fake_chain.peer(REGISTRY, listing_publish=TAKEN)


def host_says():
    """The host names the registry; the realm's publish, which it sets going, runs."""
    ok("host_registry", "ctl", registry=REGISTRY)
    publish_now()


# ---- the realm's own counts (realms-gos#119)


def test_the_realm_counts_its_members_its_proposals_and_its_votes_and_anyone_may_ask():
    world.build()
    first = ok("proposal_create", "alice", title="Bike lane")["proposal"]
    ok("proposal_create", "bob", title="Market day")
    for voter, choice in (("alice", "yes"), ("bob", "no"), ("dana", "yes")):
        ok("proposal_vote", voter, proposal=first, choice=choice)
    out = call("realm_numbers", fake_chain.ANONYMOUS)
    assert out == {"ok": True, "members": quarters.population(), "proposals": 2, "votes": 3}
    assert out["members"] > 0


def test_the_members_of_a_federation_are_those_of_every_quarter():
    world.build()
    own = quarters.population()
    quarter = Quarter(id="north", index=1, population=40, told_population=40, status="active")
    assert ok("realm_numbers", "ctl")["members"] == own + 40
    quarter.delete()


# ---- publishing


def test_a_realm_told_its_registry_publishes_what_it_holds(registry):
    host_says()
    (first,) = sent(registry)
    assert first == {"name": "Agora", "users_count": quarters.population(), "stage": "alpha",
                     "last_activity_at": fake_chain.now()}
    assert Registry[REGISTRY].principal_id == REGISTRY
    told = listing.kept()[REGISTRY]
    assert told["sent"] == {"name": "Agora", "users_count": quarters.population(), "stage": "alpha"}
    assert told["error"] == ""


def test_an_idle_realm_makes_no_call_however_many_looks_pass(registry):
    host_says()
    for _ in range(5):
        next_look()
    assert len(sent(registry)) == 1


def test_a_realm_whose_members_changed_tells_the_registry_once_at_the_next_look(registry):
    host_says()
    before = quarters.population()
    ok("invite_create", "dana", profile="member", code="X-1", max_uses=1)
    ok("realm_join", "zoe", code="X-1")
    publish_now()
    assert len(sent(registry)) == 1  # a join publishes nothing: the daily look does
    next_look()
    first, second = sent(registry)
    assert (first["users_count"], second["users_count"]) == (before, before + 1)
    assert second["last_activity_at"] > first["last_activity_at"]
    next_look()
    assert len(sent(registry)) == 2


def test_the_clock_alone_never_makes_a_realm_publish(registry):
    """``last_activity_at`` moves only with a change: it is not one of the figures compared."""
    host_says()
    later(30 * listing.DAY)
    next_look()
    assert len(sent(registry)) == 1


def test_a_registry_that_refuses_is_told_again_at_the_next_look_and_what_it_said_is_kept(registry):
    registry.answer("listing_publish", {"success": False, "error": "Register the realm before publishing its listing"})
    host_says()
    assert "Register the realm" in listing.kept()[REGISTRY]["error"] and "sent" not in listing.kept()[REGISTRY]
    registry.answer("listing_publish", TAKEN)
    next_look()
    assert len(sent(registry)) == 2 and listing.kept()[REGISTRY]["error"] == ""
    assert "sent" in listing.kept()[REGISTRY]


def test_a_registry_that_does_not_answer_does_not_stop_the_realm_or_its_next_look():
    world.build()
    ok("setup_step", "ctl", step="realm", name="Agora")
    ok("setup_launch", "ctl")
    publish_now()
    gone = fake_chain.peer(REGISTRY)  # no listing_publish: the call is rejected
    host_says()
    assert "no method" in listing.kept()[REGISTRY]["error"]
    gone.answer("listing_publish", TAKEN)
    next_look()
    assert listing.kept()[REGISTRY]["error"] == "" and len(sent(gone)) == 2


def test_the_logo_is_published_only_as_an_https_url_the_registry_takes(registry):
    for logo, published in (("data:image/png;base64,AAAA", False), ("https://x.icp0.io/logo.png", True),
                            ("https://x.icp0.io/" + "a" * 600, False), ("http://x.icp0.io/logo.png", False)):
        Realm["1"].logo_url = logo
        assert ("logo" in listing.figures()) is published, logo
    Realm["1"].logo_url = "https://x.icp0.io/logo.png"
    host_says()
    assert sent(registry)[0]["logo"] == "https://x.icp0.io/logo.png"


def test_a_name_longer_than_the_registry_takes_is_cut_to_what_it_takes(registry):
    Realm["1"].name = "A" * 300
    assert len(listing.figures()["name"]) == listing.NAME_MAX


def test_the_registry_named_by_ggg_s_host_install_is_listed_in_too():
    """A host that is not the GaaS installer (Casals) names the registry in ``host_install``, and needs no other call."""
    world.build()
    taken = fake_chain.peer(REGISTRY, listing_publish=TAKEN)
    ok("host_install", "ctl", founder="alice", network="local", registry=REGISTRY, test_mode=False)
    ok("setup_step", "ctl", step="realm", name="Agora")
    ok("setup_launch", "ctl")
    publish_now()
    assert [row["name"] for row in sent(taken)] == ["Agora"]


def test_a_launch_publishes_to_the_registry_the_host_named_during_setup():
    world.build()
    ok("setup_step", "ctl", step="realm", name="Agora")
    taken = fake_chain.peer(REGISTRY, listing_publish=TAKEN)
    ok("host_registry", "ctl", registry=REGISTRY)
    publish_now()
    assert sent(taken) == [] and listing.kept() == {}  # in setup the registry has no stage to take
    ok("setup_launch", "ctl")
    publish_now()
    assert [row["stage"] for row in sent(taken)] == ["alpha"]


def test_a_quarter_publishes_nothing_its_capital_is_the_listing(registry, monkeypatch):
    host_says()
    monkeypatch.setattr(quarters, "federated", lambda: True)
    Realm["1"].name = "Agora North"
    next_look()
    assert len(sent(registry)) == 1


def test_a_realm_registered_in_a_registry_keeps_its_listing_there_too():
    world.build()
    ok("setup_step", "ctl", step="realm", name="Agora")
    ok("setup_launch", "ctl")
    taken = fake_chain.peer(REGISTRY, register_realm='(variant { Ok = "registered" })', listing_publish=TAKEN)
    ok("realm_register", "ctl", registry=REGISTRY)
    publish_now()
    assert len(sent(taken)) == 1


# ---- who may tell the realm its registry


def test_only_the_host_tells_the_realm_its_registry_and_it_must_be_a_canister_id(registry):
    denied("host_registry", "alice", registry=REGISTRY)
    assert "canister id" in call("host_registry", "ctl", registry="not a canister")["error"]
    Realm["1"].installer_canister_id = "installer"
    ok("host_registry", "installer", registry=REGISTRY)
    assert Registry[REGISTRY] is not None


# ---- timers do not survive an upgrade


def test_the_daily_look_is_armed_when_the_realm_starts_and_only_once_per_start():
    world.build()
    fake_chain.state["timers"].clear()
    listing._timer["armed"] = False
    setup.start()
    setup.start()  # a second start in the same heap arms nothing more
    looks = [secs for secs, cb in fake_chain.state["timers"] if cb is listing.look]
    assert len(looks) == 1 and listing.FIRST <= looks[0] < listing.FIRST + listing.SPREAD


def test_an_upgrade_clears_the_heap_and_the_next_start_arms_the_look_again(registry):
    host_says()
    fake_chain.state["timers"].clear()  # an upgrade: the timers are gone
    listing._timer["armed"] = False  # and so is the heap
    setup.start()
    assert [cb for _, cb in fake_chain.state["timers"] if cb is listing.look]
    # and the look it armed is the one that publishes what changed since
    ok("invite_create", "dana", profile="member", code="X-2", max_uses=1)
    ok("realm_join", "yan", code="X-2")
    fire(lambda timer: timer[1] is listing.look)
    assert sent(registry)[-1]["users_count"] == Member.count_by("status", "active")


def test_each_look_arms_the_next_before_it_publishes(registry):
    host_says()
    fake_chain.state["timers"].clear()
    listing._timer["armed"] = False
    fake_chain.peer(REGISTRY, listing_publish=lambda arg: (_ for _ in ()).throw(Exception("down")))
    listing.arm(1)
    fire(lambda timer: timer[1] is listing.look)  # the look publishes to a registry that is down, which it survives
    assert [secs for secs, cb in fake_chain.state["timers"] if cb is listing.look] == [listing.DAY]
