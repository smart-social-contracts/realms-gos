import hashlib
import json
import math

from core import chain
from core import compliance
from core import sandbox
from api import assets, proposals
from core.db import Conflict, Fail, Forbidden, Unavailable, each, find, next_id, view
from ggg import Forward, GuestUser, LayeredCodex, Member, Quarter, Realm, User, UserProfile
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
FIELDS = ("id", "name", "index", "status", "population", "codex_id", "codex_version", "compliance", "last_sync")
# Seconds between looks at the conductor while Casals builds a quarter quarter_add asked for.
ADOPT_EVERY = 60
# What adopt() does for a quarter Casals has built, in order.
ADOPTION = ("join", "configure", "register", "sync", "launch")
# A quarter's own settings that the realm may also set for it by a realm-wide vote (quarter_configure). The capital
# sends them to the quarter in a federation_message of this topic, which only the capital's quarter_configure sends. A
# new quarter starts from its capital's where it still has the default, which means not yet chosen.
LOCAL = ("timezone", "welcome_message")
LOCAL_SETTINGS = "quarter.local_settings"
DEFAULTS = {"timezone": "UTC", "welcome_message": ""}
# Seconds between a quarter's catch-up syncs with its capital, which sets them (quarter_configure ``sync_every``). The
# capital pushes its synced settings when they change and a quarter syncs when it starts after an upgrade, so this only
# catches up what a quarter missed while it was down; each sync carries the whole directory (up to MAX_QUARTERS rows).
SYNC_EVERY, SYNC_MIN, SYNC_MAX = 3600, 3600, 7 * 24 * 3600
# A quarter that starts after an upgrade syncs within this many seconds, at a moment its canister id sets: the quarters
# of a federation upgraded together do not all call their capital in the same round.
RESUME_SPREAD = 120
# Whether this heap has armed the catch-up timer: one chain of them, armed again after an upgrade (resume).
_catch_up = {"armed": False}
# The settings a capital keeps in sync for its quarters (the last section of this module), sent in messages of
# SETTINGS_TOPIC, which only the capital sends.
SYNCED = ("name", "manifesto", "terms", "logo_url", "background_image_url", "primary_color", "accounting_currency",
          "accounting_currency_decimals", "languages", "status")
SYNCED_IMAGES = ("logo_url", "background_image_url")
SETTINGS_TOPIC = "realm.settings"
# The operations that set a synced field, as the action of a federal vote: only the capital replays such an action.
SETTING_OPS = ("realm_configure", "realm_configure_branding", "realm_configure_tokens", "setup_step", "setup_launch")
# manifest_data key on the capital: the sha256 of each image as it is now, so that a sync does not hash ~1 MB each time.
DIGESTS = "image_digests"


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
    """The codex this realm runs, as ``id`` and ``version``. A layered codex has no version: its checksum names it and
    stands for both, so a quarter on another layered codex drifts."""
    x = runtime.active()
    if x is None:
        return {"id": "", "version": ""}
    if isinstance(x, LayeredCodex):
        return {"id": x.id, "version": x.id}
    return {"id": (x.manifest or {}).get("id", x.id.split("@")[0]), "version": x.version}


def capital_codex():
    return (realm().sync_state or {}).get("codex", {}) if federated() else codex()


def drifting(version):
    return version != capital_codex().get("version", "")


def report():
    mine = codex()
    return {"population": population(), "codex_id": mine["id"], "codex_version": mine["version"]}


def own_compliance():
    """This realm's own flag. The capital is compliant by definition; a quarter does not judge itself: its capital does,
    from what it reports (``propagation.judge``), and the quarter shows the flag it was given at its last sync."""
    if not federated():
        return compliance.valid("compliant")
    return (realm().sync_state or {}).get("compliance") or compliance.valid("unknown")


def me():
    """This realm's row of the directory. A quarter's name there is its label, the one its capital registered it under,
    which it learns at each sync: ``Realm.name`` is the realm's."""
    r, state = realm(), realm().sync_state or {}
    return {"id": chain.self_id(), "name": state.get("label", r.name) if federated() else r.name,
            "index": state.get("index", 0) if federated() else 0,
            "status": "setup" if r.status == "setup" else "active", "last_sync": state.get("at", 0), **report(),
            "compliance": own_compliance()}


def known():
    for status in STATUSES:
        yield from each(Quarter, "status", status)


def zones_held():
    """The zones of the realm that members live in here, each with how many: what a quarter reports to its capital,
    which places new members by it and tells no one else, since a small count singles out a person."""
    return {z.id: z.population for z in assets.defined_zones() if z.population}


def zone_definitions():
    """The zones of the realm as ``zone_define`` takes them: what the capital sends its quarters, without a population."""
    return [{"zone": z.id, "name": z.name, "kind": z.kind} for z in assets.defined_zones()]


def zones_of(cid):
    """The zones ``cid`` holds members of: this realm's own, or what the quarter last reported; None where this realm
    is a quarter and knows only itself. Only the capital's placement reads them. The decision is one of privacy: a small
    count singles out a person, so no reply carries one."""
    if cid == chain.self_id():
        return zones_held()
    return None if federated() else Quarter[cid].zones


def directory():
    rows = [me()] + [view(q, *FIELDS) for q in known()]
    for row in rows:
        row["capital"], row["drifting"] = row["id"] == capital(), drifting(row["codex_version"])
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
    """Run operation ``name`` on another realm canister. A call to a canister of this federation, the capital or a
    quarter, carries the checksum of the codex this realm runs, so that the receiver can tell which legislation the
    sender ran while a switch is under way (§9.5)."""
    if Quarter[cid] is not None:
        payload = {**payload, "codex": codex()["id"]}
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
    if "zones" in args:  # a report keeps the realm's zones only: it is no way to store other keys
        defined = {z.id for z in assets.defined_zones()}
        q.zones = {zone: int(n) for zone, n in args["zones"].items() if zone in defined}
    q.codex_id, q.codex_version = args.get("codex_id", q.codex_id), args.get("codex_version", q.codex_version)
    from api import propagation  # it imports the install task, which imports this module

    q.compliance = propagation.judge(q, args.get("codex_id"), propagation.required())
    propagation.correct(q)
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
    seen, index, flag, label = set(), 0, None, realm().name
    for row in reply["directory"]:
        if row["id"] == chain.self_id():
            index, flag, label = row["index"], row.get("compliance"), row["name"]
            continue
        q = Quarter[row["id"]] or Quarter(id=row["id"])
        for k in FIELDS[1:]:
            setattr(q, k, row.get(k, getattr(q, k)))  # a capital on the previous release sends no compliance
        seen.add(q.id)
    for q in [q for q in known() if q.id not in seen]:
        q.delete()
    apply_zones(reply.get("zones") or [])
    r = realm()
    r.sync_state = {**(r.sync_state or {}), "at": chain.now(), "codex": reply["codex"], "index": index,
                    "compliance": flag, "label": label, "sync_every": reply.get("sync_every", SYNC_EVERY),
                    "official_timezone": reply.get("official_timezone", "")}  # a capital on the previous release sends none


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
    """Once Casals has built the pending quarter, make it a quarter of this capital (the capital is one of its
    controllers): it joins, takes the capital's ``open_registration`` so that the members it is offered to can join it,
    is registered, syncs, which brings it the realm's name and stage, and launches on that stage. Until Casals has built
    it, and after a step that failed, look again in ADOPT_EVERY seconds; a step already done is not done again."""
    p = pending()
    if not p:
        return
    try:
        cid = p.get("canister") or (yield from built(p["member"]))
        if not cid:
            chain.set_timer(ADOPT_EVERY, adopt)
            return
        for step in ADOPTION:
            if step not in p.get("done", []):
                yield from adoption_step(step, cid, p["name"])
                p = {**p, "canister": cid, "done": [*p.get("done", []), step]}
                set_pending(p)
        set_pending(None)
    except Exception as e:
        set_pending({**p, "error": str(e)[:300]})
        chain.set_timer(ADOPT_EVERY, adopt)


def built(member):
    """The canister id of ``member`` once Casals reports it ready, else None. A generator."""
    cas = casals()
    arg = json.dumps({"stand": cas["casals_stand"]})
    view_ = json.loads((yield from chain.call_text(cas["conductor_canister_id"], "orchestra_get", arg)) or "{}")
    row = next((x for x in view_.get("canisters", []) if x.get("name") == member), None)
    return row["canister_id"] if row and row.get("status") == "ready" and row.get("canister_id") else None


def adoption_step(step, cid, name):
    """One step of ``adopt`` for the quarter ``cid``, registered here as ``name``: it fails unless the quarter says it
    is done. A generator."""
    if step == "register":
        if not Quarter[cid]:
            register(cid, name)
        return
    registration = {"step": "realm", "open_registration": bool(realm().open_registration)}
    operation, args = {"join": ("quarter_join_federation", {"capital": chain.self_id()}),
                       "configure": ("setup_step", registration),
                       "sync": ("quarter_sync", {}),
                       "launch": ("setup_launch", {})}[step]
    reply = json.loads((yield from chain.call_op(cid, operation, args)) or "{}")
    if not reply.get("ok") and not (step == "join" and "already belongs" in str(reply.get("error"))):
        raise Fail(f"{cid} did not {step}: {reply.get('error')}")


def resume():
    """Timers do not survive an upgrade: keep looking for a quarter Casals is still building, and on a quarter, sync
    with the capital now and catch up from then on."""
    if pending():
        chain.set_timer(ADOPT_EVERY, adopt)
    if federated():
        catch_up_in(sum(chain.self_id().encode()) % RESUME_SPREAD)


def catch_up_in(seconds):
    """Arm this quarter's next catch-up sync in ``seconds``, unless it is armed already."""
    if not _catch_up["armed"]:
        _catch_up["armed"] = True
        chain.set_timer(seconds, catch_up)


def catch_up():
    """A quarter's catch-up sync with its capital, every ``sync_every`` seconds the capital sets, for as long as it is a
    quarter. A sync that fails is tried again at the next; its error is kept in ``sync_state`` until one succeeds."""
    _catch_up["armed"] = False
    if not federated():
        return
    catch_up_in((realm().sync_state or {}).get("sync_every", SYNC_EVERY))
    try:
        yield from sync()
        error = ""
    except Exception as e:  # noqa: BLE001  a capital that is down or upgrading: the next catch-up tries again
        error = str(e)[:300]
    r = realm()
    r.sync_state = {**(r.sync_state or {}), "catch_up_error": error}  # so that a quarter stuck behind shows why


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


def local_settings(args):
    """The quarter's own settings a ``quarter_configure`` call sets (``LOCAL``)."""
    return {k: args[k] for k in LOCAL if k in args}


def configuring(args):
    """The check of ``quarter_configure``: its federal ``rule``, its ``sync_every``, and the settings it sets for one
    quarter, which need the ``quarter`` and pass ``realm_configure``'s own test (a zone this release has data for)."""
    from api import setup  # it imports this module

    federal_rule(args)
    if "sync_every" in args and not SYNC_MIN <= int(args["sync_every"]) <= SYNC_MAX:
        raise Fail(f"sync_every is in seconds, from {SYNC_MIN} (an hour) to {SYNC_MAX} (a week)")
    local = local_settings(args)
    if local and not args.get("quarter"):
        raise Fail(f"{', '.join(local)} is set for one quarter: name it with quarter")
    setup.valid_values(local)


def set_for_quarter(q, settings):
    """Send ``settings``, the quarter's own, to quarter ``q`` to apply. A quarter that refuses or cannot be reached
    fails the call, and so does one that does not say it applied them: a quarter on an older release answers ``ok`` to a
    topic it does not know, and applies nothing. A generator."""
    try:
        reply = yield from ask(q.id, "federation_message", {"topic": LOCAL_SETTINGS, "body": settings})
    except Exception as e:  # noqa: BLE001  chain.call raises for a quarter that is down: the call fails, it does not trap
        raise Unavailable(f"{q.name} ({q.id}) cannot be reached: {e}")
    if not reply.get("ok"):
        raise Fail(f"{q.name} ({q.id}) refused {', '.join(settings)}: {reply.get('error')}")
    if not set(settings) <= set(reply.get("configured") or ()):
        raise Conflict(f"{q.name} ({q.id}) did not apply {', '.join(settings)}: upgrade it to this release first")


@op("quarter.configure", governed=proposals.realm_wide, check=configuring)
def quarter_configure(c, args):
    """Set ``quarter``'s ``name`` (its label) or ``status`` (setup→active, active⇄suspended, active→splitting or
    merging→active) and its own ``timezone`` and ``welcome_message``, which the quarter applies, and realm-wide
    ``capacity``, ``auto_scale_enabled``, ``quarter_join_mode``, ``federation_codex``, ``sync_every`` (the seconds
    between a quarter's catch-up syncs, an hour to a week) and the federal vote ``rule``: kind majority_of_quarters or
    majority_of_members, absent wait, no or ignore, threshold below 100 % (100000000), in hundred-millionths. A quarter
    that refuses its own settings, does not apply them (an older release) or cannot be reached fails the call, and
    nothing changes."""
    q = find(Quarter, args["quarter"]) if args.get("quarter") else None
    status, rule = args.get("status"), args.get("rule")
    if status and (q is None or status not in MOVES.get(q.status, ())):
        raise Conflict(f"a {q.status if q else 'missing'} quarter cannot become {status}")
    local = local_settings(args)
    if local:  # first: what this message writes before an await is kept even if the quarter then refuses
        yield from set_for_quarter(q, local)
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
    if "sync_every" in args:
        data["federation"] = {**data.get("federation", {}), "sync_every": int(args["sync_every"])}
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
    "capital"). The capital registers this realm separately. A realm with a link that has not ended, to a sync source
    or from a follower, cannot join: a quarter makes no call to another realm. Nor can a realm whose accounting
    currency differs from the capital's. The join reads the capital's ``realm_info``: a realm that has not chosen its
    time zone, welcome message or currency starts on the capital's. Returns ``capital``."""
    r, cid = realm(), args["capital"]
    refuse_join(cid)
    profile = yield from capital_profile(cid)
    refuse_join(cid)  # again: the realm may have changed while the capital answered
    refuse_currency(profile)
    take_defaults(profile)
    r.is_quarter, r.is_capital, r.federation_realm_id = True, False, cid
    # The capital becomes a known quarter so its calls are accepted (I-FED-4).
    Quarter(id=cid, name=args.get("name", "capital"), index=0, status="active")
    catch_up_in(SYNC_EVERY)
    return {"capital": cid}


def refuse_join(cid):
    """Refuse to join ``cid`` as a quarter: this realm belongs to a federation already, or has a link that has not
    ended, to a sync source or from a follower; a quarter makes no call to another realm, so it could not end them
    (I-NEST-3)."""
    from api import links  # it imports this module

    if realm().is_quarter or cid == chain.self_id():
        raise Conflict("this realm already belongs to a federation")
    open_links = links.open_links()
    if open_links:
        raise Conflict(f"end this realm's links before it becomes a quarter: {', '.join(open_links)}")


def capital_profile(cid):
    """The public profile (``realm_info``) of the capital ``cid`` this realm joins. A generator."""
    try:
        reply = yield from ask(cid, "realm_info", {})
    except Exception as e:  # noqa: BLE001  chain.call raises for a canister that cannot be reached
        raise Unavailable(f"the capital {cid} cannot be reached: {e}")
    if not reply.get("ok"):
        raise Fail(f"{cid} is not a realm: {reply.get('error')}")
    return reply["realm"]


def refuse_currency(profile):
    """A realm whose accounting currency differs from the capital's, in its code or its decimals, cannot join: amounts
    are integers in units of 10**decimals, so taking the capital's would rescale its books. A blank code has not been
    chosen."""
    r = realm()
    mine = (r.accounting_currency, r.accounting_currency_decimals)
    theirs = (profile.get("accounting_currency"), profile.get("accounting_currency_decimals"))
    if r.accounting_currency and mine != theirs:
        raise Conflict(f"this realm keeps its accounts in {mine[0]} with {mine[1]} decimals and the capital in "
                       f"{theirs[0]} with {theirs[1]}: change this realm's accounting currency first, through its own "
                       f"governance")


def take_defaults(profile):
    """A new quarter starts from its capital's settings where it has not chosen its own: its own settings (``LOCAL``)
    still at their default (``DEFAULTS``: ``UTC`` means no zone chosen, GGG I-ADM-4), and a blank accounting currency,
    which takes the capital's code and decimals. Read once: after that its own settings are its own, and the capital
    keeps the currency in sync."""
    from api import setup  # it imports this module

    r = realm()
    unchosen = [k for k in LOCAL if getattr(r, k) == DEFAULTS[k]]
    taken = {k: profile[k] for k in unchosen if profile.get(k, DEFAULTS[k]) != DEFAULTS[k]}
    if not r.accounting_currency and profile.get("accounting_currency"):
        taken.update({k: profile[k] for k in ("accounting_currency", "accounting_currency_decimals")})
    if taken:
        setup.configure(tuple(taken), taken)


def on_local_settings(c, body):
    """From the capital: this quarter's own settings, as a realm-wide vote set them (``quarter_configure``)."""
    from api import setup  # it imports this module

    from_capital(c)
    return setup.configure(LOCAL, body)


@op(None, read=True, public=True)
def quarter_directory(c, args):
    """The capital and every quarter with its status, population and codex, whether that codex drifts from the
    capital's, and the ``compliance`` the capital gives it. Returns ``capital`` and ``quarters``."""
    return {"capital": capital(), "quarters": directory()}


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


def moving(user, args, grant):
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
                signature=signature, codex=codex()["id"], compliance=q.compliance)
    member.delete()
    assets.leave_home(user)
    user.home_quarter, user.profiles = dest, []
    guest = GuestUser[c.user] or GuestUser(id=c.user)
    guest.home_quarter, guest.permissions = dest, GUEST
    return {"forward": f.id, "home_quarter": dest}


@op("quarter.sync")
def quarter_sync(c, args):
    """On a quarter: report to the capital and take its directory, the zones the realm defines and the settings it keeps
    in sync; a quarter may run it at any time, besides its catch-up timer. On the capital: answer that call, which
    carries ``population``, ``codex_id``, ``codex_version``, optionally the ``zones`` its members live in (zone id to
    how many) and a federal vote ``leg``; the answer carries the synced ``settings``, ``sync_every`` and the capital's
    ``official_timezone``."""
    if federated():
        reply = yield from sync()
        return {"quarters": len(reply["directory"]), "drifting": drifting(codex()["version"])}
    q = record(c, args)
    from api import propagation  # it imports the install task, which imports this module

    return {"directory": directory(), "codex": propagation.in_force(), "index": q.index, "zones": zone_definitions(),
            "settings": synced_values(), "sync_every": sync_every(), "official_timezone": realm().timezone}


def sync():
    """On a quarter: report to the capital, and take back its directory, the zones the realm defines, the settings it
    keeps in sync and how often to catch up. A generator; returns the capital's answer."""
    reply = yield from ask(capital(), "quarter_sync", {**report(), "zones": zones_held()})
    if not reply.get("ok"):
        raise Fail(f"the capital refused to sync: {reply.get('error')}")
    apply_directory(reply)
    if "settings" in reply:  # a capital on the previous release sends none
        yield from apply_settings(reply["settings"])
    return reply


def official_timezone():
    """The zone of the realm's official time (docs/codices/layered-codex.md §4.5): the capital's. A quarter keeps the one
    its capital named at its last sync, its own until the first, and so learns a change of the capital's at its next sync.
    The zone is not a synced setting: the quarter's own stays its own."""
    own = realm().timezone
    if not federated():
        return own
    return (realm().sync_state or {}).get("official_timezone") or own


def sync_every():
    """On the capital, the seconds between its quarters' catch-up syncs (quarter_configure ``sync_every``)."""
    return int((realm().manifest_data or {}).get("federation", {}).get("sync_every", SYNC_EVERY))


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


# ---- The settings the realm keeps in sync for its quarters (realms-gos#528; "What the realm sets for its quarters" in
# docs/architecture/quarters-and-federation.md): its identity and its shared foundations, one face for the whole realm.
#
# The capital owns them. It pushes them to every active quarter whenever an operation changes one (push_settings), and
# its answer to quarter_sync carries them for a quarter that missed a push. A quarter refuses to set one itself
# (refuse_synced) and writes what its capital sends directly (apply_settings), never through realm_configure. The voting
# rules are not among them: they belong in the codex, which reaches every quarter at one switch time.
#
# The branding images are data URLs of up to setup.IMAGE_MAX characters each, too big for every push and sync and for
# the record of a message: those carry each image's sha256 instead, and a quarter whose image differs reads the images
# once from the capital's realm_info.


def synced_values():
    """Every synced field of this realm, each image as its digest (``images``): what the capital sends its quarters."""
    r = realm()
    texts = {k: getattr(r, k) for k in SYNCED if k not in SYNCED_IMAGES}
    return {**texts, "images": {k: image_digest(k) for k in SYNCED_IMAGES}}


def image_digest(image):
    """The sha256 of the capital's ``image``, "" for none; computed once after each change (``forget_digests``)."""
    r = realm()
    known = (r.manifest_data or {}).get(DIGESTS, {})
    if image not in known:
        value = getattr(r, image)
        known = {**known, image: sandbox.sha256(value) if value else ""}
        r.manifest_data = {**(r.manifest_data or {}), DIGESTS: known}
    return known[image]


def forget_digests(values):
    """The images in ``values`` are being set: their digests are taken again when next needed."""
    r = realm()
    known = (r.manifest_data or {}).get(DIGESTS, {})
    if any(image in known for image in values):
        r.manifest_data = {**r.manifest_data, DIGESTS: {k: v for k, v in known.items() if k not in values}}


def push_settings():
    """On a capital, once an operation has changed a synced field: send them all to every active quarter, each in a
    message of its own, so that a quarter that cannot be reached holds up no other and the record of its message says
    so (propagation.tell_all). The capital's values stand whatever a quarter answers. The message always carries the
    whole set, so a repeated or late one does no harm."""
    from api import propagation  # it imports the install task, which imports this module

    propagation.tell_all(SETTINGS_TOPIC, synced_values(), recorded=settings_reply)


def settings_reply(reply):
    """A quarter's reply to a settings push, as the capital records it: a quarter on an older release answers ``ok`` to
    a topic it does not know and applies nothing, so a reply without ``configured`` is a Conflict."""
    if reply.get("ok") and "configured" not in reply:
        return {"ok": False, "kind": Conflict.kind, "error": "it applied nothing: upgrade it to this release first"}
    return reply


def refuse_synced(values):
    """On a quarter, refuse a call that sets a synced field: the capital owns it."""
    synced = sorted(set(values) & set(SYNCED))
    if synced and federated():
        raise Forbidden(f"{', '.join(synced)} is kept in sync from the capital {capital()}: the realm changes it "
                        f"there, by a realm-wide vote")


def changes_synced(action):
    """Whether a federal vote's ``action`` changes a synced field: a quarter skips its replay, since the capital replays
    it and pushes the change."""
    op, args = action.get("op"), action.get("args") or {}
    return op in SETTING_OPS and (op == "setup_launch" or bool(set(args) & set(SYNCED)))


def on_settings(c, body):
    """From the capital: the synced settings, pushed when one changed."""
    from_capital(c)
    return (yield from apply_settings(body))


def apply_settings(values):
    """On a quarter, the capital's synced settings (a push, or its answer to ``quarter_sync``): written directly. A
    quarter still in ``setup`` keeps its stage and remembers the capital's for its own ``setup_launch``; a launched one
    takes the capital's and runs its codex's ``on_stage_change`` when it changes. The images come last, so that one
    that cannot be read now holds up none of the rest. Returns what it ``configured``, ``images`` among them once they
    are the capital's, else the ``images_error``. A generator."""
    r = realm()
    configured = [k for k in SYNCED if k in values and k != "status"]
    for k in configured:
        setattr(r, k, values[k])
    stage = values.get("status")
    if stage:
        configured.append("status")
        r.sync_state = {**(r.sync_state or {}), "stage": stage}
        if r.status not in ("setup", stage):
            r.status = stage
            yield from runtime.hook("on_stage_change", {"stage": stage})
    failed = {}
    if "images" in values:
        error = yield from take_images(values["images"])
        configured += [] if error else ["images"]
        failed = {"images_error": error} if error else {}
    return {"configured": sorted(configured), **failed}


def take_images(digests):
    """Take each of the capital's images whose digest differs from the one this quarter last took, one read a call
    (``realm_image``). An image that cannot be read keeps its old digest, so the next sync tries again. Returns the
    first error, "" when every image is the capital's. A generator."""
    r = realm()
    for image in SYNCED_IMAGES:
        digest, state = digests.get(image, ""), r.sync_state or {}
        if (state.get("images") or {}).get(image) == digest:
            continue
        try:
            value = (yield from read_image(image)) if digest else ""
        except Fail as e:
            return f"{image}: {e}"
        setattr(r, image, value)
        forget_digests((image,))  # a quarter that secedes and becomes a capital takes them again
        state = r.sync_state or {}
        r.sync_state = {**state, "images": {**(state.get("images") or {}), image: digest}}
    return ""


def read_image(image):
    """The capital's ``image``, read with its ``realm_image``. A generator."""
    try:
        reply = yield from ask(capital(), "realm_image", {"image": image})
    except Exception as e:  # noqa: BLE001  chain.call raises for a capital that cannot be reached
        raise Unavailable(f"the capital {capital()} cannot be reached: {e}")
    if not reply.get("ok"):
        raise Fail(f"the capital did not give its {image}: {reply.get('error')}")
    return reply["value"]


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
