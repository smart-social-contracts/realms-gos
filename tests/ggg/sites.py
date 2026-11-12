"""Several realms in one process, each with its own database and its own heap: a capital and its quarters, for the tests that
follow a change from the capital to a quarter. ``site.here()`` runs the code in it with the realm's id, database and heap
(what ``api`` keeps in module variables), and a call from one to another runs in the other's; the clock and the
controllers stay the ones of ``fake_chain``.

    capital, north = Site("capital"), Site("north", capital)
    capital.register(north)
    with capital.here():
        ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
    north.catch_up()
"""

import contextlib
import copy
import json

import fake_chain
import world
from api import assets, quarters, sweeps
from core import op
from fake_chain import Peer
from ggg import Quarter, Realm
from ic_python_db import Database
from kit import ok

# What a realm's heap holds that its database does not: a fresh heap, as after an upgrade.
FRESH = ((quarters._synced, {"since_start": False, "at": 0}), (quarters._scale, {"check": True}),
         (quarters._catch_up, {"armed": False}), (assets.census, {"held": None, "generation": 1, "taken": 0}),
         (sweeps._timer, {"armed": False}))


class Site:
    def __init__(self, cid, capital=None):
        self.cid, self.capital = cid, capital
        self.heap = [copy.deepcopy(fresh) for _, fresh in FRESH]
        self.db = Database(audit_enabled=False)
        self.db._entity_types = dict(Database.get_instance()._entity_types)
        with self.here():
            world.build()
            realm = Realm["1"]
            realm.status = "beta"
            if capital:
                realm.is_quarter, realm.is_capital, realm.federation_realm_id = True, False, capital.cid
                Quarter(id=capital.cid, name="Capital", index=0, status="active")
                fake_chain.state["peers"][capital.cid] = Peer(
                    capital.cid, {name: self.ask_capital(name) for name in ("quarter_sync", "realm_image")})

    @contextlib.contextmanager
    def here(self):
        """Run as this realm: its database, its id, its heap; the caller of an operation is the one given to it."""
        if Database._instance is self.db:  # already here: a call it makes to itself while it waits for another
            yield self
            return
        before = (Database._instance, fake_chain.SELF, fake_chain.state["caller"], [copy.deepcopy(d) for d, _ in FRESH])
        Database._instance, fake_chain.SELF = self.db, self.cid
        for (heap, _), mine in zip(FRESH, self.heap):
            heap.clear()
            heap.update(mine)
        try:
            yield self
        finally:
            self.heap = [copy.deepcopy(d) for d, _ in FRESH]
            Database._instance, fake_chain.SELF, fake_chain.state["caller"] = before[:3]
            for (heap, _), kept in zip(FRESH, before[3]):
                heap.clear()
                heap.update(kept)

    def restart(self):
        """An upgrade: what the realm's heap held is gone."""
        self.heap = [copy.deepcopy(fresh) for _, fresh in FRESH]

    def ask_capital(self, name):
        """What the capital answers to this quarter's call of ``name``, in the capital's own database."""
        def answer(args):
            asking = fake_chain.SELF
            with self.capital.here():
                fake_chain.state["caller"] = asking
                return op.run(op.dispatch(name, json.dumps(args)))
        return answer

    def register(self, quarter):
        """Make ``quarter`` (a Site) one of this capital's quarters, as the capital's controller does."""
        with self.here():
            ok("quarter_register", "ctl", quarter=quarter.cid, name=quarter.cid.title())

    def catch_up(self):
        """Run this quarter's catch-up once, as its timer does."""
        with self.here():
            op.run(quarters.catch_up())
            return (Realm["1"].sync_state or {}).get("catch_up_error", "")

    def taken(self):
        """What this realm holds of its capital's, apart from what is about itself (``own``) and the quarters it is not told
        about (itself): to compare a quarter that took the changes one at a time with one that took them all at once."""
        with self.here():
            realm = Realm["1"]
            state = realm.sync_state or {}
            return {"quarters": {q.id: [getattr(q, k) for k in quarters.NOTABLE] for q in quarters.known()},
                    "populations": {q.id: q.population for q in quarters.known()},
                    "zones": {z.id: (z.name, z.kind) for z in assets.defined_zones()},
                    "settings": {k: getattr(realm, k) for k in quarters.SYNCED},
                    "state": {k: state.get(k) for k in ("codex", "sync_every", "official_timezone", "stage", "sweep")}}

    def own(self):
        """What this quarter holds of itself: its label, index and the flag its capital gave it."""
        with self.here():
            state = Realm["1"].sync_state
            return {"name": state["label"], "index": state["index"], "compliance": state["compliance"]}

    def row(self):
        """This quarter's row at its capital."""
        with self.capital.here():
            q = Quarter[self.cid]
            return {"name": q.name, "index": q.index, "compliance": q.compliance}
