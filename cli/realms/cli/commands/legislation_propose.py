"""What ``realms legislation propose`` does (docs/codices/layered-codex.md §7, §12.5): the change list a proposal
points to, written as canonical JSON, and the ``Code { url; checksum }`` to submit.

A chapter's checksum and size are read from the realms-codices checkout with git, at the commit the URL names, so the
bytes the realm fetches are the bytes that were hashed. The limits are the realm's (§7); a change list the realm would
refuse is refused here. The only call to a network is ``active_codex_id``, and only when the caller gives a network.
"""

from hashlib import sha256
from pathlib import Path
from typing import Dict, List, Optional

import typer

from realms.cli.commands.legislation_canonical import canonical
from realms.cli.commands.legislation_layout import GGG_DOMAINS, bytes_at, git
from realms.cli.realm_api import call_op
from realms.legislation import LegislationError
from realms.legislation.codex import source_id

KIND, FORMAT = "realms-gos/legislation-change", 1
CALENDAR = "calendar"  # a layer's calendar.json is set under <source>/calendar
MAX_CHAPTERS = 50
MAX_CHAPTER = 256 * 1024  # bytes
MAX_CHANGE_LIST = 64 * 1024  # bytes
RAW_URL = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"


def replaced_codex(
    replaces: Optional[str],
    network: Optional[str],
    canister: Optional[str],
    identity: Optional[str],
) -> Optional[str]:
    """The ``replaces`` of the change list: the id given (``none`` for a realm with no active codex), or the realm's
    active codex read from the realm. A realm is read only when the caller names its network, its canister and the
    identity that reads: never a default network, and never the identity the tool happens to find.
    """
    reading = (network, canister, identity)
    if replaces is not None:
        if any(reading):
            raise LegislationError(
                "give --replaces, or --network, --canister and --identity to read it from the realm, not both"
            )
        return None if replaces == "none" else replaces
    if network and canister and not identity:
        raise LegislationError(
            "--identity is required with --network and --canister: the read is made as the identity you name"
        )
    if not all(reading):
        raise LegislationError(
            "say which codex this replaces: --replaces ID (or none), "
            "or --network, --canister and --identity to read it from the realm"
        )
    return active_codex_id(canister, network, identity)


def active_codex_id(canister: str, network: str, identity: str) -> Optional[str]:
    """The id of the realm's active codex as ``codex_status`` shows it, or None when it has none. Says where it reads
    before it does."""
    typer.echo(f"reading codex_status of {canister} on {network} as {identity}")
    reply = call_op(
        canister,
        "codex_status",
        {"count": 1},
        network=network,
        identity=identity,
        read=True,
    )
    if not reply.get("ok"):
        raise LegislationError(f"{canister} refused codex_status: {reply.get('error')}")
    return (reply.get("codex") or {}).get("id")


def own_chapter_file(chapter: str, realm: str) -> str:
    """The file name of chapter ``<source>/<domain>`` of the realm's own layer. A layer from above is installed whole,
    as a release, never chapter by chapter (§7)."""
    source, _, domain = chapter.rpartition("/")
    source_id(source)
    if domain != CALENDAR and domain not in GGG_DOMAINS:
        raise LegislationError(
            f"{chapter}: {domain} is not a GGG domain ({', '.join(GGG_DOMAINS)}), nor {CALENDAR}"
        )
    if source != realm:
        raise LegislationError(
            f"{chapter}: only the realm's own layer, {realm}, is set or removed chapter by chapter; "
            "a layer from above is installed whole, as a release"
        )
    return "calendar.json" if domain == CALENDAR else domain + ".py"


def chapter_entry(tree: Path, commit: str, repo: str, realm: str, chapter: str) -> dict:
    """The entry of ``set`` for ``chapter``: where the bytes are at ``commit``, and their checksum and size."""
    name = own_chapter_file(chapter, realm)
    data = bytes_at(tree, commit, realm, name)
    if not 0 < len(data) <= MAX_CHAPTER:
        raise LegislationError(
            f"{chapter} is {len(data)} bytes: a chapter is between 1 and {MAX_CHAPTER} bytes "
            "(to empty a chapter, remove it)"
        )
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise LegislationError(
            f"{chapter} is not UTF-8: a realm reads a chapter as text"
        )
    prefix = git(tree, "rev-parse", "--show-prefix").decode().strip()
    return {
        "chapter": chapter,
        "url": RAW_URL.format(repo=repo, commit=commit, path=f"{prefix}{realm}/{name}"),
        "mirrors": [],
        "sha256": sha256(data).hexdigest(),
        "size": len(data),
    }


def report_entry(path: Path, url: str) -> dict:
    """The ``report`` of the change list: where the analysis report is served and the checksum of its bytes."""
    return {"url": https(url), "sha256": sha256(path.read_bytes()).hexdigest()}


def parse_summary(items: List[str]) -> Dict[str, str]:
    """``LANG=TEXT`` for each language the summary is written in."""
    summary = {}
    for item in items:
        language, equals, text = item.partition("=")
        if not (equals and language.strip() and text.strip()):
            raise LegislationError(f"a summary is LANG=TEXT, not {item!r}")
        summary[language.strip()] = text.strip()
    return summary


def https(url: str) -> str:
    if not url.startswith("https://"):
        raise LegislationError(f"{url!r}: a realm fetches over https")
    return url


def change_list(
    realm: str,
    replaces: Optional[str],
    entries: List[dict],
    remove: List[str],
    report: Optional[dict],
    summary: Dict[str, str],
) -> bytes:
    """The change list as canonical JSON. ``entries`` are the chapters to set, ``remove`` the ids of those to empty."""
    source_id(realm)
    for chapter in remove:
        own_chapter_file(chapter, realm)
    names = [e["chapter"] for e in entries] + remove
    if not names:
        raise LegislationError(
            "the change list changes nothing: set or remove a chapter"
        )
    if len(set(names)) != len(names):
        raise LegislationError("a chapter is named twice")
    if len(entries) > MAX_CHAPTERS:
        raise LegislationError(
            f"a change list sets at most {MAX_CHAPTERS} chapters, not {len(entries)}"
        )
    body = {
        "kind": KIND,
        "format": FORMAT,
        "realm": realm,
        "replaces": replaces,
        "set": entries,
        "remove": remove,
        "release": None,
        "summary": summary,
    }
    if report:
        body["report"] = report
    data = canonical(body)
    if len(data) > MAX_CHANGE_LIST:
        raise LegislationError(
            f"the change list is {len(data)} bytes: at most {MAX_CHANGE_LIST}"
        )
    return data


def code_to_submit(url: str, data: bytes) -> str:
    """The action the proposal carries (§7): the change list's address and the checksum of its exact bytes."""
    return f'Code {{ url = "{url}"; checksum = "sha256:{sha256(data).hexdigest()}" }}'
