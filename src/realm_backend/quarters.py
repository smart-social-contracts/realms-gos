import hashlib
import json
import math

import chain
import proposals
from db import Fail, each, find, next_id, view
from ggg import Codex, Forward, GuestUser, Member, Quarter, Realm, User, UserProfile
import codex as runtime
from op import op, run

CAPACITY, TEST_CAPACITY, TEST_NETWORKS, SCALE_AT = 2000, 10, ("test", "staging", "demo", "local"), 0.9
GUEST = ["self.data_view", "realm.data_view"]
MOVES = {"setup": {"active"}, "active": {"suspended", "splitting", "merging"}, "suspended": {"active"},
         "splitting": {"active"}, "merging": {"active"}}
RULE = {"kind": "majority_of_quarters", "threshold": 50, "absent": "wait"}
KINDS, ABSENT = ("majority_of_quarters", "majority_of_members"), ("wait", "no", "ignore")
FIELDS = ("id", "name", "index", "status", "population", "codex_id", "codex_version", "last_sync")


def realm():
    return Realm["1"]


def digest(data):
    return "sha256:" + hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def federated():
    """True on a quarter of someone else's federation; False on a capital or an independent realm."""
    r = realm()
    return r.is_quarter and not r.is_capital


def capital():
    return realm().federation_realm_id if federated() else chain.self_id()


def population():
    return sum(1 for m in each(Member) if m.status == "active")


def codex():
    for x in each(Codex):
        if x.status == "active":
            return {"id": (x.manifest or {}).get("id", x.id.split("@")[0]), "version": x.version}
    return {"id": "", "version": ""}


def capital_codex():
    return (realm().sync_state or {}).get("codex", {}) if federated() else codex()


def drifting(version):
    return version != capital_codex().get("version", "")


def report():
    mine = codex()
    return {"population": population(), "codex_id": mine["id"], "codex_version": mine["version"]}


def me():
    r, state = realm(), realm().sync_state or {}
    return {"id": chain.self_id(), "name": r.name, "index": state.get("index", 0) if federated() else 0,
            "status": "setup" if r.status == "setup" else "active", "last_sync": state.get("at", 0), **report()}


def directory():
    rows = [me()] + [view(q, *FIELDS) for q in each(Quarter)]
    for row in rows:
        row["capital"], row["drifting"] = row["id"] == capital(), drifting(row["codex_version"])
    return rows


def published_rule():
    return {**RULE, "window": proposals.window(), **(realm().manifest_data or {}).get("federation", {}).get("rule", {})}


def scale():
    r = realm()
    n = int((r.manifest_data or {}).get("scaling", {}).get("quarter_capacity")
            or (TEST_CAPACITY if r.network in TEST_NETWORKS else CAPACITY))
    pops = {row["id"]: row["population"] for row in directory()}
    at = math.ceil(SCALE_AT * n)
    return {"populations": pops, "capacity": n, "scale_at": at, "enabled": r.auto_scale_enabled,
            "wanted": r.auto_scale_enabled and max(pops.values()) >= at,
            "in_flight": r.scale_in_flight, "requested_at": r.scale_requested_at}


def ask(cid, method, payload):
    reply = yield from chain.call_text(cid, method, json.dumps(payload))
    return json.loads(reply or '{"ok": false, "error": "no reply"}')


def registered(principal):
    q = Quarter[principal]
    if q is None:
        raise Fail(f"{principal} is not a registered quarter canister")
    return q


def from_capital(c):
    if not federated() or c.by != capital():
        raise Fail("only this quarter's capital may send this")


def record(c, args):
    """A quarter's report reaches the capital: population, codex and optionally a federal vote leg."""
    if federated():
        raise Fail("quarters report to their capital")
    q = registered(c.by)
    if args.get("leg"):
        import federation

        federation.record_leg(q, args["leg"])
    q.population = int(args.get("population", q.population))
    q.codex_id, q.codex_version = args.get("codex_id", q.codex_id), args.get("codex_version", q.codex_version)
    q.last_sync = q.last_activity_at = chain.now()
    if q.status == "setup":
        q.status = "active"
    r = realm()
    if scale()["wanted"] and not r.scale_in_flight:
        # Intent only: Casals provisions the canister out of band, then quarter_register clears the flag.
        r.scale_in_flight, r.scale_requested_at = True, chain.now()
    return q


def apply_directory(reply):
    seen, index = set(), 0
    for row in reply["directory"]:
        if row["id"] == chain.self_id():
            index = row["index"]
            continue
        q = Quarter[row["id"]] or Quarter(id=row["id"])
        for k in FIELDS[1:]:
            setattr(q, k, row[k])
        seen.add(q.id)
    for q in [q for q in each(Quarter) if q.id not in seen]:
        q.delete()
    realm().sync_state = {"at": chain.now(), "codex": reply["codex"], "index": index}


@op("quarter.register", governed=proposals.realm_wide)
def quarter_register(c, args):
    cid = args["quarter"]
    if federated():
        raise Fail("only the capital registers quarters")
    if Quarter[cid] or cid == chain.self_id():
        raise Fail(f"{cid} is already part of this federation")
    index = max([q.index for q in each(Quarter)] + [0]) + 1
    Quarter(id=cid, name=args.get("name", f"Quarter {index}"), index=index, last_activity_at=chain.now())
    r = realm()
    r.is_quarter, r.scale_in_flight = True, False
    return {"quarter": cid, "index": index}


@op("quarter.deregister", governed=proposals.realm_wide)
def quarter_deregister(c, args):
    if federated():
        raise Fail("only the capital deregisters quarters")
    find(Quarter, args["quarter"]).delete()


@op("quarter.configure", governed=proposals.realm_wide)
def quarter_configure(c, args):
    q = find(Quarter, args["quarter"]) if args.get("quarter") else None
    status, rule = args.get("status"), args.get("rule")
    if status and (q is None or status not in MOVES.get(q.status, ())):
        raise Fail(f"a {q.status if q else 'missing'} quarter cannot become {status}")
    if rule and (rule.get("kind", RULE["kind"]) not in KINDS or rule.get("absent", "wait") not in ABSENT
                 or not 0 <= int(rule.get("threshold", 50)) < 100):
        raise Fail(f"a federal rule has kind {KINDS}, absent {ABSENT} and a threshold below 100")
    if q is not None:
        q.name, q.status = args.get("name", q.name), status or q.status
    r = realm()
    for k in ("auto_scale_enabled", "quarter_join_mode", "federation_codex"):
        if k in args:
            setattr(r, k, args[k])
    data = r.manifest_data or {}
    if "capacity" in args:
        data["scaling"] = {**data.get("scaling", {}), "quarter_capacity": int(args["capacity"])}
    if rule:
        data["federation"] = {**data.get("federation", {}), "rule": {**RULE, **rule}}
    r.manifest_data = data
    return {"quarter": view(q, *FIELDS) if q else None, "rule": published_rule()}


@op("quarter.secede", governed=proposals.realm_wide)
def quarter_secede(c, args):
    if not federated():
        raise Fail("only a quarter of a federation can secede")
    old = capital()
    yield from ask(old, "federation_message", {"topic": "quarter.secede", "body": {}})
    r = realm()
    r.is_quarter, r.is_capital, r.federation_realm_id, r.sync_state = False, True, "", {}
    for q in list(each(Quarter)):
        q.delete()
    return {"left": old}


@op("quarter.join_federation", governed=proposals.realm_wide)
def quarter_join_federation(c, args):
    r, cid = realm(), args["capital"]
    if r.is_quarter or cid == chain.self_id():
        raise Fail("this realm already belongs to a federation")
    r.is_quarter, r.is_capital, r.federation_realm_id = True, False, cid
    # The capital becomes a known quarter so its calls are accepted (I-FED-4).
    Quarter(id=cid, name=args.get("name", "capital"), index=0, status="active")
    return {"capital": cid}


@op(None, read=True, public=True)
def quarter_directory(c, args):
    return {"capital": capital(), "quarters": directory()}


@op(None, read=True, public=True)
def join_targets(c, args):
    """Least-populated active quarter; the capital only takes joins until sub-quarters exist."""
    active = [q for q in directory() if q["status"] == "active"]
    pool = [q for q in active if not q["capital"]] or active
    best = min(pool, key=lambda q: (q["population"], -q["index"]))["id"] if pool else ""
    chosen = run(runtime.hook("assign_quarter", {"user": c.user, "quarters": pool}))
    if chosen and chosen.get("ok") and (chosen.get("result") or {}).get("quarter") in [q["id"] for q in pool]:
        best = chosen["result"]["quarter"]
    return {"default": best, "targets": [q["id"] for q in pool], "mode": realm().quarter_join_mode}


@op(None, read=True, public=True)
def scale_status(c, args):
    return scale()


@op("self.change_quarter", delegable=True)
def self_change_quarter(c, args):
    dest, here = args["quarter"], chain.self_id()
    member, user = Member[c.user], User[c.user]
    if member is None or member.status != "active" or user is None:
        raise Fail("only an active member of this quarter can move")
    q = Quarter[dest]
    if q is None or q.status != "active":
        raise Fail(f"{dest} is not an active quarter of this federation")
    moved_at, subject = chain.now(), f"User:{c.user}"
    signature = digest({"subject": subject, "from": here, "to": dest, "moved_at": moved_at})
    reply = yield from ask(dest, "federation_message", {"topic": "user.arrive", "body": {
        "user": c.user, "nickname": user.nickname, "avatar": user.avatar, "from": here,
        "moved_at": moved_at, "signature": signature}})
    if not reply.get("ok"):
        raise Fail(f"{dest} refused the move: {reply.get('error')}")
    f = Forward(id=next_id(Forward, "FWD"), subject=subject, origin=here, destination=dest, moved_at=moved_at,
                signature=signature)
    member.delete()
    user.home_quarter, user.profiles = dest, []
    guest = GuestUser[c.user] or GuestUser(id=c.user)
    guest.home_quarter, guest.permissions = dest, GUEST
    return {"forward": f.id, "home_quarter": dest}


@op("quarter.sync")
def quarter_sync(c, args):
    """On a quarter: report to the capital and take its directory. On the capital: answer that call."""
    if federated():
        reply = yield from ask(capital(), "quarter_sync", report())
        if not reply.get("ok"):
            raise Fail(f"the capital refused to sync: {reply.get('error')}")
        apply_directory(reply)
        return {"quarters": len(reply["directory"]), "drifting": drifting(codex()["version"])}
    q = record(c, args)
    return {"directory": directory(), "codex": codex(), "index": q.index}


@op("quarter.sync")
def quarter_report(c, args):
    q = record(c, args)
    return {"drifting": drifting(q.codex_version)}


def on_arrive(c, body):
    who = body["user"]
    if digest({"subject": f"User:{who}", "from": c.by, "to": chain.self_id(), "moved_at": body["moved_at"]}) \
            != body["signature"]:
        raise Fail("the forward's signature does not match")
    m = Member[who]
    if m and m.status == "banned":
        raise Fail(f"{who} is banned here")
    user = User[who] or User(id=who, nickname=body.get("nickname", ""), avatar=body.get("avatar", ""))
    user.home_quarter, user.profiles = chain.self_id(), [UserProfile["member"]]
    m = m or Member(id=who, joined_at=chain.now())
    m.status = "active"
    if GuestUser[who]:
        GuestUser[who].delete()
    return {"member": who}


def on_secede(c, body):
    find(Quarter, c.by).delete()
    return {"removed": c.by}


def on_codex(c, body):
    from_capital(c)
    r = realm()
    r.sync_state = {**(r.sync_state or {}), "codex": {"id": body["id"], "version": body["version"]}}
    return {"drifting": drifting(codex()["version"])}
