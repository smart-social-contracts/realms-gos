import builtins
import json
import sys

from core import chain
from core import sdk
from core.db import Fail
from core.op import dispatch, ops, run

try:
    import _basilisk_sandbox
except ImportError:  # unit tests: no subinterpreters, hooks run in a fresh namespace instead
    _basilisk_sandbox = None

NATIVE = _basilisk_sandbox is not None  # whether subinterpreters exist here (the canister), or the fallbacks run
BUDGET = 10_000_000  # bytecode instructions per call, metered by the sandbox (deterministic)
MAX_ACTIONS = 32  # _basilisk_sandbox traps on a longer allow-list
LOADER = ("import sys\n_sdk = type(sys)('ggg_sdk')\nexec(compile(%r, 'ggg_sdk.py', 'exec'), _sdk.__dict__)\n"
          "sys.modules['ggg_sdk'] = _sdk\n" % sdk.SOURCE)


def module_source(name):
    """The source text of the bundled module ``name``, and whether it is a package. Neither runs the module.

    The canister keeps every bundled module's source on its lazy module object (never read an attribute of one: that
    would compile and run it); on a laptop the source is read from the file."""
    module = sys.modules.get(name)
    if module is None:  # a laptop, before the module was imported
        __import__(name)
        module = sys.modules[name]
    source = vars(module).get("_bsrc")
    if source is None:
        with open(module.__file__) as f:
            source = f.read()
    return source, "__path__" in vars(module)


def module_sources(package):
    """``{name: (source, is_package)}`` of ``package``, its parents and everything bundled under it: what a
    subinterpreter needs to import the package from text, as it has no path to import from."""
    names = {n for n in sys.modules if n == package or n.startswith(package + ".")}
    if len(names) <= 1:  # a laptop, before the submodules were imported: they are files
        import pkgutil

        __import__(package)
        names = {package} | {m.name for m in pkgutil.walk_packages(sys.modules[package].__path__, package + ".")}
    parents = {n.rpartition(".")[0] for n in names} - {""}
    return {name: module_source(name) for name in sorted(names | parents)}


class Resident:
    """A subinterpreter kept open across calls: ``program`` runs once at spawn, then ``call(fn, **kwargs)`` runs
    its top-level functions with plain data in and out, until ``close``. Inside, ``rpc(action, **kw)`` reaches
    ``rpc(action, kw)`` here for the actions in ``allowed``. The budget is per spawn, cumulative over the calls:
    a call that exhausts it fails with ``BudgetExceeded`` in its error text, and the interpreter is spent.

    Without ``_basilisk_sandbox`` (unit tests), the same program runs in a fresh namespace, unmetered."""

    def __init__(self, program, rpc, allowed=(), budget=BUDGET):
        self.handle = self.space = None

        def handler(_, action, kw):
            return plain(rpc(action, plain(kw)))

        if _basilisk_sandbox is None:

            def ask(action, **kw):  # as the native boundary does: the host's error crosses as text, in a RuntimeError
                try:
                    return handler(None, action, kw)
                except Exception as e:
                    raise RuntimeError(f"rpc failed: {e}")

            self.space = {"__name__": "__sandbox__", "__builtins__": dict(vars(builtins), rpc=ask)}
            exec(compile(program, "sandbox.py", "exec"), self.space)
            return
        digest = _basilisk_sandbox.sha256(program)
        _basilisk_sandbox.approve_hash(digest)
        try:
            self.handle = _basilisk_sandbox.spawn_subinterpreter(program, digest, "", tuple(allowed), handler, budget)
        finally:
            _basilisk_sandbox.revoke_hash(digest)

    def call(self, fn, **kwargs):
        """``fn(**kwargs)`` inside; raises RuntimeError with the error's text when the call itself fails."""
        if self.space is not None:
            try:
                return plain(self.space[fn](**plain(kwargs)))
            except Exception as e:
                raise RuntimeError(f"sandboxed call raised: {type(e).__name__}: {e}")
        return plain(_basilisk_sandbox.call_in_subinterpreter(self.handle, fn, plain(kwargs)))

    def close(self):
        if self.handle is not None:
            _basilisk_sandbox.close_subinterpreter(self.handle)
        self.handle = self.space = None


def sha256(text):
    if _basilisk_sandbox is not None:
        return _basilisk_sandbox.sha256(text)
    import hashlib  # not in the canister's stdlib

    return hashlib.sha256(text.encode()).hexdigest()


def plain(value):
    return json.loads(json.dumps(value))


# What a legacy codex's effect, which acts as the realm, never does: link the realm to another or publish its record
# (api.links), which only the realm's own vote may do.
VOTED = ("sync_source_add", "sync_source_remove", "follower_accept", "follower_remove", "legislation_release_publish")


def execute(source, fn, kwargs, rpc, allowed=()):
    """Call ``fn(**kwargs)`` of ``source`` in a fresh subinterpreter and return its plain-data result.

    Inside, ``rpc(action, **kw)`` calls ``rpc(action, kw)`` here, for the actions in ``allowed``.
    """
    if _basilisk_sandbox is None:
        return plain(local(source, fn, plain(kwargs), rpc))
    text = LOADER + source
    digest = _basilisk_sandbox.sha256(text)
    _basilisk_sandbox.approve_hash(digest)

    def handler(_, action, kw):
        return plain(rpc(action, plain(kw)))

    handle = _basilisk_sandbox.spawn_subinterpreter(text, digest, "", tuple(allowed), handler, BUDGET)
    out = _basilisk_sandbox.call_in_subinterpreter(handle, fn, plain(kwargs))
    _basilisk_sandbox.close_subinterpreter(handle)
    _basilisk_sandbox.revoke_hash(digest)
    return plain(out)


def local(source, fn, kwargs, rpc):
    """The same call in a fresh namespace, with ``ggg_sdk`` and ``rpc`` provided the same way."""
    module = type(sys)("ggg_sdk")
    env = dict(vars(builtins), rpc=lambda action, **kw: plain(rpc(action, plain(kw))))
    env["__import__"] = lambda name, *a, **k: module if name == "ggg_sdk" else builtins.__import__(name, *a, **k)
    module.__dict__["__builtins__"] = env
    exec(compile(sdk.SOURCE, "ggg_sdk.py", "exec"), module.__dict__)
    space = {"__name__": "codex", "__builtins__": env}
    exec(compile(source, "hooks.py", "exec"), space)
    return space[fn](**kwargs)


def call(source, fn, args, context, declared):
    """Run a codex hook, then apply the effects it returned, as the realm with codex authority.

    A generator: an effect may call other canisters. Returns ``{ok, result, effects}`` with one
    ``{op, ok, ...}`` per effect, or ``{ok: false, error}`` when the hook itself failed.
    """
    reads = readable(declared)
    envelope = execute(source, fn, {"args": args, "context": context},
                       lambda action, kw: read(action, kw, declared), reads)
    if not envelope["ok"]:
        return envelope
    results = []
    for name, kwargs in envelope["effects"]:
        results.append((yield from apply(name, resolve(kwargs, results), declared, legacy=True)))
    return {"ok": True, "result": resolve(envelope["result"], results),
            "effects": [{"op": e[0], **r} for e, r in zip(envelope["effects"], results)]}


def readable(declared):
    """The declared operations a hook may call for data; the rest it returns as effects."""
    return [a for a in declared if ops().get(a, {}).get("read")]


def read(action, args, declared):
    if not ops().get(action, {}).get("read"):
        raise Fail(f"{action} is not a read operation: return it as an effect")
    return json.loads(run(dispatch(action, json.dumps(args), origin="codex", signer=chain.self_id(),
                                   declared=declared)))


def apply(name, args, declared, legacy=False):
    """Run ``name`` as the codex (origin ``codex``, the realm as signer); ``legacy`` marks a legacy codex's effect,
    which acts for the realm where a layered codex's holds nothing (core.op.Call), except in what takes the realm's
    vote (``VOTED``)."""
    if name not in ops():
        return {"ok": False, "error": f"unknown operation '{name}'"}
    if legacy and name in VOTED:
        return {"ok": False, "error": f"{name} takes the realm's vote, which a legacy codex does not hold"}
    reply = yield from dispatch(name, json.dumps(args), origin="codex", signer=chain.self_id(), declared=declared,
                                legacy=legacy)
    return json.loads(reply)


def resolve(value, results):
    """Replace ``$eff:<n>:<key>`` references with ``results[n][key]``."""
    if isinstance(value, dict):
        return {k: resolve(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, results) for v in value]
    if isinstance(value, str) and "$eff:" in value:
        for i, r in enumerate(results):
            for k, v in r.items():
                value = value.replace(f"$eff:{i}:{k}", str(v))
    return value
