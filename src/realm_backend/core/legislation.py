"""The runtime of a layered codex (docs/codices/layered-codex.md §9.3): the active codex's chapters run in one
resident codex subinterpreter, never in the realm's own interpreter, and the realm asks it around every GGG operation.

- ``build(plan, sources)``: the install's step 5 (§9.2): every chapter of a codex loaded into a fresh sandbox, top
  to bottom, the in-canister checks of §12.2 run, the generated Cedar validated by the realm's Cedar engine, and the
  flattened view, the Cedar and the errors returned.
- ``load(codex)``, then ``activate(codex)``: at the switch (§9.2 step 6): the codex becomes the running one, its
  generated forbids parsed once and put in force (core.auth), which fails before the switch when it does not load, and
  then the ``on_codex_activate`` hooks run, once per realm (not in a quarter). A revert, and the realm's start, call
  ``warm`` instead, which runs no hook and never raises.
- ``run(op, ...)``: around a GGG operation: the guards ``check_<op>`` before it, the hooks ``on_<op>`` after it
  succeeds. ``compute(op, ...)`` answers ``compute_<op>`` when an operation asks during its work.

Loading: after an upgrade (``warm``, from setup.start) or on first use, the active codex's chapters are read from
``ChapterSource`` and loaded through core.legislation_sandbox, the program of the subinterpreter. Failures: a guard
that raises denies ("legislation error in <chapter>"), a hook that raises never fails its caller; both count per
chapter and method on the codex row (``failures``), which ``codex_status`` shows, and an entry clears when that
method next succeeds, and the IT admin team is notified of a method's first failure and again after a day (``_record``);
a failure on a query path (``compute_realm_join`` from ``join_targets``) is not kept, as a query keeps no writes.
Nothing reverts by itself. Reserved operations (§10.4) run no chapter, nor does a proposal or
a federal vote about one. Effects go through the codex caller (origin ``codex``) like a legacy codex's, except that
a governed operation always becomes a proposal the realm files; a chapter's own effects run no legislation again.
What an operation keeps secret (core.op's ``secret=``: key material and credentials) never reaches a chapter; the
personal data the audit log keeps out still reaches the legislation that decides on it (I-PRV-1).
"""

import json

from core import auth, chain
from core import change_list
from core import civil
from core import compliance
from core import it_team
from core import localtime
from core import sandbox
from core import surrogates
from core.db import PAGE, Fail, Forbidden, first, next_id, view
from core.op import FORWARDING, GENERATOR, Call, awaitable, filing, ops
import core.legislation_sandbox  # bundled with the realm for its text (core.sandbox.module_source); the realm runs only its pure ``readable``
import ggg_facts  # GGG's fact schema: the sandbox imports it from text, and the realm builds the Cedar schema from it

# The operations no chapter may hook, guard or forbid, and those it may only hook, as realms.legislation.chapter lists
# them; written here too because the realm compiles the library in its own interpreter only when an install checks a
# change list, and the runtime needs the lists before that (a test keeps them equal).
RESERVED = ("codex_revert", "codex_revert_fast", "it_team_set", "sync_source_remove", "codex_check_config",
            "codex_check_now")
HOOK_ONLY = ("link_notify",)
# The federation_message topics that carry a codex across a realm's quarters (api.propagation): like the reserved
# operations, no chapter, hook or forbid runs on a message that carries one, or a codex could refuse its own revert.
PROPAGATION = ("codex.stage", "codex.unstage", "codex.revert")
ACTIONS = ("ggg_read", "local_day")  # what a chapter may ask the realm, through the sandbox's rpc
# The operation that switches codices: after it the runtime runs the new codex, so the hooks the dispatcher would
# run for it are the new codex's, which the switch runs once in a message of its own (``activate``), never the old one's.
ACTIVATE = "codex_activate"
# Instructions the realm spends answering one sandbox call's reads, at most: past it the reads are refused and the
# call fails, so a chapter that loops on reads costs the caller this much and never the whole message.
READS = 2_000_000_000
REMINDER = 24 * 3600  # seconds a method keeps failing before the IT admin team is told again
CODEX_PAGE = "/extensions/codex_viewer"  # where an IT admin team notification takes them: the failing chapters, and the revert
# What a chapter may read of GGG's entities, and nothing else: what legislation needs to decide, never a key, a code,
# a person's private data, a sealed bid, who voted what, a grant or a payment account, nor what one member was told.
# A Request leaves out its ``body`` and ``reply`` (personal data) and its ``decision`` (free text until GGG types it as
# an enum), a Dispatch its ``body``, a Procedure its ``facts``. A member's home cell is as private as ``Human.h3_index``:
# a Zone has no ``user``, and ROWS_WHERE lets a chapter see the zones the realm defined only; legislation reads a
# member's district through ``User.home_zone``, the id of such a zone.
VIEWS = {
    "Appointment": ("id", "position", "user", "kind", "agent", "supervisor", "started_at", "ended_at", "status"),
    "Attribute": ("id", "human", "kind", "value", "status", "issuer", "verified_at", "expires_at"),
    "Authority": ("id", "grantor", "target", "operations"),
    "Budget": ("id", "fund", "period", "category", "slot", "kind", "planned", "actual", "status", "proposal"),
    "Calendar": ("id", "epoch", "fiscal_period", "voting_window", "codex_release_cycle", "benefit_cycle",
                 "service_payment_cycle", "license_review_cycle", "custom_cycles"),
    "Case": ("id", "court", "plaintiff", "defendant", "title", "dispute", "status", "filed_at", "closed_at"),
    "Contract": ("id", "title", "parties", "mandate", "value", "symbol", "status", "starts_at", "ends_at"),
    "Court": ("id", "name", "operator", "level", "jurisdiction", "parent", "status"),
    "Department": ("id", "description", "parent", "is_root", "head", "policy", "fund"),
    "Dispatch": ("id", "to", "topic", "title", "document", "codex", "queued_at", "status"),
    "Dispute": ("id", "claimant", "respondent", "subject", "title", "status", "case"),
    "Document": ("id", "department", "title", "kind", "languages", "public", "author", "at"),
    "External": ("id", "name", "kind"),
    "FederalVoteLeg": ("id", "vote", "quarter", "outcome", "status", "codex", "compliance"),
    "FiscalPeriod": ("id", "name", "starts_at", "ends_at", "status"),
    "Fund": ("id", "name", "kind", "department", "balances", "allocation_rule"),
    "Human": ("id", "user"),
    "Identity": ("id", "human", "kind", "issuer", "status", "verified_at"),
    "Invoice": ("id", "payer", "amount", "symbol", "due_at", "status", "reason", "paid_at"),
    "Judge": ("id", "user", "court", "specialization", "status", "appointed_at"),
    "Land": ("id", "x", "y", "width", "height", "cell", "kind", "owner", "status", "token_id", "metadata", "zones",
             "assessed_value", "area"),
    "LayeredCodex": ("id", "layers", "layer_paths", "status", "activates_at", "installed_at"),
    "LedgerEntry": ("id", "kind", "category", "debit", "credit", "symbol", "fund", "period", "at", "description"),
    "License": ("id", "kind", "holder", "issued_by", "status", "applied_at", "issued_at", "expires_at", "conditions"),
    "Loan": ("id", "lender", "amount", "symbol", "term", "rate", "fund", "outstanding", "status"),
    "Mandate": ("id", "title", "scope", "executor", "proposal", "status", "expires_at"),
    "Member": ("id", "status", "joined_at"),
    "Notification": ("id", "recipient", "topic", "sender", "at"),
    "Organization": ("id", "description", "status"),
    "Penalty": ("id", "verdict", "target", "kind", "amount", "symbol", "status", "due_at", "executed_at"),
    "Position": ("id", "department", "organization", "title", "profile", "headcount", "salary", "salary_period", "status"),
    "Procedure": ("id", "initiated_by", "matter", "decision_deadline", "silence", "legislation"),
    "Proposal": ("id", "title", "description", "proposer", "department", "action", "status", "opens_at", "deadline",
                 "effective_at", "threshold", "quorum", "tally", "result", "links"),
    "Quarter": ("id", "name", "index", "status", "population", "codex_id", "compliance"),
    "Realm": ("id", "name", "status", "accounting_currency", "accounting_currency_decimals", "is_quarter", "is_capital",
              "quarter_join_mode", "open_registration", "timezone"),
    "Request": ("id", "kind", "by", "filed_at", "due_at", "extension_limit", "status", "decided_at"),
    "TaxDeclaration": ("id", "declarant", "period", "base", "amount", "status", "invoice"),
    "TaxPolicy": ("id", "status", "document", "taxes", "description", "proposal", "adopted_at"),
    "Tender": ("id", "title", "description", "requester", "status", "opens_at", "closes_at", "winner", "contract"),
    "Transfer": ("id", "sender", "to", "symbol", "amount", "status", "at"),
    "User": ("id", "nickname", "home_quarter", "home_zone", "profiles"),
    "UserProfile": ("id", "description", "allowed_to"),
    "Verdict": ("id", "case", "issued_by", "decision", "status", "issued_at"),
    "Zone": ("id", "name", "kind", "land"),
}
# The field of VIEWS that a chapter reads on some rows only, ``{entity: (field, shown)}``, ``shown(row)`` saying of the
# row as VIEWS gives it whether the field is for the chapter. On any other row it reads as None, and no ``find`` goes by
# it. A private document's title is metadata in clear; a dispatch's title is a public document's only when the dispatch
# carries a document (only api.crypto.document_publish sets one; a notice's topic is the sender's word, and any other
# notice's title is not for legislation).
CONDITIONAL = {"Document": ("title", lambda row: bool(row["public"])),
               "Dispatch": ("title", lambda row: bool(row["document"]))}
# The entities of which a chapter sees the rows whose field is set and no others, ``{entity: field}``: a zone is the
# realm's (``defined``) or a member's home cell, which is no row to legislation: it reads as none, in a ``get`` as in a
# ``page`` or a ``find``.
ROWS_WHERE = {"Zone": "defined"}
# The fields of VIEWS that are an amount of money. A chapter reads them as a ``Money``, which rounds as GGG does, in
# the decimals a ``compute_`` is told (a parcel's value is in the accounting currency, and tax_assess computes a tax on
# land through a codex only when it is billed in it); the other calls do not know them.
MONEY = {"Land": ("assessed_value",)}
LIBRARY, PROGRAM, FACTS = "realms.legislation", "core.legislation_sandbox", "ggg_facts"
UNKNOWN = object()  # the running codex before the first look in a new heap
# codex: the id of the codex the runtime runs (None: a legacy codex or none); box: its sandbox, None when it did not
# load; failing: whether its row records failures, so a success need not read the row to clear one; reads: the
# instruction count when the current sandbox call began, and why its reads were stopped, if they were.
_state = {"codex": UNKNOWN, "box": None, "failing": False, "reads": {"since": 0, "stopped": None}}


def active():
    """The active layered codex, or None."""
    from ggg import LayeredCodex

    return first(LayeredCodex, "status", "active")


def warm(codex=UNKNOWN):
    """Run ``codex`` (the active layered codex by default), recording a codex that does not load instead of
    raising: where the realm cannot stop, at its start (init and post_upgrade, as the loaded sandbox is gone after
    an upgrade and a query could not keep one it loads) and after a revert. Until a codex that does not load is
    reverted, every operation its legislation could guard denies."""
    codex = active() if codex is UNKNOWN else codex
    try:
        load(codex)
    except Exception as e:  # noqa: BLE001  whatever it is, the realm starts and records it
        _drop()
        # Its forbids are unknown now, but not which operations they name: those are refused until it is reverted.
        auth.use_forbids(None, unloaded=codex.forbids if isinstance(codex.forbids, list) else ())
        _state.update(codex=codex.id, failing=bool(codex.failures))
        _record("codex", "load", str(e) if isinstance(e, Fail) else f"{type(e).__name__}: {e}")


def load(codex):
    """Make ``codex`` (a LayeredCodex row, or None for none) the one the runtime runs, its chapters loaded into a
    fresh sandbox, and run no hook. A codex that does not load raises Fail and leaves the running one as it was:
    the install task calls this before the switch, so that a switch to it never happens (§9.2 step 6)."""
    box = _spawn(_plan_of(codex), _texts(codex)) if codex is not None else None
    try:
        generated = _generated(box) if box is not None else None
        auth.use_forbids(generated)
    except BaseException:
        if box is not None:
            box.close()
        raise
    if codex is not None and codex.forbids != sorted(generated["targeted"] if generated else ()):
        codex.forbids = sorted(generated["targeted"] if generated else ())
    _drop()
    _state.update(codex=codex.id if codex is not None else None, box=box, failing=bool(codex.failures) if codex else False)
    if codex is not None:
        _clear(["codex"], "load")


def build(plan, sources):
    """Install step 5 (§9.2): load every chapter of ``plan`` (realms.legislation's ``Plan``, or a dict with its
    ``realm``, ``layers``, ``chapters`` and ``calendars``) from ``sources`` (checksum to text) into a fresh sandbox,
    top to bottom, and run the in-canister checks (§12.2), among them that the realm's Cedar engine accepts the
    forbids it generates (§9.4). Returns ``{"views", "cedar", "errors"}``: the flattened view per domain, the
    generated Cedar, and the errors (the first refusal; nothing else is loaded after it)."""
    box = None
    try:
        box = _spawn(plan if isinstance(plan, dict) else vars(plan), sources)
        generated = _generated(box)
        if generated is not None:
            auth.check_forbids(generated)
        out = {"views": box.call("views"), "cedar": generated["policies"] if generated else "", "errors": []}
    except Fail as e:
        out = {"views": {}, "cedar": None, "errors": [str(e)]}
    except RuntimeError as e:  # the sandbox itself failed on a call: the budget, or a chapter that broke it
        out = {"views": {}, "cedar": None, "errors": [f"the codex failed while building: {e}"]}
    finally:
        if box is not None:
            box.close()
    return out


def activate(codex):
    """At the switch (§9.2 step 6): every ``on_codex_activate`` hook of ``codex`` runs, once per realm: in the
    capital, not in a quarter. ``codex`` is loaded first unless ``load`` already did. A generator."""
    if _state["codex"] != codex.id or _state["box"] is None:
        load(codex)
    from ggg import Realm

    here = Realm["1"]
    if here.is_quarter and not here.is_capital:  # registering a quarter makes the capital a quarter too
        return
    realm = chain.self_id()
    yield from _hooks("codex_activate", {"codex": codex.id}, {"codex": codex.id}, Call(realm, realm, "codex"), None)


def firing(action, facts):
    """The forbids of the running codex that refuse ``facts`` on Cedar ``action``, ``[{"id", "legislation"}]``, asked of
    the sandbox once a request has been refused (core.auth.refuse_forbidden). The reason is a courtesy and the sandbox
    is not trusted with it: whatever goes wrong, or comes back that is not a short list of ids and citations, it is
    empty and the plain reason stands."""
    try:
        found = _call("fired", action=action, facts=facts)
    except Exception:  # noqa: BLE001  the refusal does not depend on it
        return []
    if not isinstance(found, list) or not all(
        isinstance(f, dict) and isinstance(f.get("id"), str) and (f.get("legislation") is None or isinstance(f["legislation"], str))
        for f in found
    ):
        return []
    return [{"id": f["id"][:200], "legislation": (f["legislation"] or "")[:200]} for f in found[:8]]


def run(op, call, args, department, perform, record, read=False):
    """Run GGG operation ``op`` for ``call`` with ``args`` under the legislation: its guards first, then ``perform()``
    (the operation itself, or the filing of the proposal that carries a governed call), then ``record(out)``, the
    audit of the operation, then its hooks, whose effects are audited after it. ``department`` is the one deciding
    a governed call directly, if any. A read runs no legislation. A hook-only operation (``HOOK_ONLY``) runs no guard,
    which fails closed when the codex is not loaded: nothing may refuse the notice that a link ended. A generator, as
    the operation and the effects may call other canisters."""
    legislated = not read and call.origin != "codex" and not is_reserved(op, args) and _ensure()
    if legislated and op not in HOOK_ONLY and not proposes_release(op, args):
        _guard(op, args, call, department)
    out = perform()
    if isinstance(out, GENERATOR):
        out = yield from out
    out = out or {}
    if record is not None:
        record(out)
    if legislated and op != ACTIVATE:  # the switch arms the new codex's on_codex_activate itself (api.codex.switch)
        yield from _hooks(op, args, out, call, department)
    return out


def compute(op, decimals=None, **kwargs):
    """The running codex's answer to ``compute_<op>`` for ``kwargs``, or None when no chapter defines it or no
    layered codex is active. ``decimals`` is the token the answer is in: the amounts the chapter reads are ``Money``
    of it, so that its rates round to that token's cent. A ``compute_`` method that fails fails the operation asking,
    and counts, except on a query path (``join_targets``), where nothing written stays."""
    if not _ensure():
        return None
    method = f"compute_{op}"
    try:
        outcome = _call("compute", op=op, kwargs=_public(kwargs, _secret(op)), decimals=decimals, now=chain.now(),
                        approvals=[])
    except Fail as e:
        _record("codex", method, str(e))
        raise Fail(f"legislation error: {e}")
    _clear(["codex"], method)
    if not outcome["found"]:
        return None
    if "error" in outcome:
        file = outcome["chapter"] or outcome["files"][-1]
        _record(file, method, outcome["error"])
        raise Fail(f"legislation error in {file}: {outcome['error']}")
    _clear(outcome["files"], method)
    return outcome["result"]


def views():
    """The flattened view of every domain of the running layered codex (§5.9), ``{domain: {"domain", "path",
    "members"}}``, as the sandbox keeps it loaded; empty when no layered codex is active. It reads no row of GGG."""
    return _call("views") if _ensure() else {}


def _ensure():
    """Whether a layered codex is active, loading it on first use: a new heap, or a query after one."""
    if _state["codex"] is UNKNOWN:
        warm()
    return _state["codex"] is not None


def is_reserved(op, args):
    """Whether ``op`` is a reserved operation, or about one (§10.4): a proposal or a federal vote whose action is
    reserved, their votes, legs, messages and stops, and the messages that carry a codex across the quarters. No
    chapter runs for them."""
    if op in RESERVED or (op == "federation_message" and args.get("topic") in PROPAGATION):
        return True
    action = _action_about(op, args)
    return isinstance(action, dict) and action.get("op") in RESERVED


def proposes_release(op, args):
    """Whether ``op`` is the ``proposal_create`` of a "Propose install" (§11.4), whose action installs a release of a
    layer from above (core.change_list.names_release). No guard or forbid of the codex runs on it: the realm votes
    only on when to install that legislation, whose date the source's legislation set (§2, rule 3). Its hooks run,
    and the vote follows the realm's rules like any proposal's."""
    action = args.get("action") if op == "proposal_create" else None
    return isinstance(action, dict) and action.get("op") == "codex_install" and change_list.names_release(
        action.get("args"))


def _action_about(op, args):
    """The action an operation is about, when it is about a proposal or a federal vote: the one proposed, voted,
    stopped, withdrawn, cancelled or carried by a message. None otherwise."""
    from ggg import FederalVoteLeg, Proposal

    if op in ("proposal_create", "federal_vote_propose"):
        return args.get("action")
    if op == "federation_message":
        body = args.get("body") if isinstance(args.get("body"), dict) else {}
        return body["action"] if "action" in body else _vote_action(body.get("vote"))
    if op == "quarter_report":
        leg = args.get("leg") if isinstance(args.get("leg"), dict) else {}
        return _vote_action(leg.get("vote"))
    if op.startswith("federal_vote_"):
        return _vote_action(args.get("vote"))
    if op.startswith("proposal_") and isinstance(args.get("proposal"), str):
        proposal = Proposal[args["proposal"]]
        if proposal is None:
            return None
        if proposal.action is not None:
            return proposal.action
        leg = first(FederalVoteLeg, "proposal_id", proposal.id)  # a federal vote's leg carries no action of its own
        return leg.vote.action if leg is not None and leg.vote is not None else None
    return None


def _vote_action(vote_id):
    from ggg import FederalVote

    vote = FederalVote[vote_id] if isinstance(vote_id, str) else None
    return vote.action if vote is not None else None


def _approvals(call, department):
    """The departments whose approval the operation carries: the one deciding it directly, or the one whose
    accepted proposal it replays."""
    if department is not None:
        return [department.id]
    if call.proposal:
        from ggg import Proposal

        proposal = Proposal[call.proposal]
        if proposal is not None and proposal.department is not None:
            return [proposal.department.id]
    return []


def _guard(op, args, call, department):
    """The guards ``check_<op>``: a denial refuses the operation with the guard's reason; a guard that raises, or a
    sandbox that fails, refuses it too (fail closed), naming the chapter, and counts."""
    method = f"check_{op}"
    try:
        outcomes = _call("guard", op=op, args=carried(_public(args, _secret(op, args))), now=chain.now(),
                         approvals=_approvals(call, department))
    except Fail as e:
        _record("codex", method, str(e))
        raise Forbidden(f"legislation error: {e}")
    _clear(["codex"], method)
    for outcome in outcomes:
        if "error" in outcome:
            file = outcome["chapter"] or outcome["files"][-1]
            _record(file, method, outcome["error"])
            raise Forbidden(f"legislation error in {file}: {outcome['error']}")
        _clear(outcome["files"], method)
        if "denied" in outcome:
            raise Forbidden(outcome["denied"])


def _hooks(op, args, result, call, department):
    """The hooks ``on_<op>`` after the operation, with GGG's event: one sandbox call per domain, so that each domain
    has the reads budget to itself and a domain that fails never costs another its effects. A hook that raises, or
    an effect that fails, never fails the caller: it counts against the chapter. A generator."""
    method, secret = f"on_{op}", _secret(op, args)
    event = {"user": call.user, "args": carried(_public(args, secret)), "result": carried(_public(result, secret)),
             "at": chain.now()}
    approvals = _approvals(call, department)
    try:
        domains = _call("hook_domains", op=op)
    except Fail as e:
        _record("codex", method, str(e))
        return
    _clear(["codex"], method)
    running = _state["codex"]
    for entry in domains:
        files = entry["files"]
        try:
            outcome = _call("hook", op=op, domain=entry["domain"], event=event, now=chain.now(), approvals=approvals)
        except Fail as e:  # the sandbox failed on this domain: its budget or its reads spent
            _record(files[-1], method, str(e))
            continue
        if "error" in outcome:
            _record(outcome["chapter"] or files[-1], method, outcome["error"])
            continue
        errors = yield from _in_own_message(outcome["effects"])
        if _state["codex"] != running:
            # Another message switched the codex while these effects waited. The domains left, and the failure of this
            # one, belong to the codex it replaced: the sandbox, and the record of failures, are the new one's now.
            return
        if errors:
            _record(files[-1], method, "; ".join(errors))
        else:
            _clear(files, method)


def _in_own_message(effects):
    """Apply a domain's effects in a message of their own, by calling the realm's own ``hook_effects``. An operation that
    raises what it does not expect traps, and a trap undoes the whole message and no handler here sees it: here it
    undoes only that message, and comes back as a rejected call, which is the chapter's failure, while the operation
    that ran the hook, and a scheduled codex switch it belongs to, stand (§9.3). A call and not a timer: a trap in a
    timer would erase the record of its own failure. Where nothing can wait for another canister (``awaitable``: an
    operation that another one runs through ``core.op.call``, as ``shell.execute``, ``extension.call`` and the
    controller's ``__shell__`` do) they run where they are, as before (§9.3). Returns the errors. A generator."""
    if not effects:
        return []
    if not awaitable():
        return (yield from _effects(effects))
    try:
        reply = yield from chain.call_text(chain.self_id(), "hook_effects", json.dumps(effects))
    except Exception as e:  # noqa: BLE001  the call was rejected: the message of the effects trapped
        return [f"the effects were rejected: {e}"]
    return json.loads(reply)


def hook_effects(effects):
    """The message in which a hook domain's effects run (main.hook_effects): ``effects`` as JSON, ``[name, args]`` each,
    applied by ``_effects``; returns its errors as JSON. Only the realm calls it, from ``_in_own_message``. A generator."""
    if chain.caller() != chain.self_id():
        chain.trap("only the realm applies the effects of its hooks")
    return json.dumps((yield from _effects(json.loads(effects))))


def _effects(effects):
    """Apply a hook's queued effects in order, as the codex, stopping at the first that fails: GGG operations, with
    ``$eff`` references to earlier results resolved as a legacy codex's are, from replies without their secrets.
    Returns the errors (one at most). A generator."""
    results = []
    for name, args in effects:
        reply = yield from _effect(name, sandbox.resolve(args, results))
        results.append(_public(reply, _secret(name)))
        if not reply.get("ok"):
            return [f"{name}: {reply.get('error')}"]
    return []


def _entity_refs(value):
    """``value`` with each GGG ``Ref`` in it, ``{kind, id}`` as a chapter's ``Ref("external", "AGE")`` crosses, written
    the way the realm's operations take an entity: ``"External:AGE"``."""
    if isinstance(value, dict):
        if set(value) == {"kind", "id"} and all(isinstance(v, str) for v in value.values()):
            return f"{value['kind'][:1].upper() + value['kind'][1:]}:{value['id']}"
        return {k: _entity_refs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_entity_refs(v) for v in value]
    return value


def _effect(name, args):
    """One effect, as a reply ``{ok, ...}``: a governed operation becomes a proposal the realm files, whatever its
    department's policy, never a direct call; a reserved operation, an operation about a proposal or a vote on one,
    and an effect that would set a time zone (§4.5) are refused; whatever else fails is the chapter's failure,
    never the caller's."""
    args = _entity_refs(args)
    if name in RESERVED or is_reserved(name, args):
        return {"ok": False, "error": f"{name} is about a reserved operation: no chapter may call it"}
    if "timezone" in args:
        return {"ok": False, "error": "a chapter never sets a time zone"}
    spec = ops().get(name)
    if spec is None:
        return {"ok": False, "error": f"unknown operation '{name}'"}
    try:
        department = spec["governed"](args) if spec["governed"] else None
        if department is not None:
            name, args = "proposal_create", filing(name, args, department)
        return (yield from sandbox.apply(name, args, (name,)))
    except Exception as e:  # noqa: BLE001  a trap is not an Exception and still undoes the message
        return {"ok": False, "error": str(e) if isinstance(e, Fail) else f"{type(e).__name__}: {e}"}


def _call(fn, **kwargs):
    """Ask the running codex's sandbox. A spent budget (``sandbox.Spent``, whatever the meter's text) reloads the codex
    and, unless the call had already read past its budget, asks once more, so a long-lived sandbox never fails a call
    for the calls before it; a call that spends a fresh sandbox too, a sandbox that fails otherwise, or a codex that
    did not load, raises Fail."""
    if _state["box"] is None:
        raise Fail(f"the codex {_state['codex']} is not loaded")
    try:
        return _ask(fn, kwargs)
    except sandbox.Spent:
        pass
    except RuntimeError as e:
        raise Fail(f"the codex {_state['codex']} failed: {e}")
    stopped = _state["reads"]["stopped"]
    from ggg import LayeredCodex

    warm(LayeredCodex[_state["codex"]])
    if stopped:
        raise Fail(stopped)
    if _state["box"] is None:
        raise Fail(f"the codex {_state['codex']} is not loaded")
    try:
        return _ask(fn, kwargs)
    except RuntimeError as e:
        raise Fail(f"the codex {_state['codex']} failed: {e}")


def _ask(fn, kwargs):
    """One call into the sandbox, its reads metered (``_answer``): a call whose reads were stopped fails, whatever
    the chapters made of the refusals."""
    _state["reads"] = {"since": chain.instructions(), "stopped": None}
    out = _state["box"].call(fn, **kwargs)
    if _state["reads"]["stopped"]:
        raise Fail(_state["reads"]["stopped"])
    return out


def _spawn(plan, sources):
    """A sandbox with the chapters of ``plan`` loaded from ``sources`` and checked. Raises Fail with the first
    refusal, in the form the checker prints (§5.5)."""
    program, _ = sandbox.module_source(PROGRAM)
    if sandbox.NATIVE:  # the subinterpreter imports the library and GGG's fact schema from text; a laptop imports them
        program += f"\nLIBRARY = {({**sandbox.module_sources(LIBRARY), FACTS: sandbox.module_source(FACTS)})!r}\n"
    try:
        box = sandbox.Resident(program, _answer, ACTIONS)
    except RuntimeError as e:
        raise Fail(f"the codex sandbox did not start: {e}")
    try:
        _ok(box.call("setup", operations=sorted(ops()), layers=list(plan["layers"]), realm=plan["realm"],
                     calendars=[[c["source"], _calendar(sources, c)] for c in plan["calendars"]],
                     entities=sorted(VIEWS), money=MONEY))
        for c in plan["chapters"]:
            _ok(box.call("load", source=c["source"], domain=c["domain"], text=_text(sources, c)))
        _ok(box.call("finish"))
    except RuntimeError as e:  # a chapter that spent the budget or the memory while loading, among other things
        box.close()
        raise Fail(f"the codex does not load: {e}")
    except BaseException:
        box.close()
        raise
    return box


def _generated(box):
    """The forbids of the codex loaded in ``box`` as the realm's Cedar engine takes them: ``{"schema", "policies",
    "targeted", "types"}`` (core.auth.use_forbids), or None when its legislation forbids nothing, so that it costs the
    engine nothing. Only the policy text comes from the sandbox, and it is read, not trusted (core.forbids.targets):
    the schema and the fact types the realm validates it against and builds its requests from are the realm's own,
    from GGG's fact schema and the context of core.auth. Fail when a generated policy names a reserved operation or a
    hook-only one, which the library refuses too (§10.4)."""
    try:
        out = _ok(box.call("cedar", secret=_secrets()))
    except RuntimeError as e:  # the sandbox itself failed on the call
        raise Fail(f"the codex does not load: {e}")
    if not out["cedar"]:
        return None
    from core.forbids import targets  # compiled when a codex forbids something, not at every start

    known = core.legislation_sandbox.readable(ggg_facts.FACTS, _secrets())
    targeted = targets(out["cedar"], known, ops(), RESERVED + HOOK_ONLY)
    schema, types = _schema_of({a: known[a] for actions in targeted.values() for a in actions})
    return {"schema": schema, "policies": out["cedar"], "targeted": targeted, "types": types}


def _schema_of(facts):
    """The Cedar schema for the actions in ``facts`` (GGG's fact schema of just those), and the type of each of their
    facts, ``{action: {fact: [type, optional]}}``, written by the library as the sandbox once wrote them. Fail when the
    actions are not a closed set (a forbid on one runs on its ``also`` too), which the generator never writes."""
    from realms.legislation import cedar as generator
    from realms.legislation.errors import LegislationError

    try:
        schema = generator.FactSchema(facts)
        text = generator.schema(schema, (), auth.CONTEXT, sorted(facts))
    except LegislationError as e:
        raise Fail(f"a generated forbid does not fit GGG's fact schema: {e}")
    return text, {a: {n: [f.type, f.optional] for n, f in schema.actions[a].facts.items()} for a in facts}


def _ok(reply):
    if not reply["ok"]:
        raise Fail(reply["error"])
    return reply


def _plan_of(codex):
    """What the sandbox loads for a stored codex: its layers, its own source (the last layer), the chapters of each
    layer path top to bottom, and the calendars (``<source>/calendar`` entries)."""
    chapters = codex.chapters
    return {
        "realm": codex.layers[-1], "layers": codex.layers,
        "chapters": [{"source": source, "domain": domain, "sha256": chapters[f"{source}/{domain}"]["sha256"]}
                     for domain in sorted(codex.layer_paths) for source in codex.layer_paths[domain]],
        "calendars": [{"source": source, "sha256": chapters[f"{source}/calendar"]["sha256"]}
                      for source in codex.layers if f"{source}/calendar" in chapters],
    }


def _texts(codex):
    """The stored text of every chapter and calendar of ``codex``, by checksum."""
    from ggg import ChapterSource

    found = {}
    for entry in codex.chapters.values():
        row = ChapterSource[entry["sha256"]]
        if row is None:
            raise Fail(f"the text of {entry['sha256']} is not stored")
        found[entry["sha256"]] = row.source
    return found


def _text(sources, entry):
    if entry["sha256"] not in sources:
        raise Fail(f"{entry['source']}/{entry.get('domain', 'calendar')}: the bytes of {entry['sha256']} are missing")
    return sources[entry["sha256"]]


def _calendar(sources, entry):
    """The layer's calendar, parsed. It is the one chapter that is JSON, so it is the one that can hold a lone
    surrogate once parsed (``"\\ud800"``, which no row or reply can keep: core.surrogates); every other chapter is code,
    where that is an escape of Python's. It is read here, after every route that brought the text (HTTPS, a capital, the
    quarter's announced one) has verified its checksum, so that a refusal by name is for the calendar the change list
    names, and the install job records it as the build's."""
    text = _text(sources, entry)
    try:
        calendar = json.loads(text)
    except ValueError as e:
        raise Fail(f"{entry['source']}/calendar.json is not JSON: {e}")
    if surrogates.starts_an_escape(text) and surrogates.holds(calendar):
        raise Fail(f"{entry['source']}/calendar.json holds a lone surrogate (U+D800 to U+DFFF), which is not text")
    return calendar


def _public(value, secret):
    """``value`` without the keys ``secret`` names (an operation's ``secret=``, core.op: key material and
    credentials), at any depth: what crosses into a chapter as that operation's arguments or result."""
    if not secret:
        return value
    if isinstance(value, dict):
        return {k: _public(v, secret) for k, v in value.items() if k not in secret}
    if isinstance(value, list):
        return [_public(v, secret) for v in value]
    return value


def _secret(op, args=None):
    """The fields operation ``op`` keeps secret (``secret=``). With its ``args``, an operation that runs another
    (core.op.FORWARDING) keeps that one's secret as well: its reply is the other's."""
    names = {op} | ({str(args.get("method"))} if op in FORWARDING and args else set())
    return tuple(dict.fromkeys(f for n in names for f in (ops().get(n) or {"secret": ()})["secret"]))


def carried(value):
    """``value``, the arguments or the result of an operation, without the secret fields of the calls it carries, at any
    depth. A dict that names an operation (``op`` or ``method``, or both) beside its ``args`` is a call: a proposal's
    action, a step of a task, what ``call_for`` forwards, the card of a pending action. Its arguments lose the secret
    fields of the operations it names, and so do the ``result`` of a card and the lines of its ``summary`` that show them
    (a line is labelled with the argument it shows)."""
    if isinstance(value, list):
        return [carried(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {k: carried(v) for k, v in value.items()}
    names = [n for n in (value.get("op"), value.get("method")) if isinstance(n, str) and n in ops()]
    if names and isinstance(value.get("args"), dict):
        secret = tuple(dict.fromkeys(f for n in names for f in _secret(n)))
        out["args"] = _public(out["args"], secret)
        if isinstance(out.get("result"), dict):
            out["result"] = _public(out["result"], secret)
        if isinstance(out.get("summary"), list):
            out["summary"] = [line for line in out["summary"] if not (isinstance(line, dict) and line.get("label") in secret)]
    return out


def _secrets():
    """What each operation keeps from the legislation (``secret=``), by operation, for those that keep something."""
    return {name: list(spec["secret"]) for name, spec in ops().items() if spec["secret"]}


def _answer(action, kw):
    """What the sandbox asks the realm: a read of GGG's entities, or the realm's local day of a timestamp. The reads
    of one sandbox call stop once they have cost READS instructions, and the call then fails."""
    reads = _state["reads"]
    if reads["stopped"] is None and chain.instructions() - reads["since"] > READS:
        reads["stopped"] = f"the chapters read for more than {READS:,} instructions in one call: the call stops"
    if reads["stopped"]:
        raise Fail(reads["stopped"])
    if action == "local_day":
        day, _ = localtime.local_day(int(kw["at"]) * localtime.NS)
        year, month, dom = civil.civil_from_days(day)
        return f"{year:04d}-{month:02d}-{dom:02d}"
    if action == "ggg_read":
        return _read(kw)
    raise Fail(f"a chapter cannot ask the realm for {action!r}")


def _read(kw):
    """One row by id, or one page of rows (all, or those whose indexed ``field`` is ``value``), of an entity in
    VIEWS, as plain data with the fields VIEWS allows and no other, and without the rows ROWS_WHERE keeps back."""
    import ggg

    entity = kw["entity"]
    fields = VIEWS.get(entity)
    if fields is None:
        raise Fail(f"a chapter does not read {entity}")
    cls = getattr(ggg, entity)
    if kw["method"] == "get":
        row = cls[str(kw["id"])]
        return _shown(entity, row, fields) if _seen(entity, row) else None
    start = int(kw.get("start") or 1)
    if kw["method"] == "page":
        rows, after = cls.load_some(start, PAGE), start + PAGE
        after = after if after <= cls.max_id() else None
    elif kw["method"] == "find":
        if kw["field"] not in fields or kw["field"] == CONDITIONAL.get(entity, (None,))[0]:
            raise Fail(f"a chapter does not read {entity}.{kw['field']}")
        rows, after = cls.find_by(kw["field"], kw["value"], from_id=start, count=PAGE)
    else:
        raise Fail(f"a chapter cannot {kw['method']!r} an entity")
    return {"rows": [_shown(entity, r, fields) for r in rows if _seen(entity, r)], "next": after}


def _seen(entity, row):
    """Whether a chapter sees ``row`` at all (ROWS_WHERE)."""
    return row is not None and (entity not in ROWS_WHERE or bool(getattr(row, ROWS_WHERE[entity])))


def _shown(entity, row, fields):
    """One row as a chapter sees it: the fields VIEWS allows, less what the row itself keeps back. A row shows the
    field of CONDITIONAL only when it is for the chapter, and a proposal's carried call and the result of running it
    show none of the arguments and reply fields its operation keeps secret (``secret=``), as when the call itself
    crosses (``_public``)."""
    data = view(row, *fields)
    if "compliance" in data:  # a flag is kept as JSON text; a chapter reads its value (core.compliance)
        data["compliance"] = compliance.of(data["compliance"])
    if entity in CONDITIONAL and not CONDITIONAL[entity][1](data):
        data[CONDITIONAL[entity][0]] = None
    if entity == "Proposal" and isinstance(data["action"], dict):
        data["result"] = carried(_public(data["result"], _secret(data["action"].get("op"))))
        data["action"] = carried(data["action"])
    return data


def _record(file, method, error):
    """One more failure of ``method`` of chapter ``file``, with its error, on the running codex's row. The IT admin team
    is told of the first failure of a method, and again when it fails once more at least a day after the last time
    (``notified``)."""
    codex = _row_of_running()
    if codex is None:
        return
    failures = codex.failures
    entry = failures.get(file, {}).get(method, {})
    told = entry.get("notified", 0)
    if not told or chain.now() - told >= REMINDER:
        told = chain.now() if _tell_it_team(file, method, error) else told
    failures.setdefault(file, {})[method] = {"count": entry.get("count", 0) + 1, "error": error[:500], "at": chain.now(),
                                             **({"notified": told} if told else {})}
    codex.failures, _state["failing"] = failures, True


def _tell_it_team(file, method, error):
    """Leave the IT admin team a notification that ``method`` of chapter ``file`` fails (§9.3). False when the realm has
    designated no team. It names the failing method and its error, and never runs legislation."""
    from ggg import Notification

    team = it_team.department()
    if team is None:
        return False
    Notification(id=next_id(Notification, "N"), recipient=f"Department:{team.id}", topic="codex.failing",
                 title=f"The codex fails: {method} of {file}", body=error[:500], href=CODEX_PAGE, sender=chain.self_id(),
                 at=chain.now())
    return True


def _clear(files, method):
    """``method`` succeeded: the failures recorded for it on any of ``files`` are over."""
    if not _state["failing"]:
        return
    codex = _row_of_running()
    if codex is None:
        return
    failures = codex.failures
    for file in files:
        if method in failures.get(file, {}):
            del failures[file][method]
            if not failures[file]:
                del failures[file]
    codex.failures, _state["failing"] = failures, bool(failures)


def _row_of_running():
    from ggg import LayeredCodex

    return LayeredCodex[_state["codex"]] if isinstance(_state["codex"], str) else None


def _drop():
    if _state["box"] is not None:
        _state["box"].close()
    _state.update(box=None, failing=False)
