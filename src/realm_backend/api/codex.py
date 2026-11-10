import json

from core import chain, journal
from core import it_team
from core import layered_codex as store
from core import legislation
from core import sandbox
from core.db import Conflict, Fail, NotFound, find, first, latest, view, window
from ggg import Codex, InstallJob, LayeredCodex, Realm
from core.op import op
from api import extensions
from api.extensions import checksum, trust
from api.proposals import realm_wide, rule_to_record

API = 2  # the hook contract codices declare as `codex_api_version`
SHOWN = ("id", "version", "status", "checksum", "capabilities", "installed_at", "previous")
LAYERED_SHOWN = ("id", "status", "layers", "layer_paths", "chapters", "previous", "installed_at", "activates_at",
                 "proposal")
JOB_SHOWN = ("id", "proposal", "step", "status", "error", "started_at", "finished_at")
REALM_FIELDS = ("name", "status", "accounting_currency", "accounting_currency_decimals", "is_capital", "is_quarter",
                "open_registration")


def active_legacy():
    """The active codex when it is a legacy one: the kind with a manifest and hooks."""
    return first(Codex, "status", "active")


def active():
    """The codex the realm runs, of either kind: a legacy ``Codex`` or a ``LayeredCodex``. At most one is active."""
    return active_legacy() or first(LayeredCodex, "status", "active")


def pending():
    """The layered codex installed and waiting for ``codex_activate`` or its time, if any; at most one waits at a time."""
    return first(LayeredCodex, "status", "pending")


def find_codex(codex_id):
    """A codex of either kind by its id: ``name@version`` for a legacy one, ``sha256:…`` for a layered one."""
    return find(Codex if "@" in codex_id else LayeredCodex, codex_id)


def runnable(manifest):
    if manifest.get("codex_api_version") != API:
        raise Fail(f"this realm runs codex API {API}, not {manifest.get('codex_api_version')}")


def merge(base, over):
    return {**base, **{k: merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
                       for k, v in over.items()}}


def config(codex):
    """The codex manifest with this realm's parameter choices applied."""
    return merge(codex.manifest or {}, (Realm["1"].manifest_data or {}).get("config_overrides", {}))


def fetch(namespace):
    """Manifest and hook source of a published codex, from the file registry."""
    manifest = json.loads((yield from extensions.fetch(namespace, "manifest.json")))
    return manifest, (yield from extensions.fetch(namespace, "backend/hooks.py"))


def hook(name, context=None):
    """Run hook ``name`` of the active codex with ``context`` and apply its effects.

    A generator (``out = yield from codex.hook(...)``). Returns None when no codex is active or it has no such
    hook, else ``{ok, result, effects}`` or ``{ok: false, error}``. A failing hook never fails the caller; it
    marks the codex unhealthy until a hook succeeds again.
    """
    codex = active_legacy()
    if codex is None or name not in (codex.manifest or {}).get("hooks", []):
        return None
    realm = Realm["1"]
    facts = {"codex": codex.id, "config": config(codex), "now": chain.now(), "realm": view(realm, *REALM_FIELDS)}
    out = yield from sandbox.call(codex.source, name, context or {}, facts, codex.capabilities or [])
    failed = [e for e in out.get("effects", []) if not e.get("ok")]
    error = out.get("error") or "; ".join(f"{e['op']}: {e.get('error')}" for e in failed)
    problem = {"codex": codex.id, "hook": name, "error": error, "at": chain.now()} if error else {}
    if realm.codex_install_state != problem:
        realm.codex_install_state = problem
    return out


def compute(name, context, decimals=None):
    """The active codex's answer to GGG's ``compute_<name>`` for ``context`` (its keyword arguments), None when no
    chapter defines it. ``decimals`` is the token the answer is in (core.legislation.compute). A generator, for the
    operations that drive it with ``yield from``."""
    return legislation.compute(name, decimals=decimals, **context)
    yield  # noqa: unreachable, on purpose


def switch_to(row, replaced):
    """Make ``row`` the active codex in place of ``replaced``, which is kept to revert to, and record the realm's voting
    rule and window with it: what will decide a vote to revert to this codex (§10.1)."""
    record = rule_to_record()
    row.previous, row.status, row.voting_rule = replaced.id if replaced else "", "active", record
    if replaced:
        replaced.status = "inactive"
        if not replaced.voting_rule:  # it became active before the realm recorded rules: the rule in force until now
            replaced.voting_rule = record


def switch(waiting):
    """The switch of an install (docs/codices/layered-codex.md §9.2 step 6): the pending ``waiting`` becomes the
    active codex in place of the current one, kept to revert to, with its ``installed_at`` and, the first time, the
    realm's own source id (its own layer). A release its install brings is checked first, and then the runtime loads
    it: a codex that is refused or does not load raises before anything changes. The ``on_codex_activate`` hooks run
    in the next message (``hooks``): a hook never fails the switch (§9.3), nor the proposal execution or install step
    that made it."""
    from api import installs  # it imports this module

    installs.check_relayed(waiting)
    legislation.load(waiting)
    installs.keep_relayed(waiting)
    switch_to(waiting, active())
    waiting.installed_at = chain.now()
    store.remember_source(waiting.layers[-1])
    store.remember_in_force(waiting.activates_at if 0 < waiting.activates_at <= chain.now() else chain.now())
    journal.codex_installed(waiting)
    chain.set_timer(0, lambda codex_id=waiting.id: hooks(codex_id))
    from api import propagation  # it imports the install task, which imports this module

    chain.set_timer(0, propagation.after_switch)  # a quarter reports to its capital; a capital judges its quarters


def hooks(codex_id):
    """The ``on_codex_activate`` hooks of the codex just switched to, once per realm, unless it was switched away
    from again before this message ran. A generator: a hook's effects may call other canisters."""
    row = LayeredCodex[codex_id]
    if row is not None and row.status == "active":
        yield from legislation.activate(row)


@op("codex.install", governed=realm_wide, returns="codex")
def codex_install(c, args):
    """Install and activate a codex: ``manifest`` and ``source``, or ``codex`` and ``version`` from the file registry.
    The code must pass the trust policy; the replaced codex is kept to revert to. Returns ``codex`` and ``init``.
    A change list's ``url`` and ``checksum`` (``sha256:<hex>``, GGG's Code action) install a layered codex instead:
    the install task fetches, checks and builds it step by step (api.installs), and the call returns its ``job``; a
    proposal with an ``effective_at`` stages it at acceptance and switches at that time. One install at a time."""
    if "checksum" in args:
        from api import installs  # it imports this module

        return installs.begin(c, args)
    if store.live_job() is not None:
        raise Conflict("an install is running")
    if "source" in args:
        manifest, source, namespace = args["manifest"], args["source"], None
    else:
        namespace = f"ext/{args['codex']}/{args['version']}"
        manifest, source = yield from fetch(namespace)
    runnable(manifest)
    digest = checksum(manifest, source)
    yield from trust(digest, namespace)
    compile(source, "hooks.py", "exec")
    if len(sandbox.readable(manifest.get("capabilities", []))) > sandbox.MAX_ACTIONS:
        raise Fail(f"a codex may declare at most {sandbox.MAX_ACTIONS} read operations")
    key, current = f"{manifest['id']}@{manifest['version']}", active()
    if current and current.id == key:
        raise Conflict(f"{key} is already active")
    row = Codex[key] or Codex(id=key)
    row.version, row.checksum, row.manifest, row.source = manifest["version"], digest, manifest, source
    row.capabilities, row.installed_at = manifest.get("capabilities", []), chain.now()
    switch_to(row, current)
    if isinstance(current, LayeredCodex):
        legislation.load(None)
    init = yield from hook("init")
    return {"codex": key, "init": init}


@op("codex.install", governed=realm_wide, returns="codex")
def codex_activate(c, args):
    """Make the pending codex, named by its ``codex`` checksum (``sha256:<hex>``), the active one now; the codex it
    replaces is kept to revert to, and its ``on_codex_activate`` hooks run in the next message. Conflict when no codex
    is pending or it is another one. ``at``, when given, is not in the future: a pending codex is activated at once.
    Returns ``codex``."""
    waiting = pending()
    if waiting is None:
        raise Conflict("no codex is pending")
    if args["codex"] != waiting.id:
        raise Conflict(f"{args['codex']} is not the pending codex")
    if args.get("at") and int(args["at"]) > chain.now():
        raise Fail("this realm activates a pending codex at once: at is not in the future")
    switch(waiting)
    return {"codex": waiting.id}


@op("codex.uninstall", governed=realm_wide)
def codex_uninstall(c, args):
    """Deactivate the active codex; the realm then runs without one."""
    codex = active()
    if codex is None:
        raise Conflict("no codex is installed")
    codex.status = "inactive"
    if isinstance(codex, LayeredCodex):
        legislation.load(None)
    return {"codex": codex.id}


def legacy_with_checksum(digest):
    """The legacy codex whose ``checksum`` is ``digest``. It is not indexed: a realm has few legacy codices."""
    return next((row for row in Codex.load_some(1, max(Codex.max_id(), 1)) if row.checksum == digest), None)


def named(value):
    """The codex of either kind that ``value`` names: a layered codex by its id, which is its checksum; a legacy codex by
    its ``checksum`` (with or without the ``sha256:`` GGG writes) or by the ``name@version`` the history lists it by."""
    if not isinstance(value, str) or not value:
        raise Fail("codex is the checksum of the codex to restore")
    found = Codex[value] if "@" in value else LayeredCodex[value] or legacy_with_checksum(value.removeprefix("sha256:"))
    if found is None:
        raise NotFound(f"{value} is not in the history of this realm's codices")
    return found


def restoring(args):
    """The earlier codex a revert restores: the one ``args["codex"]`` names, else the one the active codex replaced. The
    active codex is not an earlier one, nor is a pending one: only ``codex_activate`` activates that."""
    current = active()
    if current is None:
        raise Conflict("no codex is active")
    if args.get("codex"):
        target = named(args["codex"])
    elif current.previous:
        target = find_codex(current.previous)
    else:
        raise Conflict("there is no previous codex to revert to")
    if target.id == current.id:
        raise Conflict(f"{target.id} is the active codex already")
    if target.status == "pending":
        raise Conflict(f"{target.id} is pending: codex_activate activates it")
    return target


def only_previous(target):
    previous = active().previous
    if target.id != previous:
        raise Conflict(f"codex_revert_fast restores only the previous codex ({previous or 'none'})")


def restore(target):
    """The revert: ``target``, an earlier codex of either kind, is the active codex again and the one it replaces is
    reverted. No ``on_codex_activate`` hook runs (§10.5), and nothing else about the codex changes: it keeps the voting
    rule and window it was recorded with. A capital's quarters restore it too (api.propagation). Returns ``codex``."""
    if isinstance(target, Codex):
        runnable(target.manifest or {})
    current = active()
    current.status, target.status = "reverted", "active"
    if isinstance(current, LayeredCodex) or isinstance(target, LayeredCodex):
        legislation.warm(target if isinstance(target, LayeredCodex) else None)
    store.remember_in_force(chain.now())
    journal.codex_reverted(target, current)
    from api import propagation  # it imports the install task, which imports this module

    propagation.reverted(target)  # a capital tells its quarters; a quarter reports to its capital
    return {"codex": target.id}


def recorded_rule(action):
    """The rule and window recorded with the codex that ``action`` restores, when it is a revert (``codex_revert``, or
    ``codex_revert_fast``, which keeps its department's rule but runs under the window) and that codex has them: what
    decides its vote (§10.1). None for any other action, and for a codex with no record: its vote follows the rule and
    window in force."""
    if not (isinstance(action, dict) and action.get("op") in ("codex_revert", "codex_revert_fast")
            and (action.get("args") or {}).get("codex")):
        return None
    return named(action["args"]["codex"]).voting_rule or None


def names_the_codex(args):
    """A revert that becomes a proposal names the codex it restores, found when it is filed, so that what the vote
    approves is what runs."""
    return {**args, "codex": restoring(args).id}


def filed_revert(args, proposal):
    """A revert filed as a proposal names a codex that can be restored now."""
    if not args.get("codex"):
        raise Fail("a revert filed as a proposal names the codex it restores")
    restoring(args)


def fast_target(args):
    """The codex a fast revert restores: the previous one, and only while a team is designated, whatever files or
    replays the revert."""
    it_team_governs(args)
    target = restoring(args)
    only_previous(target)
    return target


def filed_fast_revert(args, proposal):
    filed_revert(args, proposal)
    fast_target(args)


def it_team_governs(args):
    """The IT admin team's department governs a fast revert, under its own policy; no team, no fast revert."""
    team = it_team.department()
    if team is None:
        raise Conflict("this realm has designated no IT admin team: codex_revert_fast is refused")
    return team


@op("codex.revert", governed=realm_wide, names=names_the_codex, fixes=filed_revert)
def codex_revert(c, args):
    """Go back to an earlier codex of either kind, if this realm still runs its codex API: the one named by ``codex`` (its
    checksum), else the one the active codex replaced. Filed as a proposal, it names the codex it restores, and the vote
    follows the rule recorded with that codex. No legislation runs for it, and no ``on_codex_activate`` hook. Returns
    ``codex``."""
    return restore(restoring(args))


@op("codex.revert_fast", governed=it_team_governs, names=names_the_codex, fixes=filed_fast_revert)
def codex_revert_fast(c, args):
    """Go back to the codex the active one replaced, on the approval of the IT admin team (``it_team_set``): its
    department's own policy decides, m of n, quorum and veto. Refused when no team is designated. No legislation runs
    for it, and no ``on_codex_activate`` hook. Returns ``codex``."""
    return restore(fast_target(args))


def shown(codex):
    return view(codex, *(SHOWN if isinstance(codex, Codex) else LAYERED_SHOWN))


def history(args):
    """One page of every codex the realm has had: the legacy ones, then the layered ones, each in install order.
    ``next`` is the ``from`` of the next page, None at the end."""
    start, count = window(args)
    legacy_count = Codex.max_id()
    rows = Codex.load_some(start, count) if start <= legacy_count else []
    if len(rows) < count:
        rows += LayeredCodex.load_some(max(start - legacy_count, 1), count - len(rows))
    return rows, (start + count if start + count <= legacy_count + LayeredCodex.max_id() else None)


@op("realm.data_view", read=True)
def codex_status(c, args):
    """The active codex, whether it is healthy (a legacy codex: its last hook succeeded; a layered codex: no chapter's
    method is failing, see ``failures``: per chapter and method, the count, the last error and when), its hooks and
    configuration, and one page of the install ``history``: ``count`` installs (default 100, at most 500) from row ``from`` (default 1), with
    ``next``, the ``from`` of the next page (null at the end). The history lists the legacy codices, then the layered
    ones. A layered codex shows its layers, layer paths and chapters (source, version, checksum, who vouched for it,
    route, fetch time), its status and ``activates_at``. ``pending`` is the layered codex waiting for its time, and
    ``job`` the install job running or paused, else the latest, with its step, status and error. ``it_team`` is the
    department the admins designated to revert fast ("" for none). ``journal_failure`` is the last entry the official
    journal could not record (the change it was for, when, and why), or None."""
    codex, problem = active(), Realm["1"].codex_install_state or {}
    problem = problem if codex and problem.get("codex") == codex.id else {}
    legacy_codex = codex if isinstance(codex, Codex) else None
    failures = codex.failures if isinstance(codex, LayeredCodex) else {}
    rows, nxt = history(args)
    waiting, newest = pending(), latest(InstallJob, 1)
    job = store.live_job() or (newest[0] if newest else None)
    return {"codex": shown(codex) if codex else None, "healthy": codex is not None and not problem and not failures,
            "problem": problem, "failures": failures, "hooks": (legacy_codex.manifest or {}).get("hooks", []) if legacy_codex else [],
            "config": config(legacy_codex) if legacy_codex else {},
            "history": [view(x, "id", "version", "status", "installed_at") for x in rows], "next": nxt,
            "pending": shown(waiting) if waiting else None,
            "job": view(job, *JOB_SHOWN) if job else None, "it_team": it_team.department_id(),
            "journal_failure": journal.failure()}
