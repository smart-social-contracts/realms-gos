import json
import sys

from core import auth
from core import chain
from core import pending
from core import poa
from core import ranges
from core.db import Fail, Forbidden

OPS = {}
MODULES = ("actions", "assets", "codex", "crypto", "delegation", "extensions", "external", "federation", "installs", "justice", "licenses",
           "links", "members", "notifications", "orgs", "procurement", "proposals", "quarters", "requests", "setup", "tasks",
           "taxes", "treasury")
SECRET = {"code", "private_data", "payload", "ciphertext"}
# Operations that run another operation, named by ``method``, for a member: a log hides that operation's secrets too.
FORWARDING = ("call_for", "read_for")
EVENTS_KEPT = 2000
# The longest the arguments of a call may be, as JSON, for the audit log to keep them: it keeps only their names past it.
AUDIT_ARGS_MAX = 2000
# Basilisk stubs the `types` module.
GENERATOR = type((lambda: (yield))())


class Call:
    """``user`` is who acts; ``by`` signed the message (the user, or their delegate); ``proposal`` is the
    accepted proposal whose replay this is, if any; ``grant`` is the power of attorney a delegate acts under;
    ``legacy`` marks an effect of a legacy codex, which acts for the realm (api.members.within): only the legacy
    hook path (core.sandbox.call) sets it, and a call without it holds nothing of the realm's. ``prepared_by`` is the
    delegate that prepared the call, when the member approved it from a pending action: the call is the member's own.
    ``root`` is the origin of the outermost call: what a task's step runs through the shell or an extension is still a
    task's."""

    def __init__(self, user, by, origin, proposal="", grant="", legacy=False, prepared_by="", root=""):
        self.user, self.by, self.origin, self.proposal, self.grant = user, by, origin, proposal, grant
        self.legacy, self.prepared_by, self.root = legacy, prepared_by, root or origin

    @property
    def delegated(self):
        return self.user != self.by


def op(perm, read=False, delegable=False, governed=None, signing=False, parties=None, public=False, perm_for=None,
       returns=None, check=None, private=(), derived=None, secret=(), fixes=None, releases=None, summary=None,
       title=None, lists=None):
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
    secret -> the arguments and reply fields that are key material, credentials or a member's home cell (a code,
    keys, private data, a request's body and reply, the cell of zone_set): the legislation never sees them, at any
    depth (I-PRV-1), and nor does the audit log.
    Personal data the audit keeps out (``private``) still reaches the legislation that decides on it.
    fixes(args, proposal) -> runs when a proposal that carries the call is filed, by the governed call or by
    ``proposal_create``, before the proposal is written: it refuses a call that has nothing to carry and ties the records
    the call will act on to ``proposal``, so that no other proposal takes them.
    releases(args, proposal) -> runs when that proposal ends without the call having run: it frees what ``fixes`` tied.
    summary(user, args) -> lines (``{label, value}``) about what the call does for ``user``, in the realm's words, for an
    operation that asks the member each time (core.pending): the member reads them before approving.
    title -> the operation's plain name as the member reads it, the heading of a pending action ("Add a payout
    account"). The realm writes it, never the delegate; an operation that can be prepared for the member has one.
    lists -> for each list of GGG's facts that is not one of the call's arguments, ``{list: fn(args)}`` giving its
    elements (GGG's form), such as the saved policy's entries that ``tax_policy_propose`` names by id; the forbids that
    range over the list judge each of them.
    """
    def wrap(fn):
        if fn.__name__ in OPS:  # a second registration would replace the first one's secret=, permission, governed= silently
            raise ValueError(f"{fn.__name__} is registered twice")
        OPS[fn.__name__] = {"fn": fn, "perm": perm, "read": read, "delegable": delegable, "governed": governed,
                            "signing": signing, "parties": parties, "public": public, "perm_for": perm_for,
                            "returns": returns, "check": check, "private": private, "derived": derived,
                            "secret": tuple(secret), "fixes": fixes, "releases": releases, "summary": summary,
                            "title": title, "lists": lists or {}}
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


def dispatch(fn, arg, origin="user", signer=None, declared=(), query=False, proposal="", legacy=False, prepared=None,
             note="", bound=None, root=None):
    """Run an operation; ``query`` when called through the ``read`` endpoint, whose changes are discarded; ``legacy``
    marks a legacy codex's effect (``Call.legacy``). A delegate's call that asks the member each time is filed as a
    pending action with the delegate's ``note`` instead of run (core.pending), unless a task makes it; ``prepared`` is
    the pending action whose approval this call is, which the audit log names. A delegate acts under the grant it holds
    now, or under ``bound`` (a Delegation) when the call carries out something it wrote under that grant: a task.
    ``root`` is the origin of the outermost call that led here (``origin`` when none did): a call made through the shell
    or an extension by a task is a task's, and asks nobody.

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
        if claimed == by and spec["tier"] == "member":  # not even for oneself: a member's own act takes no claim
            raise Forbidden(f"{name} is the member's own act: it takes no on_behalf_of")
        # A replay runs what a vote accepted, as its recorded signer. Stored args name no one else.
        user = by if origin == "replay" else claimed or by
        asking = origin in poa.ASKING_ORIGINS and (root or origin) in poa.ASKING_ORIGINS and user != by and not query
        asked = pending.asked_under(user, by, spec, args) if asking else None
        if asked:
            return json.dumps({"ok": True, **pending.prepare(asked, user, by, name, spec, args, note, origin, declared)})
        grant = auth.authorize(user, by, name, spec, args, origin, declared, bound)
        # The facts of a call are its arguments as GGG defines them, and ``on_behalf_of`` is one: a forbid may refuse a
        # delegate's vote. ``claimed`` was taken out of ``args`` above, for the operations never read it; a claim of
        # oneself is no delegation, and the call a member approves from a pending action is the delegate's, so it
        # states the member as it was prepared.
        acting = (claimed if claimed != by else None) or (user if origin == "confirmed" else None)
        stated = {**args, "on_behalf_of": acting} if acting else args
        auth.refuse_forbidden(user, name, spec, stated, proposal)
        ranges.check(name, args)
        if spec["check"]:
            spec["check"](args)
        governing = spec["governed"](args) if spec["governed"] and origin not in ("replay", "codex") else None
        from core import legislation

        call = Call(user, by, origin, proposal, grant, legacy, prepared.delegate if prepared else "", root)
        record = None if spec["read"] else (lambda out: acted(name, user, by, origin, args, out, grant, prepared))
        # A delegate's governed call is always a proposal: where the department decides directly, the member's own
        # vote is what lets it through. The filing is a proposal_create to the legislation.
        if governing is not None and (grant or not direct(governing)):
            from api import proposals

            pargs = filing(name, args, governing)
            auth.refuse_forbidden(user, "proposal_create", OPS["proposal_create"],
                                  {**pargs, "on_behalf_of": acting} if acting else pargs, "")
            out = yield from legislation.run("proposal_create", call, pargs, None,
                                             lambda: proposals.file_replay(pargs, user, by, grant, call.prepared_by), record)
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


def redacted(name, value, args=None):
    """The arguments of a call to ``name``, or its reply ``value`` (then ``args`` are the call's arguments), as a log may
    show them, the audit log and a scheduled task's alike: without the secrets of every call, the operation's ``private``
    fields, which hold personal data or key material (I-ALL-5), and its ``secret`` ones; for an operation that runs
    another (``FORWARDING``), the other's as well."""
    args = value if args is None else args
    names = {name} | ({str(args.get("method"))} if name in FORWARDING else set())
    hidden = set(SECRET)
    for n in names:
        spec = OPS.get(n) or ops().get(n)
        hidden |= set(spec["private"]) | set(spec["secret"]) if spec else set()
    return {k: v for k, v in value.items() if k not in hidden}


def acted(name, user, by, origin, args, out, grant, prepared=None):
    """Record an update, and tell the member if a delegate did it. The approval of a pending action (``prepared``) is the
    member's own act: the log names the delegate that prepared it and the grant, and the member is told nothing."""
    audit(name, user, by, origin, args, out, grant or (prepared.grant if prepared else ""),
          prepared.delegate if prepared else "")
    if grant:
        poa.notice(grant, name, args, out)


def audit(name, user, by, origin, args, out, grant="", prepared_by=""):
    from ggg import Event

    kept = redacted(name, args)
    if len(json.dumps(kept)) > AUDIT_ARGS_MAX:
        kept = {"keys": sorted(kept)}
    shown = {k: v for k, v in redacted(name, out, args).items() if isinstance(v, (str, int, bool))}
    Event(id=str(Event.max_id() + 1), op=name, user=user, by=by, origin=origin, args=kept, result=shown, at=chain.now(),
          grant=grant or None, prepared_by=prepared_by)
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


def on_behalf(user, by, args):
    """The arguments of a call that ``by`` signs for ``user``. When they differ the call is a delegate's, so it is made
    for ``user`` whatever ``args`` claim, and meets the grant like any call of the delegate's own. The operations that
    run others (tasks, the shell, extension calls) pass the delegate on with this."""
    return {**args, "on_behalf_of": user} if by != user else args


def call(name, args=None, user=None, origin="user", by=None, root=None):
    """Run an operation from Python (REPL, codex effects, replays, the shell) and return its result as a dict. ``by`` is
    the delegate that signs for ``user`` under a power of attorney; ``root`` the origin of the call that led here."""
    signer = by or user
    return json.loads(run(dispatch(name, json.dumps(on_behalf(user, signer, args or {})), origin=origin, signer=signer,
                                   root=root)))
