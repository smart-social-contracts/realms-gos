import builtins
import json
import sys

import chain
import sdk
from db import Fail
from op import dispatch, ops, run

try:
    import _basilisk_sandbox
except ImportError:  # unit tests: no subinterpreters, hooks run in a fresh namespace instead
    _basilisk_sandbox = None

BUDGET = 10_000_000  # bytecode instructions per call, metered by the sandbox (deterministic)
MAX_ACTIONS = 32  # _basilisk_sandbox traps on a longer allow-list
LOADER = ("import sys\n_sdk = type(sys)('ggg_sdk')\nexec(compile(%r, 'ggg_sdk.py', 'exec'), _sdk.__dict__)\n"
          "sys.modules['ggg_sdk'] = _sdk\n" % sdk.SOURCE)


def sha256(text):
    if _basilisk_sandbox is not None:
        return _basilisk_sandbox.sha256(text)
    import hashlib  # not in the canister's stdlib

    return hashlib.sha256(text.encode()).hexdigest()


def plain(value):
    return json.loads(json.dumps(value))


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
        results.append((yield from apply(name, resolve(kwargs, results), declared)))
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


def apply(name, args, declared):
    if name not in ops():
        return {"ok": False, "error": f"unknown operation '{name}'"}
    reply = yield from dispatch(name, json.dumps(args), origin="codex", signer=chain.self_id(), declared=declared)
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
