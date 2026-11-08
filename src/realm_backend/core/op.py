import json
import sys

from core import auth
from core import chain
from core import poa
from core import ranges
from core.db import Fail

OPS = {}
MODULES = ("assets", "codex", "crypto", "delegation", "extensions", "external", "federation", "installs", "justice", "licenses",
           "members", "notifications", "orgs", "procurement", "proposals", "quarters", "requests", "setup", "tasks",
           "taxes", "treasury")
SECRET = {"code", "private_data", "payload", "ciphertext"}
EVENTS_KEPT = 2000
# The longest the arguments of a call may be, as JSON, for the audit log to keep them: it keeps only their names past it.
AUDIT_ARGS_MAX = 2000
# Basilisk stubs the `types` module.
GENERATOR = type((lambda: (yield))())


class Call:
    """``user`` is who acts; ``by`` signed the message (the user, or their delegate); ``proposal`` is the
    accepted proposal whose replay this is, if any; ``grant`` is the power of attorney a delegate acts under;
    ``legacy`` marks an effect of a legacy codex, which acts for the realm (api.members.within): only the legacy
    hook path (core.sandbox.call) sets it, and a call without it holds nothing of the realm's."""

    def __init__(self, user, by, origin, proposal="", grant="", legacy=False):
        self.user, self.by, self.origin, self.proposal, self.grant = user, by, origin, proposal, grant
        self.legacy = legacy

    @property
    def delegated(self):
        return self.user != self.by


def op(perm, read=False, delegable=False, governed=None, signing=False, parties=None, public=False, perm_for=None,
       returns=None, check=None, private=(), derived=None, secret=(), fixes=None, releases=None):
    """Register an operation.

    governed(args) -> the Department whose policy governs the call; a policy that is not direct turns
    the call into a proposal. parties(args) -> principals with a stake, who may not perform it.
    perm_for(args) -> the permission this call needs, when it depends on the arguments (``perm`` otherwise).
    returns -> the key of the reply holding the model's result: the record (by id), the list of a page, the value,
    or for a governed operation the id of the record it created. Without it, the reply itself is the result.
    check(args) -> raises Fail for arguments the operation would refuse whatever the state of the realm. It runs
    before ``governed`` turns the call into a proposal, so a call that cannot succeed files nothing.
    delegable -> only for an operation that GGG's tiers (ggg_delegation) and core.poa.OWN do not classify, a test's: a
    classified operation is delegable when its tier is direct.
    private -> the arguments that hold personal data or key material: the audit log never keeps them (I-ALL-5).
    derived(args, proposal) -> the facts of the call that are not its arguments, computed from the realm's records (GGG's
    ``derived`` facts, such as the totals of a budget), for the rules that read the call's facts. ``proposal`` is the
    proposal whose replay the call is, "" for any other call.
    secret -> the arguments and reply fields that are key material or credentials (a code, keys, private data, a
    request's body and reply): the legislation never sees them, at any depth (I-PRV-1), and nor does the audit log.
    Personal data the audit keeps out (``private``) still reaches the legislation that decides on it.
    fixes(args, proposal) -> runs when a proposal that carries the call is filed, by the governed call or by
    ``proposal_create``, before the proposal is written: it refuses a call that has nothing to carry and ties the records
    the call will act on to ``proposal``, so that no other proposal takes them.
    releases(args, proposal) -> runs when that proposal ends without the call having run: it frees what ``fixes`` tied.
    """
    def wrap(fn):
        OPS[fn.__name__] = {"fn": fn, "perm": perm, "read": read, "delegable": delegable, "governed": governed,
                            "signing": signing, "parties": parties, "public": public, "perm_for": perm_for,
                            "returns": returns, "check": check, "private": private, "derived": derived,
                            "secret": tuple(secret), "fixes": fixes, "releases": releases}
        poa.classify(OPS[fn.__name__], fn.__name__)
        return fn
    return wrap


def ops():
    """Every registered operation. Read this, not OPS, when looking an operation up by name."""
    # Basilisk imports modules lazily and runs one only when a name it has not yet defined is read.
    for name in MODULES:
        __import__(f"api.{name}")
        getattr(sys.modules[f"api.{name}"], "op", None)
    return OPS


def dispatch(fn, arg, origin="user", signer=None, declared=(), query=False, proposal="", legacy=False):
    """Run an operation; ``query`` when called through the ``read`` endpoint, whose changes are discarded; ``legacy``
    marks a legacy codex's effect (``Call.legacy``).

    A refusal replies ``ok`` false with the ``error`` for people and its ``kind`` for programs: GGG's error in
    snake case (``not_found``, ``forbidden``, ``conflict``, ...), see core.db.Fail."""
    name = fn if isinstance(fn, str) else fn.__name__
    spec = OPS.get(name) or ops().get(name)
    if spec is None:
        return json.dumps({"ok": False, "error": f"unknown operation '{name}'", "kind": Fail.kind})
    if query and not spec["read"]:
        return json.dumps({"ok": False, "error": f"{name} changes the realm: call it with run, not read",
                           "kind": Fail.kind})
    try:
        args = json.loads(arg or "{}") if isinstance(arg, str) else dict(arg)
        by = signer or chain.caller()
        claimed = args.pop("on_behalf_of", None)
        # A replay runs what a vote accepted, as its recorded signer. Stored args name no one else.
        user = by if origin == "replay" else claimed or by
        grant = auth.authorize(user, by, name, spec, args, origin, declared)
        ranges.check(name, args)
        if spec["check"]:
            spec["check"](args)
        governing = spec["governed"](args) if spec["governed"] and origin not in ("replay", "codex") else None
        from core import legislation

        call = Call(user, by, origin, proposal, grant, legacy)
        record = None if spec["read"] else (lambda out: acted(name, user, by, origin, args, out, grant))
        # A delegate's governed call is always a proposal: where the department decides directly, the member's own
        # vote is what lets it through. The filing is a proposal_create to the legislation.
        if governing is not None and (grant or not direct(governing)):
            from api import proposals

            pargs = filing(name, args, governing)
            out = yield from legislation.run("proposal_create", call, pargs, None,
                                             lambda: proposals.file_replay(pargs, user, by, grant), record)
        else:
            out = yield from legislation.run(name, call, args, governing, lambda: spec["fn"](call, args), record,
                                             spec["read"])
        return json.dumps({"ok": True, **out})
    except auth.Denied as e:
        return json.dumps({"ok": False, "error": str(e), "kind": e.kind, "permission": e.permission, "denied": True})
    except Fail as e:
        return json.dumps({"ok": False, "error": str(e), "kind": e.kind})
    except KeyError as e:
        return json.dumps({"ok": False, "error": f"missing {e}", "kind": Fail.kind})
    except Exception as e:
        chain.trap(f"{name}: {e}")


def filing(name, args, department):
    """The proposal a governed call becomes, as ``proposal_create`` takes it: a title, the department and the action."""
    return {"title": f"{name} {json.dumps(args, sort_keys=True)[:120]}", "department": department.id,
            "action": {"op": name, "args": args}}


def direct(department):
    p = department.policy or {}
    return int(p.get("m", 1)) <= 1 and int(p.get("n", 1)) <= 1 and not p.get("quorum") and not p.get("veto")


def redacted(name, args):
    """The arguments of a call to ``name``, or its reply, as a log may show them, the audit log and a scheduled task's
    alike: without the secrets of every call, the operation's ``private`` fields, which hold personal data or key
    material (I-ALL-5), and its ``secret`` ones."""
    spec = OPS.get(name) or ops().get(name)
    hidden = set(SECRET) | set(spec["private"] if spec else ()) | set(spec["secret"] if spec else ())
    return {k: v for k, v in args.items() if k not in hidden}


def acted(name, user, by, origin, args, out, grant):
    """Record an update, and tell the member if a delegate did it."""
    audit(name, user, by, origin, args, out, grant)
    if grant:
        poa.notice(grant, name, args, out)


def audit(name, user, by, origin, args, out, grant=""):
    from ggg import Event

    kept = redacted(name, args)
    if len(json.dumps(kept)) > AUDIT_ARGS_MAX:
        kept = {"keys": sorted(kept)}
    shown = {k: v for k, v in redacted(name, out).items() if isinstance(v, (str, int, bool))}
    Event(id=str(Event.max_id() + 1), op=name, user=user, by=by, origin=origin, args=kept, result=shown, at=chain.now(),
          grant=grant or None)
    old = Event[str(Event.max_id() - EVENTS_KEPT)]
    if old:
        old.delete()


def oldest_event():
    """The oldest event the audit log still holds, or None."""
    from ggg import Event

    return Event[str(max(1, Event.max_id() - EVENTS_KEPT + 1))]


def run(gen):
    try:
        next(gen)
    except StopIteration as done:
        return done.value
    raise RuntimeError("a query cannot call other canisters")


def call(name, args=None, user=None, origin="user"):
    """Run an operation from Python (REPL, codex effects, replays) and return its result as a dict."""
    return json.loads(run(dispatch(name, json.dumps(args or {}), origin=origin, signer=user)))
