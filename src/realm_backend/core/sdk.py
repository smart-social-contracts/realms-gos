# The in-sandbox SDK, kept as source text: the canister freezes modules to bytecode, but the sandbox needs
# source to load as `ggg_sdk` inside each subinterpreter. It imports nothing; only plain data crosses.
SOURCE = r'''
"""ggg_sdk: what a codex hook sees inside the sandbox.

    from ggg_sdk import hook, realm

    @hook
    def on_user_register(args):
        inv = realm.invoice(args["user"], realm.config["fees"]["registration"], "registration")
        realm.notify(args["user"], "Welcome", "Your invoice: " + inv["invoice"])

Reads come from the context the host gathered (`realm.config`, `realm.info`, `realm.now`) or from
`realm.call(op, **args)`, a read operation run by the host. Writes are recorded as `(op, args)` effects;
the host applies them through the realm's operations after the hook returns, limited to the codex's
declared capabilities. `realm.effect` returns a reference: `ref["invoice"]` stands for the id the
host creates and may be used in later effects and in the hook's result.
"""

_state = {"context": {}, "effects": []}


def hook(fn):
    def run(args, context):
        _state["context"], _state["effects"] = context, []
        try:
            result = fn(args)
        except Exception as e:  # the sandbox boundary: only plain data leaves
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        return {"ok": True, "result": result, "effects": _state["effects"]}

    run.__name__ = fn.__name__
    return run


class Ref:
    def __init__(self, index):
        self.index = index

    def __getitem__(self, key):
        return "$eff:%d:%s" % (self.index, key)


def deep_get(data, path, default=None):
    for key in path.split("."):
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return data


class Realm:
    @property
    def context(self):
        return _state["context"]

    @property
    def config(self):
        return self.context.get("config") or {}

    @property
    def info(self):
        return self.context.get("realm") or {}

    @property
    def now(self):
        return self.context.get("now", 0)

    @property
    def stage(self):
        return self.info.get("status", "")

    @property
    def currency(self):
        return deep_get(self.config, "currency.symbol") or self.info.get("accounting_currency", "")

    def setting(self, path, default=None):
        return deep_get(self.config, path, default)

    def units(self, amount):
        """A config amount in whole tokens, in the token's smallest unit."""
        return int(round(float(amount) * 10 ** int(self.info.get("accounting_currency_decimals", 8))))

    def call(self, op, **args):
        reply = rpc(op, **args)  # noqa: F821 - injected by the host into the subinterpreter
        if not reply.get("ok"):
            raise RuntimeError("%s: %s" % (op, reply.get("error")))
        return reply

    def effect(self, op, **args):
        _state["effects"].append([op, args])
        return Ref(len(_state["effects"]) - 1)

    def every(self, op, key, **args):
        """All rows of a paged read operation."""
        cursor = 1
        while cursor:
            reply = self.call(op, **dict(args, **{"from": cursor}))
            for row in reply.get(key, []):
                yield row
            cursor = reply.get("next")

    def members(self):
        return [u for u in self.every("user_list", "users") if u.get("status") == "active"]

    def invoice(self, user, amount, reason, days=30):
        return self.effect("invoice_create", payer="User:" + user, amount=self.units(amount),
                           symbol=self.currency, due_at=self.now + int(days * 86400), reason=reason)

    def notify(self, user, title, body, topic="", href=""):
        return self.effect("notification_send", recipient="User:" + user, topic=topic, title=title,
                           body=body, href=href)

    def configure(self, **fields):
        return self.effect("realm_configure", **fields)

    def bill_members(self, amount, reason, title, body):
        """An invoice and a notice to every active member who is not an admin; returns how many."""
        billed = 0
        for u in self.members():
            if "admin" not in u.get("profiles", []):
                self.invoice(u["id"], amount, reason, self.setting("membership.invoice_validity_days", 30))
                self.notify(u["id"], title, body, "billing", "/extensions/member_dashboard")
                billed += 1
        return billed

    def run_payroll(self, period):
        """One payroll run per seeded department that has a fund and salaried seats."""
        for d in self.setting("seed.departments", []):
            if d.get("fund") and any(p.get("salary") for p in d.get("positions", [])):
                self.effect("payroll_run", department=d["name"], period=period)

    def seed(self):
        """The codex's `seed`: departments with fund, policy and positions, then the courts of the justice
        department. Departments that exist are left alone, so it can run again."""
        seed = self.setting("seed", {})
        existing = set(d["id"] for d in self.every("department_list", "departments"))
        for d in seed.get("departments", []):
            if d["name"] not in existing:
                self.seed_department(d)
        justice = seed.get("justice")
        if justice and justice["department"] not in existing:
            self.seed_courts(justice["courts"], "Department:" + justice["department"])

    def seed_department(self, d):
        # Each seat gets a profile of the same key carrying the department's permissions.
        self.effect("department_create", department=d["name"], description=d.get("description", ""))
        if d.get("policy"):
            self.effect("department_set_policy", department=d["name"], **d["policy"])
        if d.get("fund"):
            self.effect("fund_create", fund=d["fund"], name=d["name"], department=d["name"])
        for p in d.get("positions", []):
            seat = key(d["name"], p["title"])
            self.effect("profile_create", profile=seat, allowed_to=d.get("permissions", []))
            self.effect("position_create", department=d["name"], position=seat, title=p["title"], profile=seat,
                        headcount=p.get("headcount", 1), salary=self.units(p.get("salary", 0)))

    def seed_courts(self, courts, operator):
        """Courts listed top-down, each after its parent; capital-scoped ones only on the capital."""
        made = {}
        for c in courts:
            if c.get("scope") == "capital" and not self.info.get("is_capital", True):
                continue
            parent = made.get(c.get("parent"))
            made[c["name"]] = self.effect("court_create", name=c["name"], operator=operator,
                                          level=c.get("level", "first_instance"),
                                          jurisdiction=c.get("jurisdiction", ""),
                                          parent=parent["court"] if parent else "")


def key(*parts):
    """`Citizenship & Identity`, `kyc_officer` -> `citizenship-identity.kyc_officer`."""
    return ".".join("-".join("".join(ch if ch.isalnum() or ch == "_" else " " for ch in p.lower()).split())
                    for p in parts)


realm = Realm()
'''
