"""The rules of a layered codex's storage (docs/codices/layered-codex.md §9.1): its id, the texts of its chapters,
and the one install job at a time. The install task (§9.2) writes through these, and fetches a text by HTTPS with
``fetched`` and ``decoded``."""

import json

from core import chain
from core import sandbox
from core import surrogates
from core.db import Conflict, Fail, first, next_id
from ggg import ChapterSource, InstallJob, Realm

MAX_CHAPTER = 256 * 1024  # bytes (§7)
# The replica counts a reply's headers and its framing against max_response_bytes, not only its body: room for them,
# so that a chapter of exactly 256 KB arrives. The body itself is held to the limit (``decoded``).
HEADROOM = 8 * 1024


def canonical(value):
    """Sorted keys, no spaces, UTF-8 (§9.1)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def checksum(text):
    """``sha256:`` and the hex digest of the text's UTF-8 bytes."""
    return "sha256:" + sandbox.sha256(text)


def codex_id(layers, layer_paths, chapters):
    """The id of the codex with these layers, layer paths and ``chapters`` (``{chapter id: {sha256, ...}}``): the
    checksum of the legislation it runs, which a change list's ``replaces`` names. Where and when the texts were
    fetched, and who vouched for them, are this realm's own history and stay out of it: a capital and its
    quarters, or two realms, that run the same legislation have the same id."""
    sums = {chapter: entry["sha256"] for chapter, entry in chapters.items()}
    return checksum(canonical({"layers": layers, "layer_paths": layer_paths, "chapters": sums}))


def store_chapter(sha256, text):
    """Keep ``text`` under ``sha256``, once. Refused: a text above 256 KB, and a text that is not the one ``sha256``
    names."""
    if len(text.encode()) > MAX_CHAPTER:
        raise Fail(f"{sha256} is above {MAX_CHAPTER // 1024} KB")
    found = checksum(text)
    if found != sha256:
        raise Fail(f"the text is {found}, not {sha256}")
    return ChapterSource[sha256] or ChapterSource(sha256=sha256, source=text)


def own_source():
    """The realm's own source id (§7): set by the first layered codex that becomes active, from its own layer (the
    change list's ``realm``, which the vote approved), and fixed afterwards; None before that."""
    return ((Realm["1"].manifest_data or {}).get("legislation") or {}).get("source")


def remember_source(realm):
    row = Realm["1"]
    data = row.manifest_data or {}
    if (data.get("legislation") or {}).get("source") != realm:
        data["legislation"] = {**(data.get("legislation") or {}), "source": realm}
        row.manifest_data = data


def in_force_since():
    """When the codex in force took effect, seconds: its switch time T, or the revert that restored it. None before the
    realm recorded it."""
    return ((Realm["1"].manifest_data or {}).get("legislation") or {}).get("since")


def remember_in_force(at):
    row = Realm["1"]
    data = row.manifest_data or {}
    data["legislation"] = {**(data.get("legislation") or {}), "since": at}
    row.manifest_data = data


def live_job():
    """The install job that is running or paused, if there is one."""
    return first(InstallJob, "status", "running") or first(InstallJob, "status", "paused")


def start_job(proposal, change_list):
    """Begin the install of ``change_list`` (``{url, checksum}``) for ``proposal``. Refused while another job is
    running or paused (a paused one waits for an admin, a staged one for its switch time, §9.2)."""
    if live_job() is not None:
        raise Conflict("an install is running")
    return InstallJob(id=next_id(InstallJob, "J"), proposal=proposal, change_list=change_list, started_at=chain.now())


def fetched(url, most):
    """``(status, body)`` of a GET of ``url`` by HTTPS outcall, for a body of at most ``most`` bytes; a call that fails
    is a Fail naming the URL."""
    try:
        return (yield from chain.http_get(url, most + HEADROOM))
    except Exception as e:  # a failed call is a plain Exception, as chain.call raises it
        raise Fail(f"{url}: {e}")


def decoded(url, status, body, most, is_json=False):
    """The text of a reply: a 200 of at most ``most`` bytes of UTF-8. A body that is JSON (``is_json``) must hold no lone
    surrogate once parsed; code (a chapter) is no JSON, and a ``"\\ud800"`` in it is an escape of Python's."""
    if status != 200:
        raise Fail(f"{url}: HTTP {status}")
    if len(body) > most:
        raise Fail(f"{url}: {len(body)} bytes is above {most // 1024} KB")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise Fail(f"{url}: not UTF-8")
    if is_json and surrogates.json_holds(text):
        raise Fail(f"{url}: holds a lone surrogate (U+D800 to U+DFFF), which is not text")
    return text
