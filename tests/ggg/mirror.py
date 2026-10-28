"""Run the acceptance tests through the GGG methods: ``GGG_MIRROR=1 pytest tests/ggg``.

Each operation a test calls goes through its ggg_ method instead: the test's JSON arguments become GGG
arguments, the method runs the operation, and its reply is checked against the types of ggg.did. The test
then gets the operation's own JSON reply, so its assertions still hold. What went wrong is collected in
PROBLEMS, by method; with GGG_MIRROR=strict the first problem fails the test.
"""

import json
import re

import fake_chain
import ggg_api
import ggg_methods
from core import op

PROBLEMS = {}
USED = set()
SUCCEEDED = set()
SAMPLES = {}  # method -> its successful replies, to find fields that are always empty


class FakePrincipal(str):
    """The test world's principals are names (``dana``), which a real Principal refuses."""

    @classmethod
    def from_str(cls, text):
        return cls(text)

    def to_str(self):
        return str(self)


def check(v, t, path):
    """Raise if ``v`` is not a value of GGG type ``t`` (a ggg_did.SCHEMA type expression)."""
    k, detail = ggg_api.kind(t)
    if k == "time":
        assert isinstance(v, int) and v >= 0, f"{path}: time {v!r}"
    elif k == "opt":
        if v is not None:
            check(v, detail, path)
    elif k == "vec":
        assert isinstance(v, list), f"{path}: not a list: {v!r}"
        for i, x in enumerate(v):
            check(x, detail, f"{path}[{i}]")
    elif k == "tuple":
        assert isinstance(v, tuple) and len(v) == len(detail), f"{path}: not a tuple: {v!r}"
        for i, (x, a) in enumerate(zip(v, detail)):
            check(x, a, f"{path}.{i}")
    elif k == "record":
        want = ggg_api.fields(t)
        assert isinstance(v, dict) and set(v) == set(want), f"{path}: {t} keys {sorted(v)} != {sorted(want)}"
        for f, ft in want.items():
            check(v[f], ft, f"{path}.{f}")
    elif k == "variant":
        cases = ggg_api.fields(t)
        assert isinstance(v, dict) and len(v) == 1 and next(iter(v)) in cases, f"{path}: {t} case {v!r}"
        case = next(iter(v))
        check(v[case], cases[case], f"{path}.{case}")
    elif t == "principal":
        assert isinstance(v, FakePrincipal), f"{path}: not a principal: {v!r}"
    elif t in ("bool", "blob", "text"):
        assert isinstance(v, {"bool": bool, "blob": bytes, "text": str}[t]), f"{path}: not {t}: {v!r}"
    elif t == "null":
        assert v is None, f"{path}: not null: {v!r}"
    else:
        assert isinstance(v, int) and not isinstance(v, bool), f"{path}: not a number: {v!r}"


# Each generated method's argument and reply types, as it passes them to ggg_api.
SIGNATURES = {m[0]: (None if m[1] == "None" else m[1].strip('"'), m[2]) for m in re.findall(
    r'^def (\w+)\(.*?(?:read|run)\("\w+", (None|"[^"]+"), \w+, "([^"]+)"\)',
    open(ggg_methods.__file__).read(), re.M)}


def signature(fn):
    return SIGNATURES[fn.__name__]


# The test's JSON arguments, reshaped where ggg_api.ARGS reshapes GGG arguments for the operation.
UNARGS = {
    "department_set_policy": lambda a: {"department": a.get("department"),
                                        "policy": {k: a[k] for k in ("m", "n", "quorum_percent", "veto") if k in a}},
    "treasury_configure": lambda a: a.get("config", {}),
    "delegation_list": lambda a: {"role": "grantor" if "from_grantor" in a else "delegate", **a},
    "notification_read": lambda a: {k: v for k, v in a.items() if k != "all"},
}


class Unrepresentable(Exception):
    """The test passes what a GGG argument cannot hold: a Realms GOS-only field, or a value of another type."""


def representable(args, arg_t, key):
    known = {ggg_api.PERCENT.get((arg_t, f), ggg_api.RENAME.get((arg_t, f), f))
             for f in ggg_api.fields(arg_t)} | {key, "on_behalf_of"}
    if set(args) - known:
        return False
    for f, ft in ggg_api.fields(arg_t).items():
        v, t = args.get(f), ggg_api.unwrap(ft)
        if v is None:
            continue
        if t == "bool" and not isinstance(v, bool) or t == "text" and not isinstance(v, str):
            return False
        if t == "blob" and not (isinstance(v, str) and all(c in "0123456789abcdef" for c in v.removeprefix("sha256:"))):
            return False
        if t == "Value" and ("True" in repr(v) or "False" in repr(v)):
            return False
    return True


def same(a, b):
    """``b``, the test's argument after a trip through GGG, still says ``a``: a bare id may come back as ``Kind:id``."""
    if isinstance(a, dict) and isinstance(b, dict):
        return all(same(v, b.get(k)) for k, v in a.items() if v is not None)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, str) and isinstance(b, str):
        return a == b or b.endswith(":" + a) or a.removeprefix("sha256:") == b
    return a == b and type(a) is type(b)


def ggg_args(name, arg_t, args):
    garg = convert(name, arg_t, args)
    if arg_t is not None:
        back = ggg_api.request(name, arg_t, garg)
        want = {k: v for k, v in args.items() if k not in ("from", "count", "expires_in", "expires_at", "all")}
        if not same(want, {k: v for k, v in back.items()}):
            raise Unrepresentable
    return garg


def convert(name, arg_t, args):
    args = dict(args)
    if "expires_in" in args:
        args["expires_at"] = fake_chain.state["now"] + args.pop("expires_in")
    if name in UNARGS:
        args = UNARGS[name](args)
    key = ggg_api.KEY.get(name, name.split("_")[0])
    if arg_t is None:
        return None
    paged = ggg_api.is_record(arg_t) and "cursor" in ggg_api.fields(arg_t)
    if "from" in args and paged:
        args["cursor"] = str(args.pop("from"))
    if "count" in args and paged and name not in ggg_api.KEEP_LIMIT:
        args["limit"] = args.pop("count")
    if ggg_api.is_record(arg_t) and not representable(args, arg_t, key):
        raise Unrepresentable
    if ggg_api.is_record(arg_t):
        if "id" in ggg_api.fields(arg_t) and key in args:
            args["id"] = args.pop(key)
        garg = ggg_api.to_ggg(args, arg_t)
        for f in ggg_api.fields(arg_t):  # what the test left out stays out, not "" or 0
            if ggg_api.PERCENT.get((arg_t, f), ggg_api.RENAME.get((arg_t, f), f)) not in args:
                garg[f] = None
        return garg
    if ggg_api.is_variant(arg_t):
        return ggg_api.to_ggg(args, arg_t)
    return ggg_api.to_ggg(args.get(key), arg_t)


# Arguments a test passes that GGG covers another way.
UNARGS_OK = {"delegation_list": ("from_grantor", "from_delegate"), "notification_read": ("all",)}


def note(name, problem):
    PROBLEMS.setdefault(name, []).append(problem)
    if fake_chain.state.get("mirror") == "strict":
        raise AssertionError(f"ggg_{name}: {problem}")


def call(name, caller, args, original):
    fn = getattr(ggg_methods, f"ggg_{name}", None)
    if fn is None or (name == "setup_step" and args.get("step") in ("install", "infrastructure")) \
            or (args and signature(fn)[0] is None):
        return original(name, caller, **args)  # not a GGG method, or a Realms GOS setup step
    fake_chain.state["caller"] = caller
    seen = {}
    real_reply = ggg_api.reply

    def capture(op_name, op_args, out, reply_t):
        seen["out"], seen["args"] = dict(out), op_args
        return real_reply(op_name, op_args, out, reply_t)

    arg_t, reply_t = signature(fn)
    try:
        garg = ggg_args(name, arg_t, args)
    except Exception:  # the test passes what a GGG argument cannot hold
        return original(name, caller, **args)
    ggg_api.reply, ggg_api.STRICT = capture, []
    try:
        result = fn(garg) if arg_t is not None else fn()
        if hasattr(result, "send"):
            result = op.run(result)
        check(result, reply_t, f"ggg_{name}")
        if not isinstance(result, dict) or "Ok" in result:
            SUCCEEDED.add(name)
            SAMPLES.setdefault(name, []).append(result.get("Ok") if isinstance(result, dict) else result)
        if ggg_api.STRICT:
            note(name, f"no value for {sorted(set(ggg_api.STRICT))}")
        if "Err" in result and seen.get("out", {}).get("ok"):
            note(name, f"the operation succeeded but the method replied {result}")
    except AssertionError as e:
        note(name, str(e))
    except Exception as e:  # a crash in the method: note it, and let the test go on
        note(name, f"{type(e).__name__}: {e}")
    finally:
        ggg_api.reply, ggg_api.STRICT = real_reply, None
    USED.add(name)
    if "out" not in seen:
        return original(name, caller, **args)
    lost = sorted(set(args) - set(seen["args"]) - {"from", "count", "on_behalf_of", "expires_in"} - set(UNARGS_OK.get(name, ())))
    if lost:
        note(name, f"arguments lost on the way: {lost}")
    return seen["out"]


def report():
    lines = [f"{len(USED)} methods mirrored, {len(SUCCEEDED)} with a successful call, {len(PROBLEMS)} with problems",
             "never successful: " + " ".join(sorted(USED - SUCCEEDED))]
    for name in sorted(PROBLEMS):
        problems = sorted(set(PROBLEMS[name]))
        lines.append(f"ggg_{name}: " + " | ".join(p[:300] for p in problems[:4]))
    return "\n".join(lines)


def empties(v, path=""):
    """The paths in a reply that hold nothing."""
    if isinstance(v, dict):
        return {p for k, x in v.items() for p in empties(x, f"{path}.{k}")}
    if isinstance(v, list) and v:
        return {p for x in v for p in empties(x, f"{path}[]")}
    return {path} if v in (None, "", 0, [], b"", {"Map": []}) and not isinstance(v, bool) else set()


def always_empty():
    out = {}
    for name, replies in SAMPLES.items():
        paths = set.intersection(*(empties(r) for r in replies))
        if paths:
            out[name] = sorted(paths)
    return out


def dump(path):
    with open(path, "w") as f:
        f.write(report() + "\n")
        for name, paths in sorted(always_empty().items()):
            f.write(f"empty in every reply of ggg_{name}: {' '.join(paths)}\n")
        json.dump(sorted(USED), f)
