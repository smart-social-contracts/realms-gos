"""Letting a developer upload a submission's files from the browser.

The files go straight from the browser to the file registry, which only
accepts writes from a namespace's publishers. This canister is a granter on
the registry (its ``_granters`` ACL): ``prepare_upload`` checks that the
caller may publish this item and then asks the registry to make them a
publisher of exactly ``<kind>/<id>/<version>``. The registry applies its
own rule on top: a package already published by someone else is refused.
"""

import json
from typing import Dict

from _cdk import Async, CallResult, Principal, Service, ic, service_update, text
from api.approval import _unwrap_call_result
from api.config import get_file_registry_canister_id
from api.developers import is_suspended
from api.licenses import has_active_license
from api.verification import VALID_KINDS, _get_listing
from ic_python_logging import get_logger

logger = get_logger("api.uploads")

ANONYMOUS = "2vxsx-fae"
# basilisk's CPython has no `re` (its import hook hands back an empty stub).
_ALNUM = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")


def _is_id_segment(s: str) -> bool:
    return bool(s) and s[0] in _ALNUM and all(c in _ALNUM or c in "_.-" for c in s)


def _is_semver(v: str) -> bool:
    """1.2.3, optionally followed by -pre or +build of [0-9A-Za-z.-]."""
    cut = min([i for i in (v.find("-"), v.find("+")) if i >= 0], default=len(v))
    core, suffix = v[:cut], v[cut + 1:]
    nums = core.split(".")
    if len(nums) != 3 or not all(n and all(c in "0123456789" for c in n) for n in nums):
        return False
    return cut == len(v) or (bool(suffix) and all(c in _ALNUM or c in ".-" for c in suffix))


class FileRegistryGrantService(Service):
    @service_update
    def grant_publish(self, args: text) -> text: ...


def _is_controller() -> bool:
    try:
        return bool(ic.is_controller(ic.caller()))
    except Exception:
        return False


def upload_namespace(item_kind: str, item_id: str, version: str) -> str:
    return f"{item_kind}/{item_id}/{version}"


def check_upload(*, caller: str, item_kind: str, item_id: str, version: str) -> Dict:
    """Whether ``caller`` may upload this item's files; no side effects."""
    if not caller or caller == ANONYMOUS:
        return {"success": False, "error": "Sign in to upload"}
    if item_kind not in VALID_KINDS:
        return {"success": False, "error": f"item_kind must be one of {VALID_KINDS}"}
    item_id = (item_id or "").strip()
    segments = item_id.split("/")
    if item_kind == "ext" and len(segments) != 1:
        return {"success": False, "error": "extension_id must not contain '/'"}
    if len(segments) > 2 or not all(_is_id_segment(s) for s in segments):
        return {
            "success": False,
            "error": "id: letters, digits, '_', '-' and '.', starting with a letter or digit"
            + ("" if item_kind == "ext" else ", with at most one '/'"),
        }
    if not _is_semver((version or "").strip()):
        return {"success": False, "error": "version must be semver, e.g. 0.1.0"}

    if not _is_controller():
        if is_suspended(caller):
            return {"success": False, "error": "This account is suspended from publishing"}
        if not has_active_license(caller):
            return {
                "success": False,
                "error": "An active developer license is required to upload to the marketplace",
            }
        listing = _get_listing(item_kind, item_id)
        if listing is not None and str(listing.developer) != caller:
            return {"success": False, "error": f"'{item_id}' belongs to another developer"}

    registry_id = get_file_registry_canister_id()
    if not registry_id:
        return {"success": False, "error": "No file registry configured"}
    return {
        "success": True,
        "namespace": upload_namespace(item_kind, item_id, version.strip()),
        "file_registry_canister_id": registry_id,
    }


def prepare_upload(*, caller: str, item_kind: str, item_id: str, version: str) -> Async[Dict]:
    """Make ``caller`` a publisher of the item's version namespace."""
    check = check_upload(caller=caller, item_kind=item_kind, item_id=item_id, version=version)
    if not check["success"]:
        return check

    namespace, registry_id = check["namespace"], check["file_registry_canister_id"]
    registry = FileRegistryGrantService(Principal.from_str(registry_id))
    try:
        raw: CallResult = yield registry.grant_publish(
            json.dumps({"namespace": namespace, "principal": caller})
        )
        response = json.loads(_unwrap_call_result(raw))
    except Exception as e:
        logger.error(f"grant_publish {namespace} for {caller} failed: {e}")
        return {"success": False, "error": f"File registry call failed: {e}"}

    if not isinstance(response, dict) or response.get("error"):
        detail = response.get("error") if isinstance(response, dict) else "unexpected response"
        return {"success": False, "error": f"File registry refused the upload: {detail}"}

    logger.info(f"{caller} may upload {namespace} on {registry_id}")
    return check
