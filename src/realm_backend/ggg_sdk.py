"""In-sandbox SDK for codices (issue #265).

The SDK runs *inside* the Basilisk subinterpreter, spawned via the real
primitive ``_basilisk_sandbox.spawn_subinterpreter(source, hash)`` /
``call_in_subinterpreter(handle, fn, kwargs)``. Only plain data crosses the
boundary. The SDK follows a *gather → compute → effects* model:

  * **Reads** (``realm.config()``, ``realm.now()``, ``realm.currency()``,
    ``realm.info()``, ``realm.users.get(id)``) are served from the ``context``
    the host injected when it invoked the hook, with no round-trip. When a read
    is not in the context — a user other than the triggering one, or a key the
    hook's context spec deliberately omits — the SDK falls back to a live
    ``rpc`` call, which the host authorizes against the codex's declared read
    capabilities.

  * **Writes** (``realm.invoices.create()``, ``realm.notifications.create()``,
    ``realm.members.activate()``) do not execute inside the sandbox. They are
    *recorded* as intended effects and returned to the host, which authorizes
    each against the codex's declared ``capabilities`` and applies it through the
    public ``ggg`` API. A ``create()`` returns a light ref whose ``id`` is a
    ``$eff:<n>:id`` token; the host substitutes the real id when it applies later
    effects (and in the hook's return value), so an author can still write
    ``metadata="invoice_id:" + str(invoice["id"])`` naturally.

Example::

    from ggg_sdk import hook, iso_days_from, realm

    @hook
    def on_user_register(args):
        user = realm.users.get(args["user_id"])
        cfg = realm.config()
        now = realm.now()["epoch"]
        inv = realm.invoices.create(
            amount=cfg["fees"]["registration"], currency=realm.currency(),
            due_date=iso_days_from(now, 30), status="Pending",
            user_id=user["id"], metadata="registration invoice",
        )
        return {"success": True, "invoice_id": inv["id"]}

Trust model: the SDK is **convenience only**. All security (capability
authorization, strict plain-data serialization) is enforced host-side in
``core.codex_bridge.apply_effects``. A hostile codex may rewrite this SDK; it
still cannot make the host apply an effect for a capability it did not declare,
nor smuggle a live object across the boundary.

Why the code lives in a string constant: in the canister the realm_backend
modules are frozen to *bytecode* — there is no ``__file__``, no
``loader.get_source``, no ``inspect.getsource`` — yet the host must inject the
SDK's *source text* into each fresh subinterpreter
(``runtime_sandbox._build_codex_sandbox_source``). String constants survive
freezing, so ``GGG_SDK_SOURCE`` below is the single source of truth: the host
embeds it into the sandbox source, and this module ``exec``s it at import time
so host-side code (unit tests) can still ``import ggg_sdk`` normally. The
payload imports only the standard library so it executes unchanged inside the
subinterpreter.
"""

GGG_SDK_SOURCE = r'''
# The sandbox stdlib is minimal: only builtin (C) modules exist, so pure-Python
# stdlib like ``json`` is unavailable there.
#
# That is a problem well beyond this module. Every extension entry point has
# the signature ``f(args: str) -> str`` where both sides are JSON, so without
# ``json`` each ported extension would have to hand-roll serialization. So the
# SDK ships a pure-Python implementation and registers it as ``sys.modules
# ["json"]`` when the real one is missing, letting ``import json`` keep working
# unchanged inside the sandbox.
try:
    import json
except ImportError:
    json = None

if json is None:
    import sys as _sys

    class _JSONShim:
        """Minimal ``dumps``/``loads`` over plain JSON data.

        Deliberately small: it handles exactly what crosses the bridge
        (None/bool/int/float/str/list/dict) and raises on anything else,
        matching the host-side serializer rather than quietly coercing.
        """

        _ESCAPES = {
            '"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r",
            "\t": "\\t", "\b": "\\b", "\f": "\\f",
        }

        def _string(self, value):
            out = ['"']
            for ch in value:
                esc = self._ESCAPES.get(ch)
                if esc is not None:
                    out.append(esc)
                elif ch < " ":
                    out.append("\\u%04x" % ord(ch))
                else:
                    out.append(ch)
            out.append('"')
            return "".join(out)

        def dumps(self, value, **kwargs):
            if value is None:
                return "null"
            if value is True:
                return "true"
            if value is False:
                return "false"
            if isinstance(value, str):
                return self._string(value)
            if isinstance(value, int):
                return str(value)
            if isinstance(value, float):
                # JSON has no Infinity/NaN; surface that rather than emit it.
                if value != value or value in (float("inf"), float("-inf")):
                    raise ValueError("cannot serialize non-finite float to JSON")
                return repr(value)
            if isinstance(value, (list, tuple)):
                return "[" + ",".join(self.dumps(v) for v in value) + "]"
            if isinstance(value, dict):
                parts = []
                for key, item in value.items():
                    if not isinstance(key, str):
                        key = str(key)
                    parts.append(self._string(key) + ":" + self.dumps(item))
                return "{" + ",".join(parts) + "}"
            raise TypeError(
                "Object of type %s is not JSON serializable" % type(value).__name__
            )

        def loads(self, text, **kwargs):
            if isinstance(text, bytes):
                text = text.decode("utf-8")
            value, index = self._parse(text, self._skip(text, 0))
            if self._skip(text, index) != len(text):
                raise ValueError("Extra data after JSON value")
            return value

        def _skip(self, text, i):
            while i < len(text) and text[i] in " \t\n\r":
                i += 1
            return i

        def _parse(self, text, i):
            if i >= len(text):
                raise ValueError("Unexpected end of JSON input")
            ch = text[i]
            if ch == "{":
                return self._object(text, i)
            if ch == "[":
                return self._array(text, i)
            if ch == '"':
                return self._str(text, i)
            if text.startswith("true", i):
                return True, i + 4
            if text.startswith("false", i):
                return False, i + 5
            if text.startswith("null", i):
                return None, i + 4
            return self._number(text, i)

        def _object(self, text, i):
            out = {}
            i = self._skip(text, i + 1)
            if i < len(text) and text[i] == "}":
                return out, i + 1
            while True:
                i = self._skip(text, i)
                key, i = self._str(text, i)
                i = self._skip(text, i)
                if i >= len(text) or text[i] != ":":
                    raise ValueError("Expecting ':' in JSON object")
                value, i = self._parse(text, self._skip(text, i + 1))
                out[key] = value
                i = self._skip(text, i)
                if i < len(text) and text[i] == ",":
                    i += 1
                    continue
                if i < len(text) and text[i] == "}":
                    return out, i + 1
                raise ValueError("Expecting ',' or '}' in JSON object")

        def _array(self, text, i):
            out = []
            i = self._skip(text, i + 1)
            if i < len(text) and text[i] == "]":
                return out, i + 1
            while True:
                value, i = self._parse(text, self._skip(text, i))
                out.append(value)
                i = self._skip(text, i)
                if i < len(text) and text[i] == ",":
                    i += 1
                    continue
                if i < len(text) and text[i] == "]":
                    return out, i + 1
                raise ValueError("Expecting ',' or ']' in JSON array")

        def _str(self, text, i):
            if i >= len(text) or text[i] != '"':
                raise ValueError("Expecting a JSON string")
            i += 1
            out = []
            while i < len(text):
                ch = text[i]
                if ch == '"':
                    return "".join(out), i + 1
                if ch == "\\":
                    i += 1
                    if i >= len(text):
                        break
                    esc = text[i]
                    simple = {
                        '"': '"', "\\": "\\", "/": "/", "n": "\n",
                        "r": "\r", "t": "\t", "b": "\b", "f": "\f",
                    }
                    if esc in simple:
                        out.append(simple[esc])
                    elif esc == "u":
                        out.append(chr(int(text[i + 1:i + 5], 16)))
                        i += 4
                    else:
                        raise ValueError("Invalid escape in JSON string")
                else:
                    out.append(ch)
                i += 1
            raise ValueError("Unterminated JSON string")

        def _number(self, text, i):
            start = i
            if i < len(text) and text[i] == "-":
                i += 1
            while i < len(text) and text[i].isdigit():
                i += 1
            is_float = False
            if i < len(text) and text[i] == ".":
                is_float = True
                i += 1
                while i < len(text) and text[i].isdigit():
                    i += 1
            if i < len(text) and text[i] in "eE":
                is_float = True
                i += 1
                if i < len(text) and text[i] in "+-":
                    i += 1
                while i < len(text) and text[i].isdigit():
                    i += 1
            raw = text[start:i]
            if not raw or raw == "-":
                raise ValueError("Invalid JSON number")
            return (float(raw) if is_float else int(raw)), i

    json = _JSONShim()
    json.JSONDecodeError = ValueError
    _sys.modules["json"] = json

_REF_TOKEN = "$eff:%d:%s"


def _rpc(action, kwargs):
    """Call a host *read* verb through the sandbox rpc bridge.

    Returns ``(True, value)`` on success and ``(False, None)`` when no rpc
    channel exists — host-side unit tests, or a canister image predating the
    callback — so callers can fall back to the injected context.

    A denial or host-side error is *not* swallowed: if a codex asks for
    something it did not declare in ``capabilities``, the author should see the
    failure rather than silently receive ``None``. ``@hook`` turns it into a
    clean ``{"ok": False}`` envelope.
    """
    try:
        _fn = rpc  # noqa: F821 - builtin injected by the subinterpreter
    except NameError:
        return (False, None)
    if "action" in kwargs:
        # The bridge spends the name ``action`` on the verb itself, so a verb
        # kwarg of the same name would arrive as a duplicate argument. Caught
        # here rather than as a confusing TypeError from the handler.
        raise ValueError("rpc kwargs cannot contain 'action'")
    return (True, _fn(action, **kwargs))


class _State:
    """Per-invocation reads (``context``) and recorded writes (``effects``).

    A fresh subinterpreter runs exactly one hook call, but ``reset`` is called at
    the start of every hook anyway so host-side unit tests (which reuse the
    module) start clean.
    """

    def __init__(self):
        self.context = {}
        self.effects = []

    def reset(self, context):
        self.context = context or {}
        self.effects = []

    def record(self, verb, kwargs):
        index = len(self.effects)
        self.effects.append({"verb": verb, "kwargs": kwargs})
        return {"id": _REF_TOKEN % (index, "id")}


_state = _State()


# ---------------------------------------------------------------------------
# @hook — adapt the author's function to the sandbox hook ABI
# ---------------------------------------------------------------------------


def hook(func):
    """Adapt ``func(args: dict) -> dict|None`` to the sandbox hook ABI.

    The host calls ``name(args=<dict>, context=<dict>)`` and expects a
    plain-data envelope back::

        {"ok": bool, "error": str?, "effects": [...], "result": <func return>}

    ``args`` may also arrive as a JSON string (host-side tests / legacy
    callers); it is parsed when ``json`` is importable. Reads come from
    ``context``; writes accumulate in ``_state.effects``. Exceptions are
    stringified so a live exception never crosses the boundary.
    """

    def wrapper(args="", context=None):
        _state.reset(context)
        if isinstance(args, dict):
            params = args
        elif isinstance(args, str) and args and json is not None:
            try:
                params = json.loads(args)
            except (ValueError, TypeError):
                params = {}
        else:
            params = {}
        if not isinstance(params, dict):
            params = {}
        try:
            result = func(params)
        except Exception as exc:  # noqa: BLE001 - boundary: stringify only
            return {"ok": False, "error": str(exc), "effects": []}
        return {"ok": True, "effects": list(_state.effects), "result": result}

    wrapper.__name__ = getattr(func, "__name__", "hook")
    wrapper.__doc__ = getattr(func, "__doc__", None)
    wrapper.__wrapped__ = func
    return wrapper


# ---------------------------------------------------------------------------
# Pure helpers (no host round-trip)
# ---------------------------------------------------------------------------


def iso_days_from(epoch_seconds, days):
    """ISO-8601 ``YYYY-MM-DDTHH:MM:SS`` *days* after an epoch-seconds instant.

    Pure integer arithmetic (``datetime`` is unavailable in the sandbox);
    civil-from-days per Howard Hinnant's algorithm.
    """
    total = int(epoch_seconds) + int(days) * 86400
    days_since_epoch, rem = divmod(total, 86400)
    hh, rem = divmod(rem, 3600)
    mm, ss = divmod(rem, 60)
    z = days_since_epoch + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    if m <= 2:
        y += 1
    return "%04d-%02d-%02dT%02d:%02d:%02d" % (y, m, d, hh, mm, ss)


def tax_period(period, epoch_seconds):
    """Period key an instant falls in: ``2026-10``, ``2026-Q4`` or ``2026``."""
    iso = iso_days_from(epoch_seconds, 0)
    year, month = iso[:4], int(iso[5:7])
    if period == "monthly":
        return iso[:7]
    if period == "quarterly":
        return "%s-Q%d" % (year, (month - 1) // 3 + 1)
    if period == "annual":
        return year
    raise ValueError("unknown tax period '%s'" % period)


def tax_breakdown(tax, quantity=1):
    """How much one taxpayer owes under one tax definition, and why.

    *tax* is one entry of an adopted tax policy (see ``core.taxation``).
    *quantity* is what the base counts: ignored for ``flat`` beyond being
    positive, an area for ``land_area``. The taxable value is
    ``quantity * unit_value`` for land and 1 for flat taxes.

    ``exempt_below`` exempts a value below it entirely; it is not a deduction.
    Progressive brackets are marginal: each rate applies to the slice of the
    value inside its bracket.
    """
    base = tax.get("base") or "flat"
    method = tax.get("method") or "fixed"
    qty = float(quantity or 0)
    if base == "flat":
        value = 1.0 if qty > 0 else 0.0
    else:
        value = qty * float(tax.get("unit_value", 1) or 0)
    out = {"code": tax.get("code", ""), "quantity": qty, "value": value,
           "amount": 0.0, "exempt": False, "lines": []}
    if value <= 0:
        return out
    exempt_below = float(tax.get("exempt_below") or 0)
    if exempt_below and value < exempt_below:
        out["exempt"] = True
        return out

    lines = []
    if method == "fixed":
        amount = float(tax.get("amount") or 0)
        lines.append({"from": 0.0, "to": value, "rate_bp": 0, "amount": amount})
    elif method == "proportional":
        rate = int(tax.get("rate_bp") or 0)
        amount = value * rate / 10000.0
        lines.append({"from": 0.0, "to": value, "rate_bp": rate, "amount": amount})
    elif method == "progressive":
        amount = 0.0
        lower = 0.0
        for bracket in tax.get("brackets") or []:
            up_to = bracket.get("up_to")
            rate = int(bracket.get("rate_bp") or 0)
            upper = value if up_to is None else min(value, float(up_to))
            if upper > lower:
                part = (upper - lower) * rate / 10000.0
                lines.append({"from": lower, "to": upper, "rate_bp": rate,
                              "amount": round(part, 6)})
                amount += part
            if up_to is None or value <= float(up_to):
                break
            lower = float(up_to)
    else:
        raise ValueError("unknown tax method '%s'" % method)

    out["amount"] = round(amount, 6)
    out["lines"] = lines
    return out


# ---------------------------------------------------------------------------
# realm facade — reads from context, writes recorded as effects
# ---------------------------------------------------------------------------


class _Users:
    def get(self, user_id):
        """A user projection, or ``None``.

        Served from the injected context when the host pre-gathered this user
        (the common case: the one the hook fired for), otherwise fetched live
        over ``rpc`` and memoized for repeat lookups in the same call.
        """
        users = _state.context.get("users")
        if users is None:
            users = {}
            _state.context["users"] = users
        if user_id in users:
            return users[user_id]
        ok, value = _rpc("user.get", {"user_id": user_id})
        if not ok:
            return None
        users[user_id] = value
        return value


class _Proposals:
    def find_executed(self, target_principal, profile_name, change="assign"):
        """An executed governance proposal authorizing one role change, or
        ``None``. *change* is ``"assign"`` or ``"revoke"``.

        Always a live read: an approval that landed a second ago has to count.
        """
        ok, value = _rpc("proposal.find_executed", {
            "target_principal": target_principal,
            "profile_name": profile_name,
            "change": change,
        })
        return value if ok else None


class _Invoices:
    def create(self, amount, currency, due_date, status="Pending",
               user_id="", metadata=""):
        """Record an ``invoice.create`` effect; return ``{"id": <ref token>}``."""
        return _state.record("invoice.create", {
            "amount": amount, "currency": currency, "due_date": due_date,
            "status": status, "user_id": user_id, "metadata": metadata,
        })


class _Notifications:
    def create(self, topic, title, message, user_id="", **fields):
        """Record a ``notification.create`` effect."""
        kwargs = {"topic": topic, "title": title, "message": message,
                  "user_id": user_id}
        kwargs.update(fields)
        return _state.record("notification.create", kwargs)


class _Members:
    def activate(self, user_id, **fields):
        """Record a ``member.activate`` effect."""
        kwargs = {"user_id": user_id}
        kwargs.update(fields)
        return _state.record("member.activate", kwargs)

    def assign_profile(self, user_id, profile_name):
        """Record a ``member.assign_profile`` effect."""
        return _state.record("member.assign_profile", {
            "user_id": user_id,
            "profile_name": profile_name,
        })

    def revoke_profile(self, user_id, profile_name):
        """Record a ``member.revoke_profile`` effect."""
        return _state.record("member.revoke_profile", {
            "user_id": user_id,
            "profile_name": profile_name,
        })


class _Treasury:
    def transfer(self, to_principal, amount, treasury_name=""):
        """Record a deferred ``treasury.transfer`` effect."""
        return _state.record("treasury.transfer", {
            "to_principal": to_principal,
            "amount": amount,
            "treasury_name": treasury_name,
        })


class _Taxes:
    def policy(self):
        """The tax parameters in force: ``{"source", "policy_id", "taxes"}``.

        ``source`` is ``policy`` (adopted in the realm), ``codex`` (the
        codex's own ``taxes`` config block) or ``none``.
        """
        if "tax_policy" in _state.context:
            return _state.context["tax_policy"]
        ok, value = _rpc("tax.policy", {})
        if not ok or value is None:
            return {"source": "none", "policy_id": "", "taxes": []}
        return value


class _Init:
    def apply_init_policy(self):
        codex_id = _state.context.get("codex_id", "")
        return _state.record("realm.apply_init_policy", {"codex_id": codex_id})

    def seed_org(self, template="departments"):
        codex_id = _state.context.get("codex_id", "")
        return _state.record("org.seed_template", {
            "codex_id": codex_id,
            "template": template,
        })

    def seed_justice(self):
        codex_id = _state.context.get("codex_id", "")
        return _state.record("justice.seed_template", {"codex_id": codex_id})


class _Realm:
    """Entry point codices import as ``realm``."""

    def __init__(self):
        self.users = _Users()
        self.proposals = _Proposals()
        self.invoices = _Invoices()
        self.notifications = _Notifications()
        self.members = _Members()
        self.treasury = _Treasury()
        self.taxes = _Taxes()
        self.init = _Init()

    def _read(self, key, verb, default):
        """Context value for *key*, falling back to a live ``rpc`` read.

        The host pre-gathers whatever a hook is likely to need, so the context
        hit is the normal path. The fallback matters for hooks whose context
        deliberately omits a key — notably ``get_config``, whose context cannot
        contain ``config`` without the host re-entering the very hook it is
        gathering for.
        """
        if key in _state.context:
            value = _state.context.get(key)
            return default if value is None else value
        ok, value = _rpc(verb, {})
        if not ok or value is None:
            return default
        return value

    def config(self):
        return self._read("config", "config.get", {})

    def now(self):
        return self._read("now", "time.now", {"epoch": 0, "ns": 0})

    def info(self):
        return self._read("realm", "realm.get", {})

    def currency(self):
        return self._read("currency", "currency.get", "")


realm = _Realm()


# ---------------------------------------------------------------------------
# ctx — the extension-facing facade (core.extension_bridge)
# ---------------------------------------------------------------------------
#
# Codices get ``realm``: a hook with no end user, whose writes are batched.
# Extensions get ``ctx``: an entry point invoked by a real caller, whose reads
# and writes both go live over ``rpc``.
#
# Nothing here takes an identity argument. ``ctx.caller()`` asks the host who
# is calling, and every read and write is scoped to that same host-side value.
# An extension cannot name a user, which is the entire point.


def _require_rpc(action, kwargs):
    """Live host call. Unlike ``_rpc`` this refuses to degrade quietly.

    An extension read that silently returned ``None`` when the bridge was
    missing would look exactly like "no rows", and an extension would happily
    render an empty list instead of failing.
    """
    try:
        _fn = rpc  # noqa: F821 - builtin injected by the subinterpreter
    except NameError:
        raise RuntimeError(
            "no capability bridge available: this extension must run "
            "sandboxed via core.extension_bridge"
        )
    if "action" in kwargs:
        raise ValueError("rpc kwargs cannot contain 'action'")
    return _fn(action, **kwargs)


class NeedEffect(Exception):
    """Raised by ``ctx.services.query`` on the pass that has no result yet.

    Caught by the sandbox dispatcher, never by extension code — catching it
    would swallow the request and the outcall would never happen.
    """

    def __init__(self, request):
        Exception.__init__(self, "effect not yet resolved: " + str(request))
        self.request = request


class ServiceCallError(Exception):
    """The host made the outcall and it came back an error."""


def _effect_key(name, params):
    """Must stay identical to ``core.async_bridge.effect_key``."""
    parts = []
    for k in sorted(params):
        parts.append(k + "=" + repr(params[k]))
    return name + "(" + ",".join(parts) + ")"


class _Services:
    """Inter-canister calls, for functions the manifest lists in
    ``async_functions``.

    A subinterpreter cannot wait for an outcall — see ``core.async_bridge`` for
    why — so the host runs the body more than once. ``query`` raises on the pass
    that has no answer, and returns the answer on the pass that does:

        txns = ctx.services.query("registry.get_transactions", limit=20)

    Because the body replays, an async function must not write. The host refuses
    write verbs during these calls rather than letting a write land once per
    round.

    The target canister is not yours to pick. You name a registered service and
    the host resolves where it points.
    """

    def __init__(self):
        self._resolved = {}

    def query(self, service, **params):
        key = _effect_key(service, params)
        if key in self._resolved:
            entry = self._resolved[key]
            if "error" in entry:
                raise ServiceCallError(entry["error"])
            return entry.get("value")
        raise NeedEffect({"service": service, "params": params})

    def resolved(self, service, **params):
        """Whether a result is already in hand, for a body that wants to do
        something else on the requesting pass instead of raising."""
        return _effect_key(service, params) in self._resolved


class _Treasury:
    """Epoch-based allocation of revenue into department funds.

    ``action`` is one verb for every mutating action, because whether a change
    applies directly or opens a vote depends on the governing org's policy — a
    decision the host makes, not this extension.

    Its three shapes of reply: ``applied == "direct"`` (done),
    ``requires_confirmation`` (a vote is needed and the caller has not agreed to
    start one yet), or ``applied == "proposal"`` (a vote is open).
    """

    def overview(self):
        return _require_rpc("treasury.overview", {})

    def allocation_status(self, period=None):
        return _require_rpc("treasury.allocation_status", {"period": period})

    def flows(self, period=None):
        return _require_rpc("treasury.flows", {"period": period})

    def budgets(self, period=None):
        return _require_rpc("treasury.budgets", {"period": period})

    def timeline(self, center_ts=None, before=20, after=20):
        return _require_rpc("treasury.timeline", {
            "center_ts": center_ts, "before": before, "after": after,
        })

    def action(self, kind, fields=None, confirm=False):
        """Apply a treasury action, or open a vote on it.

        ``fields`` is checked against a per-kind allowlist host-side and the
        action is rebuilt there, so an unrecognised key is an error rather than
        something silently dropped from what gets voted on.
        """
        return _require_rpc("treasury.action", {
            "kind": kind, "fields": fields or {}, "confirm": bool(confirm),
        })

    def disable_schedule(self):
        """Switch automation off. Never needs a vote — it is the safe direction."""
        return _require_rpc("treasury.disable_schedule", {})

    def issue_draft(self):
        """Freeze current working books as an unofficial draft snapshot."""
        return _require_rpc("treasury.issue_draft", {})


class _Tax:
    """Tax policy: a draft the extension edits, and the adopted version a codex
    reads. Adopting goes through the root org's policy host-side, with the same
    three reply shapes as ``treasury.action``."""

    def overview(self):
        return _require_rpc("tax.overview", {})

    def history(self, cursor=1, count=20):
        return _require_rpc("tax.history", {"cursor": cursor, "count": count})

    def save_draft(self, document, description=""):
        return _require_rpc("tax.save_draft", {
            "document": document, "description": description,
        })

    def discard_draft(self):
        return _require_rpc("tax.discard_draft", {})

    def adopt(self, confirm=False):
        return _require_rpc("tax.adopt", {"confirm": bool(confirm)})

    def assess(self, cursor=""):
        """Ask the active codex to assess one batch; pass back ``next_cursor``."""
        return _require_rpc("tax.assess", {"cursor": cursor or ""})


class _DeptDocs:
    """Encrypted documents shared with a department.

    The host decides who may read and who may manage, from department headship
    and realm admin — this extension never sees the plaintext and never gets to
    widen the department filter.
    """

    def list(self, department=""):
        return _require_rpc("dept_doc.list", {"department": department or ""})

    def get(self, id):
        """One document including its ciphertext, for decryption in the client."""
        return _require_rpc("dept_doc.get", {"id": id})

    def create(self, department, title):
        """Returns ``{"id", "scope"}``; the blob is attached separately, because
        the key scope embeds the id that does not exist until now."""
        return _require_rpc(
            "dept_doc.create", {"department": department, "title": title}
        )

    def update(self, id, title=None, ciphertext=None):
        payload = {"id": id}
        if title is not None:
            payload["title"] = title
        if ciphertext is not None:
            payload["ciphertext"] = ciphertext
        return _require_rpc("dept_doc.update", payload)

    def delete(self, id):
        return _require_rpc("dept_doc.delete", {"id": id})

    def reshare_list(self, department=""):
        return _require_rpc(
            "dept_doc.reshare_list", {"department": department or ""}
        )

    def reshare_dismiss(self, job_id):
        return _require_rpc("dept_doc.reshare_dismiss", {"id": job_id})

    def reshare_complete(self, job_id):
        return _require_rpc("dept_doc.reshare_complete", {"id": job_id})


class _Procurement:
    """RFP tendering: lifecycle, sealed bids, scoring, vendor reputation.

    Every method here is one host verb, and the host owns all four roles
    (requester, vendor, evaluator, approver) plus the lifecycle graph. Two things
    worth knowing when writing against it:

    * **Identity is never a parameter.** ``bid_create`` bids as the caller and
      ``scores_submit`` scores as the caller. There is no way to act as someone
      else, so there is no argument for it.
    * **Sealed means sealed.** While an RFP is open, ``bid_payload`` returns a
      bid's ciphertext only to the vendor who submitted it — not to the requester
      and not to an admin. Asking for payloads in ``bid_list`` is a request, not
      a grant; the host filters per bid.
    """

    def roles(self):
        """The caller's own roles, for hiding UI they cannot use. Advisory: every
        verb re-checks."""
        return _require_rpc("procurement.roles", {})

    # -- reads --

    def rfp_list(self, status=""):
        return _require_rpc("procurement.rfp_list", {"status": status or ""})

    def rfp_get(self, rfp_id):
        """One RFP with its full transition history."""
        return _require_rpc("procurement.rfp_get", {"rfp_id": rfp_id})

    def transitions(self, rfp_id):
        return _require_rpc("procurement.transitions", {"rfp_id": rfp_id})

    def bid_list(self, rfp_id, include_payload=False):
        return _require_rpc("procurement.bid_list", {
            "rfp_id": rfp_id, "include_payload": bool(include_payload),
        })

    def bid_payload(self, bid_id):
        return _require_rpc("procurement.bid_payload", {"bid_id": bid_id})

    def score_list(self, rfp_id):
        return _require_rpc("procurement.score_list", {"rfp_id": rfp_id})

    def evaluators(self):
        """Principals a vendor must wrap its bid key for."""
        return _require_rpc("procurement.evaluators", {})

    def vendor_get(self, vendor_id):
        return _require_rpc("procurement.vendor_get", {"vendor_id": vendor_id})

    def vendor_list(self):
        return _require_rpc("procurement.vendor_list", {})

    # -- lifecycle --

    def rfp_create(self, title, description="", rubric_json=None,
                   opens_at=0, closes_at=0):
        """Create a draft. ``rubric_json`` may be text or the parsed structure;
        weights must sum to 1.0 or the host refuses it."""
        return _require_rpc("procurement.rfp_create", {
            "title": title, "description": description,
            "rubric_json": rubric_json if rubric_json is not None else "[]",
            "opens_at": opens_at, "closes_at": closes_at,
        })

    def rfp_update(self, rfp_id, **fields):
        """Edit a draft. Omitted fields are left alone, which is why they go in a
        dict rather than being positional."""
        return _require_rpc("procurement.rfp_update", {
            "rfp_id": rfp_id, "fields": fields,
        })

    def rfp_publish(self, rfp_id):
        return _require_rpc("procurement.rfp_publish", {"rfp_id": rfp_id})

    def rfp_close(self, rfp_id):
        """Close early and reveal bids for evaluation. Admin only."""
        return _require_rpc("procurement.rfp_close", {"rfp_id": rfp_id})

    def demo_advance(self, rfp_id):
        """Advance one stage, bypassing time and role gates. Test mode only."""
        return _require_rpc("procurement.demo_advance", {"rfp_id": rfp_id})

    def sweep(self):
        """Close every RFP whose bidding window has ended."""
        return _require_rpc("procurement.sweep", {})

    # -- bidding --

    def bid_create(self, rfp_id):
        """Reserve a bid id and key scope for the caller. The ciphertext is
        attached separately, because the scope embeds the new bid id."""
        return _require_rpc("procurement.bid_create", {"rfp_id": rfp_id})

    def bid_set_payload(self, bid_id, ciphertext, encryption_mode=""):
        return _require_rpc("procurement.bid_set_payload", {
            "bid_id": bid_id, "ciphertext": ciphertext,
            "encryption_mode": encryption_mode or "",
        })

    # -- scoring and award --

    def scores_submit(self, bid_id, scores):
        """``scores`` is ``{criterion_id: value}``, recorded against the caller."""
        return _require_rpc("procurement.scores_submit", {
            "bid_id": bid_id, "scores": scores or {},
        })

    def totals_compute(self, rfp_id):
        """Average the per-evaluator weighted totals onto each bid."""
        return _require_rpc("procurement.totals_compute", {"rfp_id": rfp_id})

    def award(self, rfp_id, winning_bid_id):
        return _require_rpc("procurement.award", {
            "rfp_id": rfp_id, "winning_bid_id": winning_bid_id,
        })

    def execute(self, rfp_id, note=""):
        return _require_rpc("procurement.execute", {
            "rfp_id": rfp_id, "note": note or "",
        })

    def vendor_flag(self, vendor_id, code, note="", rfp_id="", bid_id=""):
        """Append a conduct flag to a vendor's record. Admin only."""
        return _require_rpc("procurement.vendor_flag", {
            "vendor_id": vendor_id, "code": code, "note": note,
            "rfp_id": rfp_id, "bid_id": bid_id,
        })


class _Justice:
    """Courts, cases, verdicts, penalties, appeals, and private litigations.

    The host owns the whole authorization model, and three parts of it are worth
    knowing when writing against this:

    * **Litigations are private by default.** Visible to the submitter and the
      justice department, and to nobody else — not even the defendant. Reads are
      filtered per row, so ``verdicts()`` and ``appeals()`` return only what
      belongs to a case this caller may see.
    * **Identity is never a parameter.** ``file_case`` files as the caller,
      ``issue_verdict`` rules as the caller's own assignment on that case, and
      ``file_appeal`` appeals as the caller. There is no plaintiff, judge or
      appellant argument.
    * **Content is opaque.** ``create_litigation`` returns a key scope and the
      recipient principals; the client encrypts and calls
      ``set_litigation_content``. The canister never sees the plaintext.
    """

    def roles(self):
        """The caller's own standing, for hiding UI they cannot use."""
        return _require_rpc("justice.roles", {})

    def audience(self):
        """Principals to wrap a litigation's key for: the justice department."""
        return _require_rpc("justice.audience", {})

    # -- structure --

    def justice_systems(self, system_type=""):
        return _require_rpc(
            "justice.justice_systems", {"system_type": system_type or ""}
        )

    def courts(self, justice_system_id=None, status="", level=""):
        return _require_rpc("justice.courts", {
            "justice_system_id": justice_system_id,
            "status": status or "", "level": level or "",
        })

    def judges(self, court_id=None, status="", specialization=""):
        return _require_rpc("justice.judges", {
            "court_id": court_id, "status": status or "",
            "specialization": specialization or "",
        })

    def initialize(self):
        """Guarantee an active court exists. Idempotent; admin or justice head."""
        return _require_rpc("justice.initialize", {})

    def seed_courts(self):
        return _require_rpc("justice.seed_courts", {})

    def create_court(self, name, description="", jurisdiction="", level="",
                     justice_system_id=None, parent_court_id=None):
        return _require_rpc("justice.create_court", {
            "name": name, "description": description,
            "jurisdiction": jurisdiction, "level": level or "",
            "justice_system_id": justice_system_id,
            "parent_court_id": parent_court_id,
        })

    # -- cases --

    def cases(self, court_id=None, status="", plaintiff_id=None,
              defendant_id=None, user_id=None):
        """Cases the caller may see. The filters narrow; they never widen."""
        return _require_rpc("justice.cases", {
            "court_id": court_id, "status": status or "",
            "plaintiff_id": plaintiff_id, "defendant_id": defendant_id,
            "user_id": user_id,
        })

    def case(self, case_id):
        """One case with its verdicts and appeals. Refuses exactly as a missing
        case would, so ids cannot be probed."""
        return _require_rpc("justice.case", {"case_id": case_id})

    def file_case(self, court_id, defendant_id, title, description=""):
        """File a public case as the caller."""
        return _require_rpc("justice.file_case", {
            "court_id": court_id, "defendant_id": defendant_id,
            "title": title, "description": description,
        })

    def assign_judge(self, case_id, judge_id):
        return _require_rpc("justice.assign_judge", {
            "case_id": case_id, "judge_id": judge_id,
        })

    # -- private litigations --

    def litigations(self, from_id=1, page_size=25):
        """Paged. Justice members and admins see all; everyone else their own."""
        return _require_rpc("justice.litigations", {
            "from_id": from_id, "page_size": page_size,
        })

    def create_litigation(self, defendant_principal=None, court_id=None,
                          defendant_kind="", defendant_department="",
                          defendant_department_id="", defendant_quarter_id=""):
        """Open a private litigation. Returns ``{"id", "scope", "recipients"}``;
        the ciphertext is attached separately.

        ``defendant_principal`` may be a local principal or a ``realm://``
        User address. That address is not a venue.

        Only fields the caller actually set are sent: the host reads an absent key
        as "not specified" and falls back to the default court and an individual
        defendant.
        """
        fields = {
            "court_id": court_id,
            "defendant_principal": defendant_principal,
            "defendant_kind": defendant_kind,
            "defendant_department": defendant_department,
            "defendant_department_id": defendant_department_id,
            "defendant_quarter_id": defendant_quarter_id,
        }
        return _require_rpc("justice.create_litigation", {
            key: value for key, value in fields.items() if value
        })

    def set_litigation_content(self, case_id, ciphertext):
        return _require_rpc("justice.set_litigation_content", {
            "case_id": case_id, "ciphertext": ciphertext,
        })

    # -- verdicts, penalties, appeals --

    def verdicts(self, case_id=None):
        return _require_rpc("justice.verdicts", {"case_id": case_id})

    def issue_verdict(self, case_id, decision, reasoning="", penalties=None):
        """Rule as the caller's own assignment on this case. ``penalties`` is a
        list of ``{type, amount, currency, description, target_user_id}``."""
        return _require_rpc("justice.issue_verdict", {
            "case_id": case_id, "decision": decision,
            "reasoning": reasoning, "penalties": penalties or [],
        })

    def penalties(self, verdict_id=None, target_user_id=None, status=""):
        """Penalties on visible cases, plus any levied against the caller."""
        return _require_rpc("justice.penalties", {
            "verdict_id": verdict_id, "target_user_id": target_user_id,
            "status": status or "",
        })

    def execute_penalty(self, penalty_id):
        return _require_rpc("justice.execute_penalty", {"penalty_id": penalty_id})

    def waive_penalty(self, penalty_id, reason=""):
        return _require_rpc("justice.waive_penalty", {
            "penalty_id": penalty_id, "reason": reason,
        })

    def appeals(self, case_id=None, appellant_id=None, status="", court_id=None):
        return _require_rpc("justice.appeals", {
            "case_id": case_id, "appellant_id": appellant_id,
            "status": status or "", "court_id": court_id,
        })

    def file_appeal(self, case_id, grounds, appellate_court_id=None):
        """Appeal as the caller, who must be a party to the case."""
        return _require_rpc("justice.file_appeal", {
            "case_id": case_id, "grounds": grounds,
            "appellate_court_id": appellate_court_id,
        })

    def decide_appeal(self, appeal_id, decision, reasoning=""):
        return _require_rpc("justice.decide_appeal", {
            "appeal_id": appeal_id, "decision": decision,
            "reasoning": reasoning,
        })

    def withdraw_appeal(self, appeal_id):
        return _require_rpc("justice.withdraw_appeal", {"appeal_id": appeal_id})

    def transfer_case(
        self, case_id, dest=None, ciphertext="", wrapped_deks=None, origin_scope="",
    ):
        """Judge Transfer: freeze origin and send ``justice.transfer``.

        ``dest`` is a dest canister id (not a filer venue picker).
        """
        payload = {"case_id": case_id, "dest": dest}
        if ciphertext:
            payload["ciphertext"] = ciphertext
        if wrapped_deks is not None:
            payload["wrapped_deks"] = wrapped_deks
        if origin_scope:
            payload["origin_scope"] = origin_scope
        return _require_rpc("justice.transfer_case", payload)

    def begin_executing(self, case_id):
        """Declare the verdict final so penalties may run."""
        return _require_rpc("justice.begin_executing", {"case_id": case_id})

    def close_case(self, case_id):
        return _require_rpc("justice.close_case", {"case_id": case_id})

    def statistics(self):
        """Realm-wide counts. Aggregates only, so not filtered per caller."""
        return _require_rpc("justice.statistics", {})


class _Entities:
    """Generic gated reads. The host applies caller scope before returning."""

    def list(self, type, where=None, limit=1000):
        """Rows of *type* visible to the caller.

        ``where={"mine": True}`` self-scopes against the authenticated caller;
        there is no way to ask for another user's rows.
        """
        return _require_rpc("entity.list", {
            "type": type, "where": where or {}, "limit": limit,
        })

    def rows(self, type, where=None, limit=1000):
        """Just the rows, for the common case that ignores the total."""
        return self.list(type, where, limit).get("rows", [])

    def get(self, type, id):
        """One row by id, or ``None`` when absent *or* not visible."""
        return _require_rpc("entity.get", {"type": type, "id": id})

    def schema(self):
        """The types and fields this extension is allowed to read."""
        return _require_rpc("schema.describe", {})

    def erd(self):
        """The realm's full entity-relationship schema (metadata, not rows)."""
        return _require_rpc("schema.entities", {})


class _Own:
    """CRUD over the extension's *own* declared entities.

    Generic writes are safe here — unlike shared ``ggg`` types — because the
    host derives the namespace from the calling extension id, so these can
    only ever touch ``ext_<this extension>::*``.

    The schema itself is declared in the manifest's ``entities`` block, not
    here: a live ORM class cannot cross into the sandbox, but the declaration
    that produces one is just data.
    """

    def create(self, type, **values):
        return _require_rpc("ext_entity.create", {"type": type, "values": values})

    def list(self, type, where=None, limit=1000):
        return _require_rpc("ext_entity.list", {
            "type": type, "where": where or {}, "limit": limit,
        })

    def rows(self, type, where=None, limit=1000):
        return self.list(type, where, limit).get("rows", [])

    def get(self, type, id):
        return _require_rpc("ext_entity.get", {"type": type, "id": id})

    def update(self, type, id, **values):
        return _require_rpc("ext_entity.update", {
            "type": type, "id": id, "values": values,
        })

    def delete(self, type, id):
        return _require_rpc("ext_entity.delete", {"type": type, "id": id})


class _Console:
    """Realm-setup console: organizations, seats, invites, citizen import.

    Invite codes are credentials, so minting and listing them are gated on
    ``invite.manage`` and importing on ``user.add``, checked host-side.
    """

    def overview(self):
        return _require_rpc("console.overview", {})

    def regenerate_invite(self, department, profile, **fields):
        return _require_rpc("console.regenerate_invite", dict(
            fields, department=department, profile=profile,
        ))

    def import_citizens(self, citizens, **fields):
        return _require_rpc("console.import_citizens", dict(
            fields, citizens=citizens,
        ))

    def citizen_invites(self, offset=0, limit=100, only_pending=False):
        return _require_rpc("console.list_citizen_invites", {
            "offset": offset, "limit": limit, "only_pending": only_pending,
        })


class _Notifications:
    """Messages, department broadcasts, and outbound email.

    Reads are already scoped: ``list`` returns only what the caller may see,
    and the id-addressed verbs refuse an id the caller cannot see, so this
    facade has no filtering of its own to get wrong.
    """

    def list(self):
        return _require_rpc("notification.list", {})

    def departments(self):
        return _require_rpc("notification.departments", {})["departments"]

    def create(self, title, message, **fields):
        return _require_rpc("notification.create", dict(
            fields, title=title, message=message,
        ))

    def mark_read(self, id, read=True):
        return _require_rpc("notification.mark_read", {"id": id, "read": read})

    def delete(self, id):
        return _require_rpc("notification.delete", {"id": id})

    def email_settings(self):
        """The caller's own address and delivery preference."""
        return _require_rpc("notification.email_settings", {})

    def set_email(self, email):
        return _require_rpc("notification.set_email", {"email": email})

    def set_email_unverified(self, email):
        return _require_rpc("notification.set_email_unverified", {
            "email": email,
        })

    def request_email_verification(self, email):
        return _require_rpc("notification.request_email_verification", {
            "email": email,
        })

    def verify_email_code(self, code):
        return _require_rpc("notification.verify_email_code", {"code": code})

    def set_email_preferences(self, enabled):
        return _require_rpc("notification.set_email_preferences", {
            "email_notifications_enabled": enabled,
        })

    def pending_emails(self):
        return _require_rpc("notification.pending_emails", {})["notifications"]

    def mark_email_sent(self, id, success=False, error=""):
        return _require_rpc("notification.mark_email_sent", {
            "id": id, "success": success, "error": error,
        })

    def send_test_email(self, to, subject=None, body=None):
        kwargs = {"to": to}
        if subject is not None:
            kwargs["subject"] = subject
        if body is not None:
            kwargs["body"] = body
        return _require_rpc("notification.send_test_email", kwargs)

    def send_join_link(self, emails=None, to=""):
        kwargs = {}
        if emails is not None:
            kwargs["emails"] = emails
        if to:
            kwargs["to"] = to
        return _require_rpc("notification.send_join_link", kwargs)


class _ExtensionAccess:
    """Who may use which extension.

    ``target`` is one of ``user``, ``department``, ``profile``. Both writes
    are privilege changes, so the host checks ``role.assign`` /
    ``role.revoke`` against the caller.
    """

    def list(self):
        return _require_rpc("extension_access.list", {})

    def grant(self, extension, target, name):
        return _require_rpc("extension_access.grant", {
            "extension": extension, "target": target, "name": name,
        })

    def revoke(self, extension, target, name):
        return _require_rpc("extension_access.revoke", {
            "extension": extension, "target": target, "name": name,
        })


class _Lands:
    """The land registry.

    Reads are paginated host-side; writes are typed because land carries
    invariants (one parcel per H3 cell, residential land belongs to members)
    that a generic update could not express, and because ownership must not be
    settable as an ordinary field.
    """

    def list(self, from_id=1, page_size=10, all=False):
        return _require_rpc("land.list", {
            "from_id": from_id, "page_size": page_size, "all": all,
        })

    def get(self, land_id):
        return _require_rpc("land.get", {"land_id": land_id})

    def map(self, min_x=0, max_x=20, min_y=0, max_y=20, from_id=1,
            page_size=10):
        return _require_rpc("land.map", {
            "min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y,
            "from_id": from_id, "page_size": page_size,
        })

    def create(self, **fields):
        return _require_rpc("land.create", fields)

    def update(self, land_id, **fields):
        return _require_rpc("land.update", dict(fields, land_id=land_id))

    def set_owner(self, land_id, owner_user_id=None,
                  owner_organization_id=None):
        return _require_rpc("land.set_owner", {
            "land_id": land_id,
            "owner_user_id": owner_user_id,
            "owner_organization_id": owner_organization_id,
        })

    def prepare_nft(self, land_id, owner_principal):
        return _require_rpc("land.prepare_nft", {
            "land_id": land_id, "owner_principal": owner_principal,
        })

    def set_nft_token(self, land_id, nft_token_id):
        return _require_rpc("land.set_nft_token", {
            "land_id": land_id, "nft_token_id": nft_token_id,
        })


class _MemberDirectory:
    """Admin views over the member directory.

    Every method here is gated host-side on ``user.view`` (or
    ``notification.send`` for the write), so holding the capability is not by
    itself enough — the *caller* must also hold the operation.
    """

    def list(self):
        return _require_rpc("member.list", {})

    def profile(self, subject):
        """Full profile of *subject*.

        ``subject`` names who is being asked about; who is *asking* is the
        host-injected caller and is never passed from here.
        """
        return _require_rpc("member.profile", {"subject": subject})

    def notifications(self, subject):
        return _require_rpc("member.notifications", {"subject": subject})

    def notify(self, subject, title, message, **fields):
        """Send a member a notification.

        Shares ``notification.create`` with the notifications extension rather
        than having its own verb: both create the same row, and two verbs with
        different authorization would just mean an attacker picks the weaker.
        """
        return _require_rpc("notification.create", dict(
            fields, audience_type="user", subject=subject,
            title=title, message=message,
        ))

    def private_data_envelope(self, scope):
        """The caller's own key envelope for a scope, if it was shared."""
        return _require_rpc("crypto.envelope", {"scope": scope})


class _Zones:
    """Typed zone writes. Ownership and the one-zone-per-cell invariant are
    enforced host-side, so they cannot be skipped from in here."""

    def create(self, h3_index, name="Zone", description="",
               zone_type="unassigned", metadata="{}"):
        return _require_rpc("zone.create", {
            "h3_index": h3_index, "name": name, "description": description,
            "zone_type": zone_type, "metadata": metadata,
        })

    def update(self, h3_index, **fields):
        return _require_rpc("zone.update", dict(fields, h3_index=h3_index))

    def delete(self, h3_index):
        return _require_rpc("zone.delete", {"h3_index": h3_index})


class _Ctx:
    """Entry point extensions import as ``ctx``."""

    def __init__(self):
        self.entities = _Entities()
        self.own = _Own()
        self.members = _MemberDirectory()
        self.lands = _Lands()
        self.extension_access = _ExtensionAccess()
        self.notifications = _Notifications()
        self.console = _Console()
        self.zones = _Zones()
        self.services = _Services()
        self.dept_docs = _DeptDocs()
        self.treasury = _Treasury()
        self.tax = _Tax()
        self.procurement = _Procurement()
        self.justice = _Justice()
        self._caller = None

    def caller(self):
        """The authenticated caller, as the host sees them.

        ``{"id", "name", "registered", "is_admin"}``. Memoized per call: a
        subinterpreter serves exactly one entry point invocation, so the
        caller cannot change underneath it.
        """
        if self._caller is None:
            self._caller = _require_rpc("caller.get", {})
        return self._caller

    def caller_id(self):
        return self.caller().get("id", "")

    def is_admin(self):
        return bool(self.caller().get("is_admin"))

    def now(self):
        """Consensus time in nanoseconds — the sandbox has no clock."""
        return _require_rpc("time.now", {}).get("nanos", 0)

    def now_seconds(self):
        return _require_rpc("time.now", {}).get("seconds", 0)

    def log(self, message):
        """Write to the canister log, tagged with this extension's id."""
        return _require_rpc("log.write", {"message": str(message)})

    def system_snapshot(self, sections=None):
        """Admin-gated operational diagnostics.

        ``sections`` selects a subset of ``runtime, db, canister, tokens,
        files, extensions``; omit it for all of them.
        """
        return _require_rpc("system.snapshot", {"sections": sections})

    def realm_info(self):
        """``{"canister_id", "registry_canister_id", "version"}`` — how this
        realm is addressed from outside."""
        return _require_rpc("realm.info", {})

    def departments(self):
        """Departments the caller can see, with member principals."""
        return _require_rpc("department.list", {})["departments"]


ctx = _Ctx()

__all__ = [
    "hook", "realm", "ctx", "iso_days_from", "tax_period", "tax_breakdown",
    "NeedEffect", "ServiceCallError",
]
'''

exec(compile(GGG_SDK_SOURCE, "ggg_sdk.py", "exec"), globals())
