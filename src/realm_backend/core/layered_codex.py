"""The rules of a layered codex's storage (docs/codices/layered-codex.md §9.1): its id, the texts of its chapters,
and the one install job at a time. The install task (§9.2) writes through these."""

import json

from core import chain
from core import sandbox
from core.db import Conflict, Fail, first, next_id
from ggg import ChapterSource, InstallJob, Realm

MAX_CHAPTER = 256 * 1024  # bytes (§7)


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


def live_job():
    """The install job that is running or paused, if there is one."""
    return first(InstallJob, "status", "running") or first(InstallJob, "status", "paused")


def start_job(proposal, change_list):
    """Begin the install of ``change_list`` (``{url, checksum}``) for ``proposal``. Refused while another job is
    running or paused (a paused one waits for an admin, a staged one for its switch time, §9.2)."""
    if live_job() is not None:
        raise Conflict("an install is running")
    return InstallJob(id=next_id(InstallJob, "J"), proposal=proposal, change_list=change_list, started_at=chain.now())
