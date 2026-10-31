"""The GGG interface: every method of ggg.did (ggg_methods) calls its operation here, through ``dispatch``.

Each call converts the GGG arguments to the operation's JSON, runs the operation (so permissions, governance
and the audit log apply as for ``read`` and ``run``), and builds the GGG reply, mostly from the rows the
operation touched. The conversion follows the types of ggg_did: GGG has nanoseconds, principals, variants,
references as records and ratios in basis points, where Realms GOS has seconds, text, lower-case text,
``Kind:id`` and percentages. Types are ggg_did.SCHEMA's type expressions (``"opt vec Ref"``): the canister's
Python keeps no types at run time. tests/ggg/test_candid.py checks the methods against ggg.did.
"""
import json

from basilisk import Principal  # tests replace it, to accept their names for principals

import ggg
import ggg_did
from api.proposals import settings
from api.setup import VERSION
from core import chain
from core.candid import Principal as ParsedPrincipal, Reader, idl_hash, quote
from core.op import dispatch, ops, run as drive
from ggg_did import METHODS, SCHEMA

NS = 1_000_000_000
STANDARDS = (("ICRC-10", "https://github.com/dfinity/ICRC/blob/main/ICRCs/ICRC-10/ICRC-10.md"),
             ("GGG", f"https://github.com/smart-social-contracts/ggg/blob/v{ggg_did.VERSION}/ggg.did"))
# GGG fields in basis points, and the Realms GOS field holding the same ratio in percent.
PERCENT = {("Policy", "quorum"): "quorum_percent", ("Allocation", "share"): "share", ("Criterion", "weight"): "weight",
           ("Proposal", "threshold"): "threshold", ("Proposal", "quorum"): "quorum_percent",
           ("GovernanceUpdateArgs", "voting_threshold"): "threshold",
           ("GovernanceUpdateArgs", "voting_quorum"): "quorum_percent"}
# GGG fields Realms GOS names otherwise.
RENAME = {("DelegationGrantArgs", "scope"): "operations", ("DelegationRequestArgs", "scope"): "operations",
          ("DelegationDecideArgs", "scope"): "operations", ("QuarterRegisterArgs", "canister"): "quarter",
          ("ResolutionQueryArgs", "search"): "text"}
# GGG lists of pairs that Realms GOS keeps as a map, with the fields holding the key and the value.
MAPS = {("TenderCreateArgs", "rubric"): ("criterion", "weight"), ("Tender", "rubric"): ("criterion", "weight"),
        ("AllocationRuleDraftArgs", "rules"): ("fund", "share"), ("AllocationRule", "rules"): ("fund", "share"),
        ("CredentialIssueArgs", "fields"): (0, 1), ("CredentialIssued", "header"): (0, 1),
        ("CredentialVerifyArgs", "header"): (0, 1), ("KeyGrantArgs", "keys"): ("principal", "wrapped_dek"),
        ("DocumentStoreArgs", "keys"): ("principal", "wrapped_dek"),
        ("DocumentShareArgs", "keys"): ("principal", "wrapped_dek"), ("BidScoreArgs", "scores"): ("criterion", "score"),
        ("Fund", "balances"): ("symbol", "amount")}
# GGG lists of records that Realms GOS keeps as a map from one field to the rest.
KEYED = {("BidsRevealArgs", "openings"): "bid"}
# Set to a list by the tests: every required field a reply had no value for is added to it.
STRICT = None
KINDS = {}


class BadArgument(Exception):
    pass


# ---- Types


def kind(t):
    """A type expression as ``(kind, detail)``: ``opt`` or ``vec`` and the inner type, ``tuple`` and its types,
    ``time``, ``record`` or ``variant`` and the name, or ``prim`` and the Candid type."""
    if t not in KINDS:
        if t.startswith(("opt ", "vec ")):
            KINDS[t] = (t[:3], t[4:])
        elif t.startswith("("):
            KINDS[t] = ("tuple", t[1:-1].split(", "))
        elif t in ("Timestamp", "Duration"):
            KINDS[t] = ("time", t)
        else:
            KINDS[t] = (SCHEMA[t][0], t) if t in SCHEMA else ("prim", t)
    return KINDS[t]


def fields(t):
    return SCHEMA[t][1]


def is_record(t):
    return t is not None and kind(t)[0] == "record"


def is_variant(t):
    return t is not None and kind(t)[0] == "variant"


def unwrap(t):
    return t[4:] if t.startswith("opt ") else t


def camel(text):
    return "".join(part.capitalize() for part in str(text).split("_"))


def snake(name):
    return "".join("_" + ch.lower() if ch.isupper() else ch for ch in name).lstrip("_")


def entity(t):
    """The ggg entity class a GGG record type stands for, or None."""
    cls = getattr(ggg, t, None) if is_record(t) else None
    return cls if isinstance(cls, type) and hasattr(cls, "load_some") else None


# ---- Realms GOS to GGG


def get(x, name):
    if x is None:
        return None
    if isinstance(x, dict):
        return x.get(name)
    return getattr(x, name, None)


def ident(x):
    if isinstance(x, (str, int)):
        return str(x)
    if isinstance(x, dict):
        return str(x.get("id", ""))
    return str(getattr(x, "id", None) or getattr(x, "_id", ""))


def principal(x):
    return Principal.from_str(ident(x))


def many(x):
    if x is None or x == "":
        return []
    if isinstance(x, dict):
        return list(x.values())
    return list(x)


def blob(x):
    if isinstance(x, bytes):
        return x
    try:
        return bytes.fromhex((x or "").removeprefix("sha256:"))
    except ValueError:
        return x.encode()


def value(x):
    """Any JSON-like data as an ICRC-3 value."""
    if isinstance(x, bool):
        return {"Nat": int(x)}
    if isinstance(x, int):
        return {"Nat": x} if x >= 0 else {"Int": x}
    if isinstance(x, bytes):
        return {"Blob": x}
    if isinstance(x, (list, tuple)):
        return {"Array": [value(i) for i in x]}
    if isinstance(x, dict):
        return {"Map": [(str(k), value(v)) for k, v in x.items()]}
    if x is None:
        return {"Map": []}
    return {"Text": x if isinstance(x, str) else ident(x) if hasattr(x, "_id") else str(x)}


def ref(x):
    if isinstance(x, dict):
        return {"kind": str(x.get("kind", "")), "id": str(x.get("id", ""))}
    if hasattr(x, "_id"):
        return {"kind": type(x).__name__, "id": ident(x)}
    kind, _, key = str(x or "").rpartition(":")
    if not kind and key:  # Realms GOS takes a bare id for an organization of that name, or else a user
        kind = "Organization" if ggg.Organization[key] else "User"
    return {"kind": kind, "id": key}


def unset(x, t):
    return x is None or x == "" or (x == 0 and kind(t)[0] == "time")


def to_ggg(x, t):
    """Realms GOS data (a row, a dict, text, seconds) as a value of GGG type ``t``."""
    k, detail = kind(t)
    if k == "time":
        return int(EPOCHS.get(x, x) or 0) * NS  # Realms GOS names some periods ("monthly")
    if k == "opt":
        return None if unset(x, detail) else to_ggg(x, detail)
    if k == "vec":
        return [to_ggg(i, detail) for i in many(x)]
    if k == "tuple":
        return tuple(to_ggg(i, a) for i, a in zip(x, detail))
    if t == "Value":
        return value(x)
    if t == "Ref":
        return ref(x)
    if k == "record":
        # An operation's view of a row, or its id: the record is built from the row itself.
        row = load(t, x) if entity(t) and isinstance(x, (str, dict)) else None
        return record(row or x, t)
    if k == "variant":
        return variant(x, t)
    if t == "principal":
        return principal(x)
    if t == "bool":
        return bool(x)
    if t == "blob":
        return blob(x)
    if t == "text":
        return "" if x is None else ident(x)
    if t == "null":
        return None
    return len(x) if isinstance(x, list) else int(x or 0)  # a count, where the operation lists what it counted


def record(x, name):
    fixed = FIX[name](x) if name in FIX else {}
    out = {}
    for w, ft in fields(name).items():
        if w in fixed:
            raw = fixed[w]
        elif (name, w) in PERCENT:
            raw = get(x, PERCENT[name, w])
            raw = None if raw is None else int(raw) * 100
        else:
            raw = get(x, RENAME.get((name, w), w))
        if (name, w) in KEYED and isinstance(raw, dict):
            raw = [{KEYED[name, w]: key, **val} for key, val in raw.items()]
        if (name, w) in MAPS and isinstance(raw, dict):
            k, v = MAPS[name, w]
            raw = [(key, val) if k == 0 else {k: key, v: val} for key, val in raw.items()]
        if STRICT is not None and raw is None and kind(ft)[0] not in ("opt", "vec"):
            STRICT.append(f"{name}.{w}")
        out[w] = to_ggg(raw, ft)
    return out


def variant(x, name):
    cases = fields(name)
    if name in FIX:
        x = FIX[name](x)
    if isinstance(x, dict) and len(x) == 1 and next(iter(x)) in cases:
        case, payload = next(iter(x.items()))
        return {case: to_ggg(payload, cases[case])}
    case = camel(x)
    if case not in cases:
        if STRICT is not None:
            STRICT.append(f"{name}: {x!r}")
        case = next(iter(cases))
    return {case: to_ggg(None if cases[case] == "null" else x, cases[case])}


# The values a GGG record has where the Realms GOS row has none, or another shape (docs/ggg/spec.md §1).
def realm_fix(r):
    from api import quarters

    rules = settings()
    # A quarter counts its own members; a capital adds what its quarters last reported.
    members = quarters.population() + (0 if quarters.federated() else sum(q.population for q in quarters.known()))
    return {"member_count": members, "voting_threshold": int(rules.get("threshold", 50)) * 100,
            "voting_quorum": int(rules.get("quorum_percent", 0)) * 100,
            "installed_version": get(r, "installed_version") or VERSION,
            "accounting_currency_decimals": get(r, "accounting_currency_decimals") if get(r, "accounting_currency") else None}


def action(a):
    """A Realms GOS proposal action, ``{op, args}``, as a GGG ProposalAction: a call of the operation's ggg_ method
    with its arguments in Candid. An operation GGG lacks keeps its name, with its JSON arguments."""
    a = a or {}
    if isinstance(a, dict) and len(a) == 1 and next(iter(a)) in ("Motion", "Call", "Code"):
        return a
    if not a.get("op"):
        return {"Motion": None}
    op, args = a["op"], a.get("args") or {}
    if op == "codex_install" and args.get("checksum"):
        return {"Code": {"url": args.get("url", ""), "checksum": args["checksum"]}}
    if op not in METHODS:
        return {"Call": {"method": op, "arg": json.dumps(args, sort_keys=True).encode()}}
    arg_t = METHODS[op][0]
    text = f"({to_candid(ggg_args(op, args), arg_t)})" if arg_t else "()"
    return {"Call": {"method": f"ggg_{op}", "arg": chain.candid_encode(text)}}


def scope(row, key):
    s = get(row, "scope")
    return s.get(key) if isinstance(s, dict) else None


FIX = {
    "Realm": realm_fix,
    "Member": lambda m: {"user": get(m, "user") or ident(m)},
    "User": lambda u: {"private_data": get(u, "private_data") if ident(u) == chain.caller() else None},
    "ProposalAction": action,
    "Mandate": lambda m: {"max_value": get(m, "max_value") or scope(m, "max_value"),
                          "symbol": get(m, "symbol") or scope(m, "symbol")},
    "FederalVote": lambda v: {"title": get(v, "title") or (get(v, "action") or {}).get("title", "")},
    "Event": lambda e: {"agent": bool(get(e, "agent"))},
    "Proposal": lambda p: {"action": action(get(p, "action")),  # a realm-wide proposal names no department
                           "department": None if getattr(get(p, "department"), "is_root", False) else get(p, "department")},
    # The replayed operation's JSON reply stands in for a Candid one.
    "ProposalResult": lambda r: r if "Reply" in r or "Error" in r else {"Error": r.get("error", "")}
    if not r.get("ok", True) else {"Reply": json.dumps(r, sort_keys=True).encode()},
    "DelegableOperation": lambda d: {"operation": get(d, "operation") or get(d, "op")},
    "Delegation": lambda d: {"scope": scope(d, "operations") or []},
    "DelegationRequest": lambda d: {"scope": scope(d, "operations") or []},
    "Policy": lambda p: {"m": get(p, "m") or 1, "n": get(p, "n") or 1, "veto": get(p, "veto") or [],
                         "quorum": int(get(p, "quorum_percent") or 0) * 100},
    "UserEntry": lambda e: entry(get(e, "user") or e),
    "Verdict": lambda v: {"issued_by": get(v, "issued_by") or ""},
    "License": lambda lic: {"issued_by": get(lic, "issued_by") or ""},
    "CodexSource": lambda c: c if "Package" in c or "Registry" in c else
    {"Package": c} if "manifest" in c else {"Registry": c},
    "SetupStep": lambda s: s if len(s) == 1 else {camel(s.get("step")): {k: v for k, v in s.items() if k != "step"}},
    "DepartmentEditArgsHead": lambda h: h if isinstance(h, dict) else {"Set": h} if h else {"Clear": None},
}


# Realms GOS names its treasury epochs; GGG has their length.
EPOCHS = {"monthly": 30 * 86400, "quarterly": 91 * 86400, "yearly": 365 * 86400}


def entry(user):
    uid = ident(user)
    return {"user": uid, "member": ggg.Member[uid]}


def unaction(case, payload):
    if case == "Code":
        return {"op": "codex_install", "args": payload}
    if case != "Call":
        return None
    method, data = payload["method"], bytes.fromhex(payload["arg"])
    op = method.removeprefix("ggg_")
    if method.startswith("ggg_") and op in METHODS:
        if not data.startswith(b"DIDL"):
            raise BadArgument(f"the arguments of {method} are not Candid")
        try:
            values = Reader(chain.candid_decode(data)).args()
        except ValueError as e:
            raise BadArgument(f"the arguments of {method} do not decode: {e}")
        arg_t = METHODS[op][0]
        if len(values) != (1 if arg_t else 0):
            raise BadArgument(f"{method} takes {1 if arg_t else 0} argument(s), not {len(values)}")
        return {"op": op, "args": request(op, arg_t, from_candid(values[0], arg_t)) if arg_t else {}}
    if op != method and op in ops():  # an operation only Realms GOS has, with JSON arguments
        return {"op": op, "args": json.loads(data or b"{}")}
    raise BadArgument(f"this realm has no method {method}")


# Variants Realms GOS writes otherwise, from their GGG case and its converted payload.
UNFIX = {
    "ProposalAction": unaction,
    "CodexSource": lambda case, payload: payload,
    "SetupStep": lambda case, payload: {"step": snake(case), **payload},
    "DepartmentEditArgsHead": lambda case, payload: payload or "",
}


def ggg_args(op, args):
    """An operation's JSON arguments as the GGG argument of its method: the other way from ``request``."""
    arg_t, args = METHODS[op][0], dict(args or {})
    if arg_t is None:
        return None
    if not is_record(arg_t):
        return to_ggg(args if is_variant(arg_t) else args.get(KEY.get(op, op.split("_")[0])), arg_t)
    hints, key = fields(arg_t), KEY.get(op, op.split("_")[0])
    if "id" in hints and key in args:
        args["id"] = args.pop(key)
    if "cursor" in hints and "from" in args:
        args["cursor"] = str(args.pop("from"))
    if "limit" in hints and "count" in args:
        args["limit"] = args.pop("count")
    global STRICT
    strict, STRICT = STRICT, None  # an argument left out takes its default: not a reply missing a value
    try:
        return to_ggg(args, arg_t)
    finally:
        STRICT = strict


def to_candid(v, t):
    """A GGG value of type ``t`` as candid text, with every field named by its number."""
    k, detail = kind(t)
    if k == "time":
        return f"{v} : nat64"
    if k == "opt":
        return "null" if v is None else f"opt {to_candid(v, detail)}"
    if k == "vec":
        return "vec { " + "; ".join(to_candid(i, detail) for i in v) + " }"
    if k == "tuple":
        return "record { " + "; ".join(to_candid(i, a) for i, a in zip(v, detail)) + " }"
    if k == "record":
        return "record { " + "; ".join(f"{idl_hash(f)} = {to_candid(v[f], ft)}" for f, ft in fields(t).items()) + " }"
    if k == "variant":
        case, payload = next(iter(v.items()))
        return f"variant {{ {idl_hash(case)} = {to_candid(payload, fields(t)[case])} }}"
    if t == "text":
        return quote(v)
    if t == "bool":
        return "true" if v else "false"
    if t == "principal":
        return f"principal {quote(v.to_str() if hasattr(v, 'to_str') else str(v))}"
    if t == "blob":
        return 'blob "' + "".join(f"\\{b:02x}" for b in v) + '"'
    return "null" if t == "null" else f"{v} : {t}"


def from_candid(v, t):
    """A parsed candid value (core.candid.Reader) as a GGG value of type ``t``; BadArgument if it is not one."""
    k, detail = kind(t)
    if k == "opt":
        return None if v is None else from_candid(v, detail)
    if k == "vec" and isinstance(v, list):
        return [from_candid(i, detail) for i in v]
    if k == "tuple" and isinstance(v, dict):
        return tuple(from_candid(v.get(i), a) for i, a in enumerate(detail))
    if k in ("record", "variant") and isinstance(v, dict):
        named = {idl_hash(f): f for f in fields(t)}
        got = {named.get(key, key): val for key, val in v.items()}
        if k == "variant":
            case = next(iter(got), None)
            if len(got) != 1 or case not in fields(t):
                raise BadArgument(f"not a {t}: {v!r}")
            return {case: from_candid(got[case], fields(t)[case])}
        return {f: from_candid(got.get(f), ft) for f, ft in fields(t).items()}
    if t == "principal" and isinstance(v, ParsedPrincipal):
        return Principal.from_str(v)
    if t == "text" and isinstance(v, str) and not isinstance(v, ParsedPrincipal):
        return v
    if (t == "blob" and isinstance(v, bytes)) or (t == "bool" and isinstance(v, bool)) or (t == "null" and v is None):
        return v
    if k in ("time", "prim") and isinstance(v, int) and not isinstance(v, bool) and t not in ("text", "blob", "bool"):
        return v
    raise BadArgument(f"not a {t}: {v!r}")


# ---- GGG to Realms GOS


def unvalue(v):
    case, x = next(iter(v.items()))
    if case == "Array":
        return [unvalue(i) for i in x]
    if case == "Map":  # ICRC-3 has no null: Realms GOS's null travels as an empty map
        return {k: unvalue(i) for k, i in x} or None
    return x.hex() if case == "Blob" else x


def to_realms(x, t):
    """A GGG value of type ``t`` as the JSON an operation takes."""
    if x is None:
        return None
    k, detail = kind(t)
    if k == "time":
        return int(x) // NS
    if k == "opt":
        return to_realms(x, detail)
    if k == "vec":
        return [to_realms(i, detail) for i in x]
    if k == "tuple":
        return [to_realms(i, a) for i, a in zip(x, detail)]
    if t == "Value":
        return unvalue(x)
    if t == "Ref":
        return f"{x['kind']}:{x['id']}"
    if k == "record":
        out = {}
        for w, ft in fields(t).items():
            v = x.get(w)
            if v is None:
                continue
            if (t, w) in PERCENT:
                out[PERCENT[t, w]] = int(v) // 100
            elif (t, w) in KEYED:
                key = KEYED[t, w]
                out[w] = {item[key]: {f: v2 for f, v2 in item.items() if f != key} for item in to_realms(v, ft)}
            elif (t, w) in MAPS:
                key, val = MAPS[t, w]
                out[w] = {pair[key]: pair[val] for pair in to_realms(v, ft)}
            else:
                out[RENAME.get((t, w), w)] = to_realms(v, ft)
        return out
    if k == "variant":
        case, payload = next(iter(x.items()))
        payload = to_realms(payload, fields(t)[case])
        if t in UNFIX:
            return UNFIX[t](case, payload)
        return snake(case) if payload is None else {case: payload}
    if hasattr(x, "to_str"):
        return x.to_str()
    if isinstance(x, bytes):
        return x.hex()
    return x


def request(op, arg_t, arg):
    """The JSON arguments of ``op`` for a GGG argument of type ``arg_t``."""
    if arg_t is None:
        args = {}
    elif is_record(arg_t) or is_variant(arg_t):
        args = to_realms(arg, arg_t)
        if "id" in args:  # GetArgs and the like: the operation names the id after what it finds
            args[KEY.get(op, op.split("_")[0])] = args.pop("id")
    else:
        args = {KEY.get(op, op.split("_")[0]): to_realms(arg, arg_t)}
    cursor = args.pop("cursor", None)
    if cursor is not None:
        if not str(cursor).isdigit():
            raise BadArgument(f"malformed cursor '{cursor}'")
        args["from"] = int(cursor)
    if "limit" in args and op not in KEEP_LIMIT:
        args["count"] = args.pop("limit")
    return ARGS[op](args, arg) if op in ARGS else args


# ---- Calls


def refused(op, e):
    """An argument that does not convert: but a caller who must sign in first is told that first."""
    if chain.caller() == chain.ANONYMOUS and not ops()[op]["public"]:
        return {"Err": {"Unauthenticated": None}}
    return {"Err": {"InvalidArgument": str(e)}}


def read(op, arg_t, arg, reply_t):
    if op in LOCAL:
        return LOCAL[op](arg)
    if op == "user_get" and user_get(arg):
        return user_get(arg)
    try:
        args = request(op, arg_t, arg)
    except BadArgument as e:
        return refused(op, e)
    return reply(op, args, json.loads(drive(dispatch(op, json.dumps(args), query=True))), reply_t)


def run(op, arg_t, arg, reply_t):
    if op in LOCAL:
        return LOCAL[op](arg)
    try:
        args = request(op, arg_t, arg)
    except BadArgument as e:
        return refused(op, e)
    out = yield from dispatch(op, json.dumps(args))
    return reply(op, args, json.loads(out), reply_t)


def error(out):
    """The GggError for a refusal: dispatch names its kind (core.db.Fail)."""
    kind, message = out.get("kind", "invalid_argument"), out.get("error", "")
    if kind == "forbidden":
        return {"Forbidden": {"permission": out.get("permission"), "message": message}}
    if kind in ("not_found", "unauthenticated"):
        return {camel(kind): None}
    return {camel(kind): message}


def reply(op, args, out, reply_t):
    ok_t = fields(reply_t)["Ok"]
    if not out.pop("ok"):
        err = error(out)
        if "NotFound" in err and ok_t.startswith("opt "):
            return {"Ok": None}
        return {"Err": err}
    raw = BUILD[op](out, args) if op in BUILD else result(op, out, ok_t)
    return {"Ok": to_ggg(raw, ok_t)}


def load(t, key):
    cls = entity(t)
    if cls is None or key in (None, ""):
        return None
    if isinstance(key, dict):
        key = key.get("id")
    return cls[str(key)]


def result(op, out, t):
    """The model's result in an operation's reply: under the key its @op ``returns`` names (a record by id, a page's
    list, a value, the id a governed operation created), or the reply itself."""
    key, optional, t = ops()[op]["returns"], t.startswith("opt "), unwrap(t)
    if t == "Governed":
        return {"Proposed": out["proposal"]} if out.get("requires_vote") else {"Done": out.get(key) if key else None}
    if key is None:
        return out
    if key not in out and STRICT is not None and not optional:
        STRICT.append(f"{op} returns {key}")
    if is_record(t) and "items" in fields(t) and "next" in fields(t):
        return {**out, "items": out.get(key) or [], "next": nxt(out)}
    return out.get(key)


def user_get(p):
    """A caller is always answered about itself; anyone else needs ``user.view``."""
    me = chain.caller()
    if to_realms(p, "principal") != me:
        return None
    return {"Ok": None if me == chain.ANONYMOUS or not ggg.User[me] else to_ggg(me, "UserEntry")}


LOCAL = {
    "icrc10_supported_standards": lambda _: [{"name": n, "url": u} for n, u in STANDARDS],
    "realm_info": lambda _: to_ggg(ggg.Realm["1"], "Realm"),
}
# The argument key of an operation whose GGG method takes one bare value (or an ``id``), when it isn't the
# operation's first word.
KEY = {"crypto_envelope": "scope", "crypto_derive_key": "transport_public_key", "private_data_update": "private_data",
       "federal_vote_finalize": "vote", "federal_vote_cancel": "vote", "tax_policy_propose": "policy",
       "allocation_rule_propose": "rule", "representative_remove": "appointment", "document_get": "document",
       "dispute_get": "dispute", "report_view": "report"}
# Operations that take ``limit`` as such, not as a page size.
KEEP_LIMIT = {"document_delete"}


def seat_owner(args):
    a = ggg.Appointment[args.get("appointment", "")]
    org = a and a.position and a.position.organization
    return {**args, "organization": ident(org)} if org else args


def delegations(out, args):
    side = "grantor" if args.get("role") == "grantor" else "delegate"
    nxt = (out.get("next") or {}).get(side)
    return {"items": out.get(f"as_{side}", []), "requests": out.get("requests", []),
            "next": None if nxt is None else str(nxt)}


def me(out, args):
    mine = out.get("delegations") or {}
    return {**entry(out["user"]), "permissions": out.get("permissions", []),
            "delegations": mine.get("as_grantor", []) + mine.get("as_delegate", []), "requests": mine.get("requests", [])}


def treasury(out, args):
    return {"treasury": ggg.Treasury["1"], "tokens": out.get("tokens", []), "nft_tokens": out.get("nft_tokens", []),
            "funds": list(out.get("funds") or {}), "next": None if out.get("next") is None else str(out["next"])}


def federal_vote(out, args):
    """The vote, or on a quarter, whose capital holds it, what the quarter knows of it."""
    from api import quarters

    row = ggg.FederalVote[out.get("vote", "")]
    out.setdefault("deadline", chain.now() + quarters.published_rule()["window"])
    return row or {"id": out.get("vote"), "title": args.get("title"), "action": {**args["action"], "title": args["title"]},
                   "origin_quarter": chain.self_id(), "status": "open", "deadline": out.get("deadline"),
                   "hash": out.get("hash", ""), "rule": {}, "tally": {}}


def page_of(key, item=None):
    def build(out, args):
        rows = out.get(key) or []
        nxt = out.get("next")
        return {**out, "items": [item(r) for r in rows] if item else rows, "next": None if nxt is None else str(nxt)}
    return build


# Argument and reply adapters, for operations whose shapes differ beyond names and units.
ARGS = {
    "department_set_policy": lambda a, g: {**{k: v for k, v in a.items() if k != "policy"}, **a.get("policy", {})},
    "treasury_configure": lambda a, g: {"config": a},
    "representative_remove": lambda a, g: seat_owner(a),
    "delegation_list": lambda a, g: {"from_" + a.get("role", "delegate"): a.get("from"), "count": a.get("count")},
    "notification_read": lambda a, g: {**a, "all": True} if "notification" not in a else a,
}


def chart(out, args):
    c = out["chart"]
    return {"department": c["department"], "positions": len(c.get("positions", [])), "seats": c.get("seats", 0),
            "filled": c.get("filled", 0), "children": [x["department"] for x in c.get("children", [])], "next": None}


def proposal_detail(out, args):
    pid = out["proposal"]["id"]
    return {"proposal": pid, "votes": [ggg.Vote[f"{pid}:{v['voter']}"] for v in out.get("votes", [])],
            "next": None if out.get("next") is None else str(out["next"])}


def nxt(out):
    return None if out.get("next") is None else str(out["next"])


def detail(kind, out):
    """A department or organization with its seats: each position and its holders."""
    return {kind: out[kind]["id"], "next": None,
            "seats": [{"position": p["id"], "holders": [h["id"] for h in p.get("holders", [])]}
                      for p in out.get("positions", [])]}


def directory(out):
    return {"capital": out.get("capital"), "items": out.get("quarters", []), "next": None}


def local(name, **args):
    """Another operation's reply, as the caller."""
    from core.op import call

    return call(name, args, user=chain.caller())


BUILD = {
    "invite_create": lambda o, a: {"invite": o["invite"]["id"], "code": o["invite"]["code"]},
    "me": me,
    "delegation_list": delegations,
    "my_invoices": page_of("invoices", lambda i: {"invoice": i["id"], "pay_to": i["pay_to"]}),
    "resolution_query": page_of("verdicts", lambda v: {"verdict": v["id"], "penalties": v.get("penalties", [])}),
    "treasury_view": treasury,
    "department_get": lambda o, a: detail("department", o),
    "organization_get": lambda o, a: detail("organization", o),
    "tender_get": lambda o, a: {"tender": o["tender"], "bids": o["tender"].get("bids", []), "next": None},
    "invoice_refresh": lambda o, a: {"items": list(o.get("invoices") or {}), "next": nxt(o)},
    "quarter_directory": lambda o, a: directory(o),
    "quarter_sync": lambda o, a: directory(local("quarter_directory")),
    "federal_vote_propose": federal_vote,
    "department_chart": chart,
    "proposal_get": proposal_detail,
    "enforcement_record": lambda o, a: ggg.Event[str(ggg.Event.max_id())],
    "credential_verify": lambda o, a: {"valid": o.get("valid"), **(o.get("checks") or {})},
    "codex_status": lambda o, a: {**o, "checksum": (o.get("codex") or {}).get("id"),
                                  "history": [h["id"] for h in o.get("history", [])], "signing": signing()},
}


def signing():
    from api import delegation

    return {"agents": sorted(delegation.signing()), "delegable": sorted(delegation.delegable()),
            "supervision": "required"}
