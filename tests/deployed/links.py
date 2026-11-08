"""Several realms on one PocketIC instance, for flows that cross canisters: links between realms, quarters, installs
that fetch from a sync source. See README.md in this folder.

    net = Network(wasm_of("production"), people)
    capital, north = net.realm("capital"), net.realm("north", members=("alice",))
    net.register_quarters(capital, north)
    proposal = capital.govern("quarter_configure", {"quarter": north.cid, "name": "North"}, effective_at=...)
    net.wait(3600)

The wasm is uploaded once, and time, timers and HTTPS outcalls (``net.web``) belong to the instance, not to a realm.
"""

from outcalls import Web
from pic import Canister, Wasm, new_pocket_ic

ACCEPTED = ("executed", "execution_scheduled")


class Network:
    def __init__(self, wasm_path, people):
        self.pic = new_pocket_ic()
        self.wasm = Wasm(self.pic, wasm_path)
        self.people = people
        self.web = Web(self.pic)

    def realm(self, name, members=("alice", "bob")):
        """A launched, open realm called ``name`` whose members are the founder and ``members``, ready to vote: a
        proposal closes as soon as its outcome is certain."""
        return Realm(self, name, members)

    def register_quarters(self, capital, *quarters):
        """Make each of ``quarters`` a quarter of ``capital``, the way a federation is built (api.quarters.adopt), then
        let each sync again so that every quarter knows all the others."""
        for quarter in quarters:
            quarter.ok("quarter_join_federation", "ctl", capital=capital.cid)
            capital.ok("quarter_register", "ctl", quarter=quarter.cid, name=quarter.name)
            quarter.ok("quarter_sync", "ctl")
        for quarter in quarters:
            quarter.ok("quarter_sync", "ctl")

    def tick(self):
        """One round on every canister, then the answers to the HTTPS requests it made."""
        self.pic.tick()
        self.web.answer()

    def wait(self, seconds, rounds=10):
        """Let ``seconds`` pass on the instance's clock and run what falls due, timers and the calls they make."""
        self.pic.advance_time(seconds * 10**9)
        for _ in range(rounds):
            self.tick()

    def wait_until(self, reached, step=1, rounds=1, limit=86400):
        """Let time pass ``step`` seconds and ``rounds`` rounds at a time until ``reached()``, so that a flow of timer
        steps can be stopped between two of them."""
        waited = 0
        while not reached():
            assert waited < limit, f"not reached in {limit} seconds"
            self.wait(step, rounds)
            waited += step

    def upgrade_when(self, realm, reached, **pace):
        """Run the flow until ``reached()``, then upgrade ``realm`` in place right there: its timers are gone until
        post_upgrade and its calls in flight are lost, as in a real upgrade. ``pace`` is ``wait_until``'s."""
        self.wait_until(reached, **pace)
        realm.upgrade()


class Realm(Canister):
    def __init__(self, network, name, members):
        super().__init__(None, installer=network.people["installer"], pic=network.pic, wasm=network.wasm)
        self.network, self.name = network, name
        self.cid = self.id.to_str()
        self.members = ("founder",) + tuple(members)
        self.ok("setup_step", "installer", step="install", network="local", founder=network.people["founder"])
        self.ok("setup_step", "founder", step="realm", name=name, open_registration=True)
        self.ok("setup_step", "founder", step="trust_policy", require_marketplace_approval=False)
        self.ok("setup_launch", "founder")
        for member in members:
            self.ok("realm_join", member)
        self.ok("governance_update", "ctl", voting_window=0)

    def ok(self, op, who, query=False, **args):
        """Run ``op`` as ``who``, a name in ``people`` or a principal, and expect it to succeed."""
        out = self.call(op, args, sender=self.network.people.get(who, who), query=query)
        assert out["ok"], (self.name, op, out)
        return out

    def wait(self, seconds):
        """The instance's clock is shared, and so are its HTTPS outcalls: wait on the network."""
        self.network.wait(seconds)

    def proposal(self, proposal_id):
        return self.ok("proposal_get", "alice", query=True, proposal=proposal_id)["proposal"]

    def govern(self, op, args, by="alice", effective_at=None):
        """Run ``op`` with ``args`` as the realm, by a vote: ``by`` files the proposal, every member votes yes, and it is
        finalized if the vote is still open. Returns the proposal id once it is executed, or scheduled for
        ``effective_at`` (then ``run_at_effective_at`` runs it)."""
        filing = {"title": f"{op} in {self.name}", "action": {"op": op, "args": args}}
        if effective_at:
            filing["effective_at"] = effective_at
        proposal_id = self.ok("proposal_create", by, **filing)["proposal"]
        for member in self.members:
            if self.proposal(proposal_id)["status"] != "voting":
                break
            self.ok("proposal_vote", member, proposal=proposal_id, choice="yes")
        if self.proposal(proposal_id)["status"] == "voting":
            self.ok("proposal_finalize", "ctl", proposal=proposal_id)
        proposal = self.proposal(proposal_id)
        assert proposal["status"] in ACCEPTED, proposal
        return proposal_id

    def run_at_effective_at(self, proposal_id):
        """Let time pass until a scheduled proposal runs, and return it, executed."""
        scheduled = self.proposal(proposal_id)
        self.network.wait(scheduled["effective_at"] + 1 - self.now())
        proposal = self.proposal(proposal_id)
        assert proposal["status"] == "executed", proposal
        return proposal
