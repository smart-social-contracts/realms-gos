# Build, test and publish a codex

This page builds `town`, a small codex: a registration fee once the realm bills, a welcome notice,
and two departments seeded at install.

## 1. Create the package

```text
town/
├── manifest.json
└── backend/
    └── hooks.py
```

> **Note.** `realms new codex <dir>` still writes the previous hook API (`codex_api_version: 1`,
> `backend/entry.py`, `get_config`). This realm refuses that version
> (`this realm runs codex API 2, not 1`), so start from the files below, or from
> [agora](https://github.com/smart-social-contracts/realms-codices/tree/refactor/slim/codices/agora).

`manifest.json`:

```json
{
  "id": "town",
  "version": "1.0.0",
  "name": "Town",
  "description": "A small town: a registration fee and two departments.",
  "kind": "codex",
  "codex_api_version": 2,
  "hooks": ["init", "on_user_register"],
  "capabilities": ["department_list", "department_create", "department_set_policy",
                   "invoice_create", "notification_send"],
  "dependencies": [],
  "fees": { "registration": 5 },
  "membership": { "invoice_validity_days": 14 },
  "seed": {
    "departments": [
      { "name": "Works", "description": "Roads and parks", "policy": { "m": 2, "n": 3 } },
      { "name": "Library", "description": "Books" }
    ]
  }
}
```

`backend/hooks.py`:

```python
from ggg_sdk import hook, realm


@hook
def init(args):
    realm.seed()
    return {"seeded": True}


@hook
def on_user_register(args):
    user, fee = args["user"], realm.setting("fees.registration", 0)
    if realm.stage in ("beta", "production") and fee > 0:
        days = realm.setting("membership.invoice_validity_days", 30)
        invoice = realm.invoice(user, fee, "registration", days)
        realm.notify(user, "Welcome", "Your registration invoice is " + invoice["invoice"] + ".")
        return {"invoice": invoice["invoice"]}
    realm.notify(user, "Welcome", "Nothing to pay for now.")
    return {"stage": realm.stage}
```

Check `capabilities` against what the hooks do:

- `realm.seed()` reads `department_list` and may create departments with a policy.
- `realm.invoice` and `realm.notify` are `invoice_create` and `notification_send`.
- Departments here have no `fund` or `positions`, so `fund_create`, `profile_create` and
  `position_create` are not needed. Add them if the seed grows.

See the [SDK](sdk.md) for every helper and the operation it uses.

## 2. Test the hooks alone

The SDK is plain Python, so a hook can run in a test with canned reads, without a realm. This is
the pattern of the codices repo's
[`tests/test_hooks.py`](https://github.com/smart-social-contracts/realms-codices/blob/refactor/slim/tests/test_hooks.py).
Point `REALM_BACKEND` at a realms-gos checkout's `src/realm_backend`.

```python
import json, os, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parents[1]
SDK = {}
exec((pathlib.Path(os.environ["REALM_BACKEND"]) / "core" / "sdk.py").read_text(), SDK)
READS = {"department_list": {"departments": [{"id": "Administration"}], "next": None}}


def run(fn, args=None, **info):
    sdk = type(sys)("ggg_sdk")
    sdk.rpc = lambda op, **kw: {"ok": True, **READS[op]}
    exec(SDK["SOURCE"], sdk.__dict__)
    sys.modules["ggg_sdk"] = sdk
    manifest = json.loads((HERE / "manifest.json").read_text())
    hooks = {}
    exec((HERE / "backend" / "hooks.py").read_text(), hooks)
    realm = {"status": "alpha", "accounting_currency": "REALM", "accounting_currency_decimals": 8, **info}
    out = hooks[fn](args or {}, {"config": manifest, "now": 1_790_000_000, "realm": realm})
    assert out["ok"], out
    assert {op for op, _ in out["effects"]} <= set(manifest["capabilities"])
    return out["result"], out["effects"]


def test_init_seeds_both_departments():
    _, effects = run("init")
    assert [a["department"] for op, a in effects if op == "department_create"] == ["Works", "Library"]
    assert ["department_set_policy", {"department": "Works", "m": 2, "n": 3}] in effects


def test_registration_is_free_until_beta():
    result, effects = run("on_user_register", {"user": "bob"})
    assert result == {"stage": "alpha"} and [op for op, _ in effects] == ["notification_send"]


def test_registration_invoice_at_beta():
    result, effects = run("on_user_register", {"user": "bob"}, status="beta")
    (_, invoice), (_, notice) = effects
    assert result == {"invoice": "$eff:0:invoice"}
    assert (invoice["payer"], invoice["amount"], invoice["symbol"]) == ("User:bob", 500_000_000, "REALM")
    assert notice["body"] == "Your registration invoice is $eff:0:invoice."
```

```bash
REALM_BACKEND=../realms-gos/src/realm_backend pytest tests
```

The `$eff:0:invoice` placeholders become the real invoice id when a realm applies the effects.

## 3. Test it in the realm

The realm's own tests install the first-party codices into a test world and run their hooks
there, with Cedar and the real operations
([`tests/ggg/test_codex.py`](../../tests/ggg/test_codex.py)). The same works for a new codex:

```python
from api import codex
from core import op
import world
from ggg import Department, Invoice, Realm
from kit import ok


def test_town_in_a_realm():
    world.build()
    Realm["1"].require_marketplace_approval = False    # trust any code, as a local realm does
    ok("codex_install", "ctl", manifest=MANIFEST, source=SOURCE)
    assert Department["Works"] and Department["Library"]
    Realm["1"].status = "beta"
    out = op.run(codex.hook("on_user_register", {"user": "bob"}))
    assert Invoice[out["result"]["invoice"]].payer == "User:bob"
```

`MANIFEST` is the parsed `manifest.json` and `SOURCE` the text of `hooks.py`. See
[Testing](../development/testing.md) for `world`, `kit` and how to run these tests.

## 4. Try it in a local realm

```bash
realms local start                          # once
realms local publish ./town                 # to the local file registry, under ext/town/1.0.0
realms local setup --codex town             # install its extensions and the codex, set the token, launch
realms local call codex_status
```

`setup` works on a realm still in setup. Publish a new `version` to try a change, then install it
with `realms local call codex_install '{"codex": "town", "version": "1.0.1"}'`.

> **Note.** `realms local` runs the released realm matching your CLI, and no release runs codex API
> 2 yet (the latest, v0.8.0-rc2, predates it). Until the next release, steps 2 and 3 are the
> way to test a codex.

## 5. Publish for real realms

A codex is published like an extension, under `ext/<id>/<version>`
([steps](../extensions/build-test-publish.md#5-publish-for-real-realms)): a listing and an upload
on the marketplace, then a review. For a realm to install it:

- **From the registry:** a member proposes `codex_install {"codex": "town", "version": "1.0.0"}`.
  It is governed realm-wide, so it runs after the vote passes. The realm checks the marketplace's
  approval of `ext/town/1.0.0`.
- **Inline:** `codex_install {"manifest": …, "source": …}` needs the code's checksum pinned in the
  realm's `trusted_approvers` first. See
  [Trust](../architecture/trust-and-registry.md).

If a new version misbehaves, `codex_revert` goes back to the one it replaced, by the same vote.

## Checklist

- [ ] `kind: "codex"` and `codex_api_version: 2`
- [ ] `hooks` lists exactly the hooks in `hooks.py`
- [ ] `capabilities` covers every read and effect, and nothing more
- [ ] `init` is safe to run again
- [ ] Hook tests pass, including at the stages your hooks branch on
- [ ] A new `version` for each release
