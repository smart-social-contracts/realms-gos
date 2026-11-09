import hashlib
import json
import math

from core import chain
from core import compliance
from api import assets, proposals
from core.db import Conflict, Fail, Forbidden, Unavailable, each, find, first, next_id, view
from ggg import Codex, Forward, GuestUser, Member, Quarter, Realm, User, UserProfile
from api import codex as runtime
from core.op import filing, op, run
from core.pending import quoted
from core.ratio import ONE, PERCENT

CAPACITY, TEST_CAPACITY, TEST_NETWORKS, SCALE_AT = 2000, 10, ("test", "staging", "demo", "local"), 0.9
GUEST = ["self.data_view", "realm.data_view"]
# Every sync carries the whole directory, so a federation has a fixed most quarters.
MAX_QUARTERS = 500
STATUSES = ("setup", "active", "suspended", "splitting", "merging")
MOVES = {"setup": {"active"}, "active": {"suspended", "splitting", "merging"}, "suspended": {"active"},
         "splitting": {"active"}, "merging": {"active"}}
RULE = {"kind": "majority_of_quarters", "threshold": ONE // 2, "absent": "wait"}
KINDS, ABSENT = ("majority_of_quarters", "majority_of_members"), ("wait", "no", "ignore")
FIELDS = ("id", "name", "index", "status", "population", "codex_id", "codex_version", "last_sync")
# Seconds between looks at the conductor while Casals builds a quarter quarter_add asked for.
ADOPT_EVERY = 60


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
    return Member.count_by("status", "active")


def codex():
    x = first(Codex, "status", "active")
    if x is None:
        return {"id": "", "version": ""}
    return {"id": (x.manifest or {}).get("id", x.id.split("@")[0]), "version": x.version}


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


def known():
    for status in STATUSES:
        yield from each(Quarter, "status", status)


def zones_held():
    """The zones of the realm that members live in here, each with how many: what a quarter reports to its capital."""
    return {z.id: z.population for z in assets.defined_zones() if z.population}


def zone_definitions():
    """The zones of the realm as ``zone_define`` takes them: what the capital sends its quarters, without a population."""
    return [{"zone": z.id, "name": z.name, "kind": z.kind} for z in assets.defined_zones()]


def zones_of(cid):
    """The zones ``cid`` holds members of: this realm's own, or what the quarter last reported; None where this realm
    is a quarter and knows only itself."""
    if cid == chain.self_id():
        return zones_held()
    return None if federated() else Quarter[cid].zones


def directory(zones=False):
    """The capital and every quarter. ``zones`` adds the zones each holds members of; the sync broadcast leaves them out,
    since every quarter would store them."""
    rows = [me()] + [view(q, *FIELDS) for q in known()]
    for row in rows:
        row["capital"], row["drifting"] = row["id"] == capital(), drifting(row["codex_version"])
        if zones:
            row["zones"] = zones_of(row["id"])
    return rows


def published_rule():
    return {**RULE, "window": proposals.window(), **(realm().manifest_data or {}).get("federation", {}).get("rule", {})}


def capacity():
    """The members one quarter takes: the realm's own figure, else its network's default."""
    r = realm()
    return int((r.manifest_data or {}).get("scaling", {}).get("quarter_capacity")
               or (TEST_CAPACITY if r.network in TEST_NETWORKS else CAPACITY))


def scale():
    """Quarter populations against the capacity per quarter; whether a new quarter is ``wanted``; ``in_flight`` once
    one is proposed or being built, and the ``pending`` one Casals is building."""
    r, n = realm(), capacity()
    pops = {row["id"]: row["population"] for row in directory()}
    at = math.ceil(SCALE_AT * n)
    return {"populations": pops, "capacity": n, "scale_at": at, "enabled": r.auto_scale_enabled,
            "wanted": r.auto_scale_enabled and max(pops.values()) >= at,
            "in_flight": r.scale_in_flight, "requested_at": r.scale_requested_at, "pending": pending()}


def ask(cid, name, payload):
    reply = yield from chain.call_op(cid, name, payload)
    return json.loads(reply or '{"ok": false, "error": "no reply"}')


def registered(principal):
    q = Quarter[principal]
    if q is None:
        raise Forbidden(f"{principal} is not a registered quarter canister")
    return q


def from_capital(c):
    if not federated() or c.by != capital():
        raise Forbidden("only this quarter's capital may send this")


def record(c, args):
    """A quarter's report reaches the capital: population, codex and optionally a federal vote leg."""
    if federated():
        raise Conflict("quarters report to their capital")
    q = registered(c.by)
    if args.get("leg"):
        from api import federation

        federation.record_leg(q, args["leg"])
    q.population = int(args.get("population", q.population))
    if "zones" in args:
        q.zones = {zone: int(n) for zone, n in args["zones"].items()}
    q.codex_id, q.codex_version = args.get("codex_id", q.codex_id), args.get("codex_version", q.codex_version)
    q.last_sync = q.last_activity_at = chain.now()
    if q.status == "setup":
        q.status = "active"
    want_quarter()
    return q


def want_quarter():
    """On the capital, once scale_status wants a quarter: the realm proposes its next quarter itself and the members
    decide (quarter_add). Checked whenever a population changes: a quarter reports, a member joins."""
    r = realm()
    if not federated() and scale()["wanted"] and not r.scale_in_flight:
        r.scale_in_flight, r.scale_requested_at = True, chain.now()
        proposals.file_replay(filing("quarter_add", {}, proposals.root()), chain.self_id(), chain.self_id())


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
    for q in [q for q in known() if q.id not in seen]:
        q.delete()
    apply_zones(reply.get("zones", []))
    realm().sync_state = {"at": chain.now(), "codex": reply["codex"], "index": index}


def apply_zones(definitions):
    """Define the capital's zones here, by id: the realm's codex defines its districts on the capital alone, and a zone
    this quarter has already is redefined in place. A zone the quarter defined itself stays; a member's home cell is
    left as it is."""
    for d in definitions:
        try:
            assets.define_zone(d["zone"], d["name"], d["kind"])
        except Conflict:  # a member's home cell
            pass


@op("quarter.register", governed=proposals.realm_wide, returns="quarter")
def quarter_register(c, args):
    """On the capital, add canister ``quarter`` to the federation, with optional ``name`` (default "Quarter <index>").
    Returns ``quarter`` and its ``index``."""
    if federated():
        raise Forbidden("only the capital registers quarters")
    return register(args["quarter"], args.get("name"))


def register(cid, name=None):
    if Quarter[cid] or cid == chain.self_id():
        raise Conflict(f"{cid} is already part of this federation")
    if Quarter.count() >= MAX_QUARTERS:
        raise Conflict(f"a federation has at most {MAX_QUARTERS} quarters")
    index = next_index()
    Quarter(id=cid, name=name or f"Quarter {index}", index=index, last_activity_at=chain.now())
    r = realm()
    r.is_quarter, r.scale_in_flight = True, False
    return {"quarter": cid, "index": index}


def next_index():
    return max([q.index for q in known()] + [0]) + 1


def casals():
    return (realm().manifest_data or {}).get("casals", {})


def pending():
    return ((realm().manifest_data or {}).get("scaling") or {}).get("pending")


def set_pending(value):
    r = realm()
    data = r.manifest_data or {}
    data["scaling"] = {**data.get("scaling", {}), "pending": value}
    r.manifest_data = data


@op("quarter.register", governed=proposals.realm_wide, returns="member")
def quarter_add(c, args):
    """On the capital, ask Casals for a new quarter, optionally called ``name``: the next ``<stand>-quarter-<n>``
    of the stand the realm runs on. Casals builds it on its own; the capital then makes it a quarter and registers
    it (see ``scale_status``). Returns ``member``, the canister Casals is building."""
    if federated():
        raise Forbidden("only the capital adds quarters")
    cas = casals()
    if not (cas.get("conductor_canister_id") and cas.get("casals_stand")):
        raise Unavailable("this realm does not know the Casals conductor and stand it runs on")
    if pending():
        raise Conflict(f"{pending()['member']} is still being built")
    n = next_index()
    member = f"{cas['casals_stand']}-quarter-{n}"
    arg = json.dumps({"stand": cas["casals_stand"], "members": [member]})
    reply = json.loads((yield from chain.call_text(cas["conductor_canister_id"], "stand_grow", arg)) or "{}")
    if not reply.get("ok"):
        raise Fail(f"Casals did not add {member}: {reply.get('error') or 'no reply'}")
    set_pending({"member": member, "name": args.get("name") or f"Quarter {n}", "requested_at": chain.now()})
    r = realm()
    r.scale_in_flight, r.scale_requested_at = True, chain.now()
    chain.set_timer(ADOPT_EVERY, adopt)
    return {"member": member}


def adopt():
    """Once Casals has built the pending quarter: make it a quarter of this capital (the capital is one of its
    controllers) and register it. Until then, look again in ADOPT_EVERY seconds."""
    p = pending()
    if not p:
        return
    cas = casals()
    try:
        arg = json.dumps({"stand": cas["casals_stand"]})
        view_ = json.loads((yield from chain.call_text(cas["conductor_canister_id"], "orchestra_get", arg)) or "{}")
        row = next((x for x in view_.get("canisters", []) if x.get("name") == p["member"]), None)
        if row is None or row.get("status") != "ready" or not row.get("canister_id"):
            chain.set_timer(ADOPT_EVERY, adopt)
            return
        cid = row["canister_id"]
        joined = json.loads((yield from chain.call_op(cid, "quarter_join_federation", {"capital": chain.self_id()}))
                            or "{}")
        if not joined.get("ok") and "already belongs" not in str(joined.get("error")):
            raise Fail(f"{p['member']} did not join: {joined.get('error')}")
        # The quarter takes the capital's registration rule, so the members it is offered to can join it.
        named = json.loads((yield from chain.call_op(cid, "setup_step", {
            "step": "realm", "name": p["name"], "open_registration": bool(realm().open_registration)})) or "{}")
        if not named.get("ok"):
            raise Fail(f"{p['member']} was not configured: {named.get('error')}")
        if not Quarter[cid]:
            register(cid, p["name"])
        set_pending(None)
        # Its first report makes it active, and joinable (join_targets offers active quarters).
        synced = json.loads((yield from chain.call_op(cid, "quarter_sync", {})) or "{}")
        if not synced.get("ok"):
            Quarter[cid].status = "setup"
    except Exception as e:
        set_pending({**p, "error": str(e)[:300]})
        chain.set_timer(ADOPT_EVERY, adopt)


def resume():
    """Timers do not survive an upgrade: keep looking for a quarter Casals is still building."""
    if pending():
        chain.set_timer(ADOPT_EVERY, adopt)


@op("quarter.deregister", governed=proposals.realm_wide)
def quarter_deregister(c, args):
    """On the capital, remove ``quarter`` from the federation."""
    if federated():
        raise Forbidden("only the capital deregisters quarters")
    find(Quarter, args["quarter"]).delete()


def federal_rule(args):
    """The federal vote ``rule`` of a ``quarter_configure`` call, refused if it is not one. A threshold of 1 to 99 is the
    previous release's whole percent: it would count as almost none, so it is refused, and every threshold under 100 that
    is stored is an old one (core.ratio.upgrade_federal_rule)."""
    rule = args.get("rule")
    if not rule:
        return
    threshold = int(rule.get("threshold", RULE["threshold"]))
    if 0 < threshold < 100:
        raise Fail(f"the threshold is in hundred-millionths: 60 % is {60 * PERCENT}")
    if rule.get("kind", RULE["kind"]) not in KINDS or rule.get("absent", "wait") not in ABSENT or not 0 <= threshold < ONE:
        raise Fail(f"a federal rule has kind {KINDS}, absent {ABSENT} and a threshold below {ONE} (100 %)")


@op("quarter.configure", governed=proposals.realm_wide, check=federal_rule)
def quarter_configure(c, args):
    """Set ``quarter``'s ``name`` or ``status`` (setup→active, active⇄suspended, active→splitting or merging→active),
    and realm-wide ``capacity``, ``auto_scale_enabled``, ``quarter_join_mode``, ``federation_codex`` and the federal
    vote ``rule``: kind majority_of_quarters or majority_of_members, absent wait, no or ignore, threshold below 100 %
    (100000000), in hundred-millionths."""
    q = find(Quarter, args["quarter"]) if args.get("quarter") else None
    status, rule = args.get("status"), args.get("rule")
    if status and (q is None or status not in MOVES.get(q.status, ())):
        raise Conflict(f"a {q.status if q else 'missing'} quarter cannot become {status}")
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
    """On a quarter, leave the federation: tell the capital, then run as an independent realm. Returns ``left``, the
    former capital."""
    if not federated():
        raise Forbidden("only a quarter of a federation can secede")
    old = capital()
    yield from ask(old, "federation_message", {"topic": "quarter.secede", "body": {}})
    r = realm()
    r.is_quarter, r.is_capital, r.federation_realm_id, r.sync_state = False, True, "", {}
    for q in list(known()):
        q.delete()
    return {"left": old}


@op("quarter.join_federation", governed=proposals.realm_wide)
def quarter_join_federation(c, args):
    """Make this independent realm a quarter of ``capital`` (its canister id), known here as ``name`` (default
    "capital"). The capital registers this realm separately. Returns ``capital``."""
    r, cid = realm(), args["capital"]
    if r.is_quarter or cid == chain.self_id():
        raise Conflict("this realm already belongs to a federation")
    r.is_quarter, r.is_capital, r.federation_realm_id = True, False, cid
    # The capital becomes a known quarter so its calls are accepted (I-FED-4).
    Quarter(id=cid, name=args.get("name", "capital"), index=0, status="active")
    return {"capital": cid}


@op(None, read=True, public=True)
def quarter_directory(c, args):
    """The capital and every quarter with its status, population and codex, and whether that codex drifts from the
    capital's. Returns ``capital`` and ``quarters``."""
    return {"capital": capital(), "quarters": directory(zones=True)}


def joiner_zone(c, args):
    """The district the joiner states (``home_zone``), else the home zone of a member already known here, else None. It
    is only used to place them: no reply carries it."""
    user = User[c.user]
    return args.get("home_zone") or (user.home_zone if user else "") or None


@op(None, read=True, public=True)
def join_targets(c, args):
    """Least-populated active quarter; the capital only takes joins until sub-quarters exist. A layered codex's
    ``compute_realm_join`` may choose among the quarters below capacity, by the optional ``home_zone`` of the joiner
    and the ``zones`` each quarter holds members of; capacity decides before it does, so a full quarter is never offered
    while another has room."""
    active = [q for q in directory() if q["status"] == "active"]
    pool = [q for q in active if not q["capital"]] or active
    best = min(pool, key=lambda q: (q["population"], -q["index"]))["id"] if pool else ""
    chosen = run(runtime.hook("assign_quarter", {"user": c.user, "quarters": pool}))
    if chosen and chosen.get("ok") and (chosen.get("result") or {}).get("quarter") in [q["id"] for q in pool]:
        best = chosen["result"]["quarter"]
    limit = capacity()
    room = [{**q, "zones": zones_of(q["id"])} for q in pool if q["population"] < limit]
    if room:
        placed = run(runtime.compute("realm_join", {"user": c.user, "home_zone": joiner_zone(c, args), "quarters": room}))
        if placed in [q["id"] for q in room]:
            best = placed
    return {"default": best, "targets": [q["id"] for q in pool], "mode": realm().quarter_join_mode}


@op(None, read=True, public=True)
def scale_status(c, args):
    """Quarter populations against the capacity per quarter, and whether a new quarter is wanted or being set up."""
    return scale()


def moving(user, args):
    """What a move changes for ``user``: they become a guest here and lose their profiles in this quarter."""
    dest = Quarter[str(args.get("quarter"))]
    return ([{"label": "Moves you to", "value": quoted(dest.name)}] if dest and dest.name else []) + [
        {"label": "What changes", "value": "You become a guest here and your profiles in this quarter are deleted"}]


@op("self.change_quarter", returns="forward", summary=moving, title="Move to another quarter")
def self_change_quarter(c, args):
    """Move your membership to ``quarter``, another active quarter of the federation; here you become a guest.
    Returns ``forward`` and ``home_quarter``."""
    dest, here = args["quarter"], chain.self_id()
    member, user = Member[c.user], User[c.user]
    if member is None or member.status != "active" or user is None:
        raise Forbidden("only an active member of this quarter can move")
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
    assets.leave_home(user)
    user.home_quarter, user.profiles = dest, []
    guest = GuestUser[c.user] or GuestUser(id=c.user)
    guest.home_quarter, guest.permissions = dest, GUEST
    return {"forward": f.id, "home_quarter": dest}


@op("quarter.sync")
def quarter_sync(c, args):
    """On a quarter: report to the capital and take its directory and the zones the realm defines. On the capital: answer
    that call, which carries ``population``, ``codex_id``, ``codex_version``, optionally the ``zones`` its members live
    in (zone id to how many) and a federal vote ``leg``."""
    if federated():
        reply = yield from ask(capital(), "quarter_sync", {**report(), "zones": zones_held()})
        if not reply.get("ok"):
            raise Fail(f"the capital refused to sync: {reply.get('error')}")
        apply_directory(reply)
        return {"quarters": len(reply["directory"]), "drifting": drifting(codex()["version"])}
    q = record(c, args)
    return {"directory": directory(), "codex": codex(), "index": q.index, "zones": zone_definitions()}


def reporting_quarter(args, proposal=""):
    """The fact of a quarter_report call that is not one of its arguments (GGG's derived facts): the compliance of the
    quarter reporting."""
    return {"follower_compliance": compliance.of_reporting_quarter()}


@op("quarter.sync", derived=reporting_quarter)
def quarter_report(c, args):
    """On the capital, record a quarter's ``population``, ``codex_id``, ``codex_version``, ``zones`` (zone id to how many
    of its members live there) and optional federal vote ``leg``. Returns the ``quarter`` and ``drifting``: whether its
    codex differs from the capital's."""
    q = record(c, args)
    return {"quarter": q.id, "drifting": drifting(q.codex_version)}


def on_arrive(c, body):
    who = body["user"]
    if digest({"subject": f"User:{who}", "from": c.by, "to": chain.self_id(), "moved_at": body["moved_at"]}) \
            != body["signature"]:
        raise Forbidden("the forward's signature does not match")
    m = Member[who]
    if m and m.status == "banned":
        raise Forbidden(f"{who} is banned here")
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
