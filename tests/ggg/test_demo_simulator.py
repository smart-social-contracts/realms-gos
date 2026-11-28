"""The demo_simulator extension (extensions/extensions/demo_simulator) run by the realm as an installed extension: its
backend's source, through extension_call, with the operations its manifest declares and nothing else. It keeps its
settings in its own task, fills this canister with keyless members on that task's schedule, and stops at a quarter's
capacity."""

import json
from pathlib import Path

import fake_chain
import pytest
import world
from ggg import Extension, Member, Task, User
from ic.principal import Principal
import runpy

from kit import call, fire_timers, ok

EXT = Path(__file__).resolve().parents[2] / "extensions" / "extensions" / "demo_simulator"
if not (EXT / "backend" / "entry.py").exists():  # a checkout without the extensions submodule
    pytest.skip("the extensions submodule is not checked out", allow_module_level=True)
MANIFEST = json.loads((EXT / "manifest.json").read_text())
PERMS = ("extension.call", "realm.monitor", "task.create", "task.edit", "task.delete", "task.schedule", "user.add")


def installed():
    """Agora, on a test network (a quarter takes 10 members), with the extension installed as extension_install
    leaves it, and ada an administrator who may use it."""
    world.build()
    from ggg import Realm

    Realm["1"].network = "test"
    world.profile("admin", *PERMS)
    world.person("ada", "admin")
    Extension(id="demo_simulator", version=MANIFEST["version"], source=(EXT / "backend" / "entry.py").read_text(),
              operations=MANIFEST["operations"], access={"profiles": MANIFEST["profiles"], "departments": []},
              manifest=MANIFEST)


def sim(function, who="ada", **args):
    out = ok("extension_call", who, extension="demo_simulator", function=function, args=args)
    return out["result"]


def members():
    return Member.count_by("status", "active")


def simulated():
    """The users that are simulated members: a 29-byte principal ending in 0x02 whose bytes start with ``rsim``."""
    def made(uid):
        try:
            raw = Principal.from_str(uid).bytes
        except Exception:  # noqa: BLE001  a test world's users are named, not principals
            return False
        return len(raw) == 29 and raw[-1] == 2 and raw[:4] == b"rsim"
    return [u for u in User.load_some(1, 1000) if made(u.id)]


def test_the_manifest_declares_a_sandboxed_backend_and_no_host_import():
    assert MANIFEST["runtime"] == "sandboxed" and MANIFEST["backend"] is True
    source = (EXT / "backend" / "entry.py").read_text()
    assert not [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert sorted(p.name for p in (EXT / "backend").iterdir() if p.suffix == ".py") == ["entry.py"]


def test_a_batch_adds_keyless_members_with_valid_principals_until_the_quarter_is_full():
    installed()
    before = members()
    out = sim("run_once")
    assert out["added"] == 10 - before and out["status"] == "ok" and members() == 10  # a test quarter's capacity
    made = simulated()
    assert len(made) == 10 - before and len({u.id for u in made}) == len(made)
    assert all(" " in u.nickname for u in made)  # a first and a last name
    assert sim("run_once")["status"] == "full" and members() == 10


def test_a_target_above_the_capacity_and_the_batch_size_are_kept():
    installed()
    sim("update_config", batch_size=7, target=30, interval_seconds=2)
    status = sim("get_status")
    assert (status["batch_size"], status["target"], status["interval_seconds"], status["running"]) == (7, 30, 2, False)
    before = members()
    assert sim("run_once")["added"] == 7 and members() == before + 7


def test_the_schedule_runs_batches_until_the_target_then_adds_nothing():
    installed()
    sim("update_config", batch_size=4, target=25, interval_seconds=3)
    assert sim("toggle", enabled=True)["running"]
    t = Task["demo_simulator"]
    assert t is not None and [s.repeat_every for s in t.schedules] == [3]
    for _ in range(10):  # each firing of the schedule is a batch
        fake_chain.state["now"] += 3
        fire_timers()
        if members() >= 25:
            break
    assert members() == 25
    fake_chain.state["now"] += 3
    fire_timers()
    assert members() == 25
    status = sim("get_status")
    assert status["running"] and status["population"] == 25 and status["batch_size"] == 4
    assert not sim("toggle", enabled=False)["running"] and not sim("get_status")["running"]
    assert sim("get_status")["batch_size"] == 4  # a stopped simulator keeps its settings


def test_a_member_is_named_by_the_canister_the_seed_and_the_population_when_added():
    installed()
    sim("update_config", batch_size=3, seed=7, target=30)
    before = members()
    sim("run_once")
    entry = runpy.run_path(str(EXT / "backend" / "entry.py"))
    expected = {entry["simulated"](fake_chain.SELF, 7, before + i) for i in range(3)}
    assert {u.id for u in simulated()} == expected


def test_only_an_administrator_uses_it_and_it_calls_only_what_it_declares():
    installed()
    world.person("bob")
    assert "not available to you" in call("extension_call", "bob", extension="demo_simulator",
                                          function="get_status", args={})["error"]
    assert "proposal_create" not in MANIFEST["operations"] and "user_add" not in MANIFEST["operations"]
