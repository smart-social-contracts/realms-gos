import json
import sys

from core import auth
from core import chain
from core.db import Fail

OPS = {}
MODULES = ("assets", "codex", "crypto", "delegation", "extensions", "federation", "justice", "licenses", "members",
           "notifications", "orgs", "procurement", "proposals", "quarters", "setup", "tasks", "taxes", "treasury")
SECRET = {"code", "private_data", "payload", "ciphertext"}
EVENTS_KEPT = 2000
# Basilisk stubs the `types` module.
GENERATOR = type((lambda: (yield))())


class Call:
    """``user`` is who acts; ``by`` signed the message (the user, or their delegate); ``proposal`` is the
    accepted proposal whose replay this is, if any."""

    def __init__(self, user, by, origin, proposal=""):
        self.user, self.by, self.origin, self.proposal = user, by, origin, proposal

    @property
    def delegated(self):
        return self.user != self.by


def op(perm, read=False, delegable=False, governed=None, signing=False, parties=None, public=False, perm_for=None):
    """Register an operation.

    governed(args) -> the Department whose policy governs the call; a policy that is not direct turns
    the call into a proposal. parties(args) -> principals with a stake, who may not perform it.
    perm_for(args) -> the permission this call needs, when it depends on the arguments (``perm`` otherwise).
    """
    def wrap(fn):
        OPS[fn.__name__] = {"fn": fn, "perm": perm, "read": read, "delegable": delegable, "governed": governed,
                            "signing": signing, "parties": parties, "public": public, "perm_for": perm_for}
        return fn
    return wrap


def ops():
    """Every registered operation. Read this, not OPS, when looking an operation up by name."""
    # Basilisk imports modules lazily and runs one only when a name it has not yet defined is read.
    for name in MODULES:
        __import__(f"api.{name}")
        getattr(sys.modules[f"api.{name}"], "op", None)
    return OPS


def dispatch(fn, arg, origin="user", signer=None, declared=(), query=False, proposal=""):
    """Run an operation; ``query`` when called through the ``read`` endpoint, whose changes are discarded."""
    name = fn if isinstance(fn, str) else fn.__name__
    spec = OPS.get(name) or ops().get(name)
    if spec is None:
        return json.dumps({"ok": False, "error": f"unknown operation '{name}'"})
    if query and not spec["read"]:
        return json.dumps({"ok": False, "error": f"{name} changes the realm: call it with run, not read"})
    try:
        args = json.loads(arg or "{}") if isinstance(arg, str) else dict(arg)
        by = signer or chain.caller()
        user = args.pop("on_behalf_of", None) or by
        auth.authorize(user, by, name, spec, args, origin, declared)
        governing = spec["governed"](args) if spec["governed"] and origin not in ("replay", "codex") else None
        if governing is not None and not direct(governing):
            from api import proposals

            out = proposals.file_replay(name, args, user, by, governing)
        else:
            out = spec["fn"](Call(user, by, origin, proposal), args)
            if isinstance(out, GENERATOR):
                out = yield from out
            out = out or {}
        if not spec["read"]:
            audit(name, user, by, origin, args, out)
        return json.dumps({"ok": True, **out})
    except (Fail, auth.Denied) as e:
        return json.dumps({"ok": False, "error": str(e)})
    except KeyError as e:
        return json.dumps({"ok": False, "error": f"missing {e}"})
    except Exception as e:
        chain.trap(f"{name}: {e}")


def direct(department):
    p = department.policy or {}
    return int(p.get("m", 1)) <= 1 and int(p.get("n", 1)) <= 1 and not p.get("quorum_percent") and not p.get("veto")


def audit(name, user, by, origin, args, out):
    from ggg import Event

    kept = {k: v for k, v in args.items() if k not in SECRET}
    if len(json.dumps(kept)) > 2000:
        kept = {"keys": sorted(kept)}
    Event(id=str(Event.max_id() + 1), op=name, user=user, by=by, origin=origin, args=kept,
          result={k: v for k, v in out.items() if k not in SECRET and isinstance(v, (str, int, bool))}, at=chain.now())
    old = Event[str(Event.max_id() - EVENTS_KEPT)]
    if old:
        old.delete()


def run(gen):
    try:
        next(gen)
    except StopIteration as done:
        return done.value
    raise RuntimeError("a query cannot call other canisters")


def call(name, args=None, user=None, origin="user"):
    """Run an operation from Python (REPL, codex effects, replays) and return its result as a dict."""
    return json.loads(run(dispatch(name, json.dumps(args or {}), origin=origin, signer=user)))
