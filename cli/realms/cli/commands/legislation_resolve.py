"""What ``realms legislation resolve`` does (docs/codices/layered-codex.md §8, §12.4): a realm's layer paths, its
flattened view and its Cedar, worked out by the library the canister runs (``plan`` and ``resolve``).

The sync sources' records (§8) come from files, or from the tree's own ``releases.json``. The chapters' bytes come from
the realms-codices checkout, read with git: a relayed layer at the commit its release names, the realm's own layer at
the commit the tool is given. Nothing is read from the working copy, so a result names the commits it was made from.
Nothing here touches a network.
"""

import json
from hashlib import sha256
from pathlib import Path
from typing import Dict, List, Optional

from realms.cli.commands.legislation_canonical import codex_id
from realms.cli.commands.legislation_layout import bytes_at, names_at
from realms.legislation import LegislationError, plan, resolve
from realms.legislation.paths import ARROW


def checksum(data: bytes) -> str:
    return "sha256:" + sha256(data).hexdigest()


def load_json(text: str, where: str):
    try:
        return json.loads(text)
    except ValueError as e:
        raise LegislationError(f"{where} is not valid JSON: {e}")


def tree_record(tree: Path, commit: str, source: str) -> dict:
    """What ``source`` relays if the tree's ``releases.json`` are its record: the newest release of each source on
    its path that has one, top to bottom, ``source`` last."""
    parts = source.split("/")
    layers = []
    for i in range(len(parts)):
        layer = "/".join(parts[: i + 1])
        if "releases.json" in names_at(tree, commit, layer):
            releases = load_json(
                bytes_at(tree, commit, layer, "releases.json").decode(),
                f"{layer}/releases.json",
            )
            layers.append({"source": layer, **releases[-1]})
    if not layers:
        raise LegislationError(
            f"{source} and the sources above it have no releases.json at commit {commit[:7]}"
        )
    return {"source": source, "layers": layers}


def file_record(path: Path) -> dict:
    """A sync source's record in a file, in the shape of §8: ``{"source": ..., "layers": [...]}``."""
    return load_json(path.read_text(), str(path))


def layer_at(tree: Path, commit: str, source: str) -> dict:
    """``source``'s own layer as it stands at ``commit``, in the shape ``plan`` takes (§8): the checksum of each of
    its chapters and of its calendar."""
    names = names_at(tree, commit, source)
    return {
        "chapters": {
            name[:-3]: checksum(bytes_at(tree, commit, source, name))
            for name in sorted(names)
            if name.endswith(".py")
        },
        "calendar": (
            checksum(bytes_at(tree, commit, source, "calendar.json"))
            if "calendar.json" in names
            else None
        ),
    }


def release_commits(relays: List[dict]) -> Dict[str, str]:
    """The commit each relayed release names, by source: where its files are read from."""
    return {
        layer["source"]: layer["commit"]
        for relay in relays
        for layer in relay["layers"]
        if layer.get("commit")
    }


def file_at(tree: Path, commit: str, source: str, name: str, expected: str) -> bytes:
    """``source``'s file ``name`` at ``commit``, if it is the bytes ``expected`` names."""
    data = bytes_at(tree, commit, source, name)
    if checksum(data) != expected:
        raise LegislationError(
            f"{source}/{name} at commit {commit[:7]} is {checksum(data)}, not the {expected} the record lists"
        )
    return data


def resolve_realm(
    tree: Path,
    realm: str,
    commit: str,
    relays: List[dict],
    own: Optional[dict],
    operations,
    modules,
    facts: Optional[dict],
):
    """The ``Resolution`` of ``realm`` that syncs from ``relays``. Its own layer is ``own`` (§8), or its folder at
    ``commit``; the bytes of every chapter are read from the tree and checked against the checksums.
    """
    if own is None:
        own = layer_at(tree, commit, realm)
    planned = plan(realm, relays, own)
    commits = release_commits(relays)
    chapters, calendars = {}, {}
    for entry in planned.chapters:
        at = commits.get(entry["source"], commit)
        data = file_at(
            tree, at, entry["source"], entry["domain"] + ".py", entry["sha256"]
        )
        chapters[entry["sha256"]] = data.decode()
    for entry in planned.calendars:
        at = commits.get(entry["source"], commit)
        data = file_at(tree, at, entry["source"], "calendar.json", entry["sha256"])
        calendars[entry["sha256"]] = load_json(
            data.decode(), f"{entry['source']}/calendar.json"
        )
    return resolve(
        planned,
        chapters,
        calendars,
        operations=operations,
        modules=modules,
        facts=facts,
    )


def report_lines(resolution, with_cedar: bool) -> List[str]:
    """What the command prints: the codex id, the layer paths, the flattened view of each domain (§5.9) and the
    generated Cedar (§5.8)."""
    planned = resolution.plan
    lines = [
        f"codex {codex_id(planned)}",
        f"layers {ARROW.join(planned.layers)}",
        "layer paths:",
    ]
    lines += [
        f"  {domain}: {ARROW.join(path)}" for domain, path in planned.paths.items()
    ]
    lines += [
        f"skipped {s['source']}/{s['domain']}: only a realm's own layer fills the realm domain"
        for s in planned.skipped
    ]
    for domain, view in resolution.views().items():
        lines += [
            "",
            f"{domain}, as it applies in {planned.realm}, path {ARROW.join(view['path'])}:",
        ]
        lines += view_lines(view["members"])
    lines += ["", "Cedar:"]
    lines.append(
        resolution.codex.cedar()
        if with_cedar
        else "not generated: --facts FILE gives GGG's fact schema"
    )
    return lines


def view_lines(members: List[dict]) -> List[str]:
    """One line per member: its name, what it is (and the value it resolves to, for a setting), the layers that
    define it and the provision they cite."""
    rows = [
        (
            m["name"],
            what(m),
            ARROW.join(layer["source"] for layer in m["layers"]),
            cited(m),
        )
        for m in members
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(3)] if rows else []
    return [
        (
            "  "
            + "  ".join(cell.ljust(w) for cell, w in zip(row, widths))
            + "  "
            + row[3]
        ).rstrip()
        for row in rows
    ]


def what(member: dict) -> str:
    text = f"{member['kind']} {member['mark']}" if member["mark"] else member["kind"]
    if member["kind"] == "setting":
        text += " = " + json.dumps(member["value"], sort_keys=True)
    return text


def cited(member: dict) -> str:
    """The provision of the lowest layer that cites one."""
    for layer in reversed(member["layers"]):
        if layer["legislation"]:
            return layer["legislation"]
    return ""
