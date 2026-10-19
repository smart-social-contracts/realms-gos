"""__CODEX_NAME__ codex — hook API entry point (codex_api_version 1).

A codex is the realm's constitution: the realm calls it at well-known hook
points and reads its policy from ``get_config``. This one is the minimal
shape of the Agora codex:

  get_config  — the manifest's config blocks (onboarding, lifecycle, fees,
                governance, membership), the single source of realm policy
  init        — post-install realm setup (idempotent): summary into
                ``Realm.manifest_data`` and the registration policy

More hooks (``seed``, ``on_user_register``, ``on_stage_change``,
``on_treasury_send``, …) are optional; see the Agora codex for examples.
"""

import json
import os

from _cdk import ic

_DIR = os.path.dirname(__file__)

# Manifest keys that are packaging metadata, not realm configuration.
_NON_CONFIG_KEYS = {
    "id", "name", "version", "kind", "codex_api_version", "description",
    "author", "dependencies", "extension_overrides", "data_files",
    "profiles", "categories", "icon", "show_in_sidebar", "sidebar_label",
    "doc_url", "permissions", "parameters", "entry_access",
}


def _manifest() -> dict:
    # Installed layout: manifest.json next to entry.py (backend/ prefix is
    # stripped at install). Source layout (local tests): one level up.
    for candidate in (
        os.path.join(_DIR, "manifest.json"),
        os.path.join(os.path.dirname(_DIR), "manifest.json"),
    ):
        try:
            with open(candidate, "r") as f:
                return json.loads(f.read())
        except FileNotFoundError:
            continue
        except Exception as e:
            ic.print(f"__CODEX_ID__: could not load manifest.json: {e}")
            return {}
    ic.print("__CODEX_ID__: manifest.json not found")
    return {}


def _realm():
    from ggg import Realm

    realms = Realm.instances()
    return realms[0] if realms else None


def get_config(args: str) -> str:
    """Realm configuration blocks declared by this codex."""
    config = {k: v for k, v in _manifest().items() if k not in _NON_CONFIG_KEYS}
    return json.dumps(config)


def init(args: str) -> str:
    """Post-install realm setup. Runs again on reinstall, so it must be idempotent."""
    realm = _realm()
    if not realm:
        return json.dumps({"success": False, "error": "No Realm found"})

    manifest = _manifest()
    summary = {
        "onboarding": manifest.get("onboarding", {}),
        "lifecycle": manifest.get("lifecycle", {}),
        "dependencies": manifest.get("dependencies", []),
    }
    # Keep keys that other parts of the realm wrote (e.g. config_overrides).
    try:
        existing = json.loads(getattr(realm, "manifest_data", "") or "{}") or {}
        for key, value in existing.items():
            summary.setdefault(key, value)
    except Exception:
        pass
    realm.manifest_data = json.dumps(summary)

    registration = (manifest.get("onboarding", {}) or {}).get("registration", {}) or {}
    if "open_registration" in registration:
        realm.open_registration = bool(registration["open_registration"])

    ic.print("__CODEX_ID__: init complete")
    return json.dumps({"success": True, "codex": "__CODEX_ID__"})
