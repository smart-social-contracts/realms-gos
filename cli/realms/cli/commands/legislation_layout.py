"""The layout of a ``legislation/`` tree (docs/codices/layered-codex.md §3), the one place its rules live.

``realms legislation check`` runs ``layout_problems`` before it loads any chapter; realms-codices CI runs that
command and has no checker of its own. Each problem is ``<path>: <reason>``, the path relative to the tree.

The rules:

- a folder name is lowercase letters, digits and ``-``;
- a source is a folder with a ``source.json`` (§3.1), whose ``id`` is the folder's path under the tree;
- the only ``.py`` files in a source are ``<domain>.py``, ``<domain>`` a GGG domain;
- ``calendar.json`` (§3.4) and ``releases.json`` (§3.2) have the shapes the design shows;
- ``<domain>.cases.json`` (§12.3) tests the chapter ``<domain>.py`` beside it, and has the shape of
  ``legislation_cases``;
- a release lists all of its source's chapters and its calendar, never the ``realm`` domain (which only a realm's own
  layer fills, §3: a ``realm.py`` beside a ``releases.json`` is the realm's own and no release carries it), and every
  checksum is the sha256 of the file's bytes **at the release's own commit**, which is where a follower fetches them
  from (§7). The commit must be in the history of ``HEAD``: a commit on a side branch or a fork is not what the
  tree's history vouches for. The tree holds only the current files, so history is read with git; a chapter edited
  after its release is a proposal in progress, not a broken release.
"""

import json
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Callable, List, Optional

from realms.legislation.calendar import Calendar, parse_day
from realms.legislation.codex import source_id
from realms.legislation.errors import LegislationError
from realms.legislation.paths import REALM

# GGG's domain list: the ggg submodule's domains.json, which the canister carries in core/change_list.py
# (tests/ggg keeps those two equal, and cli/tests keeps this one equal to the canister's).
GGG_DOMAINS = (
    "realm", "tax", "procedure", "register", "local_regime", "water", "data_protection", "language",
    "treasury", "federation",
)  # fmt: skip
SOURCE_FILES = ("source.json", "releases.json", "calendar.json")
CASES_SUFFIX = ".cases.json"
CASE_KINDS = ("call", "guard", "forbid")
HEX = frozenset("0123456789abcdef")


def layout_problems(tree: Path) -> List[str]:
    """Every way ``tree`` breaks the layout, folder by folder, top to bottom."""
    problems = []
    for folder in [tree] + sorted(p for p in tree.rglob("*") if p.is_dir()):
        problems += folder_problems(tree, folder)
    return problems


def folder_problems(tree: Path, folder: Path) -> List[str]:
    problems = []
    if folder != tree:
        try:
            source_id(folder.name)
        except LegislationError:
            problems.append(
                f"{shown(tree, folder)}: folder names are lowercase letters, digits and -"
            )
    files = {f.name: f for f in folder.iterdir() if f.is_file()}
    if folder == tree or "source.json" not in files:
        return problems + [
            f"{shown(tree, files[name])}: not in a source: a source is a folder with a source.json"
            for name in sorted(files)
            if name.endswith((".py", CASES_SUFFIX)) or name in SOURCE_FILES
        ]
    source = folder.relative_to(tree).as_posix()
    problems += json_problems(
        tree, files["source.json"], lambda data: source_reasons(data, source)
    )
    problems += chapter_problems(tree, files)
    for name in sorted(files):
        if name.endswith(CASES_SUFFIX):
            problems += json_problems(tree, files[name], cases_reasons)
    if "calendar.json" in files:
        problems += json_problems(tree, files["calendar.json"], calendar_reasons)
    if "releases.json" in files:
        problems += json_problems(
            tree,
            files["releases.json"],
            lambda data: releases_reasons(data) or history_reasons(tree, source, data),
        )
    return problems


def chapter_problems(tree: Path, files: dict) -> List[str]:
    problems = [
        f"{shown(tree, files[name])}: {name[:-3]} is not a GGG domain ({', '.join(GGG_DOMAINS)})"
        for name in sorted(files)
        if name.endswith(".py") and name[:-3] not in GGG_DOMAINS
    ]
    return problems + [
        f"{shown(tree, files[name])}: cases for {name[: -len(CASES_SUFFIX)]}, but there is no {name[: -len(CASES_SUFFIX)]}.py beside them"
        for name in sorted(files)
        if name.endswith(CASES_SUFFIX)
        and name[: -len(CASES_SUFFIX)] + ".py" not in files
    ]


# ── The shape of each JSON file ──────────────────────────────────────────────────────────────────────────────────


def json_problems(tree: Path, file: Path, reasons_of: Callable) -> List[str]:
    """``reasons_of(data)`` for the JSON in ``file``, each as ``<path>: <reason>``."""
    try:
        data = json.loads(file.read_text())
    except ValueError as e:  # a bad JSON text and bytes that are not UTF-8 alike
        return [f"{shown(tree, file)}: not valid JSON: {e}"]
    return [f"{shown(tree, file)}: {reason}" for reason in reasons_of(data)]


def source_reasons(data, source: str) -> List[str]:
    """§3.1: ``id`` (the folder's path), ``name`` by language, ``created_by`` (left out when no act created the source)
    and ``publisher.realm``."""
    if not isinstance(data, dict):
        return ["an object with id, name and publisher"]
    reasons = []
    if data.get("id") != source:
        reasons.append(f"id is {data.get('id')!r}, but the folder is {source}")
    if not by_language(data.get("name")):
        reasons.append('name is the official names by language, {"es": "..."}')
    if "created_by" in data and not is_text(data["created_by"]):
        reasons.append(
            "created_by is the act that created the source; leave it out when none did"
        )
    publisher = data.get("publisher")
    if not (
        isinstance(publisher, dict)
        and "realm" in publisher
        and (publisher["realm"] is None or is_text(publisher["realm"]))
    ):
        reasons.append(
            'publisher is {"realm": <canister id>}, or {"realm": null} for a source with no realm of its own'
        )
    return reasons


def calendar_reasons(data) -> List[str]:
    """§3.4: what the library's ``Calendar`` takes, with the ``legislation`` it comes from and each holiday's name."""
    try:
        Calendar([data])
    except LegislationError as e:
        return [str(e)]
    reasons = []
    if not is_text(data.get("legislation")):
        reasons.append("legislation is the provision the dates come from")
    for entries in (data.get("years") or {}).values():
        reasons += [
            f"holiday {entry['date']} has no name by language"
            for entry in entries
            if not by_language(entry.get("name"))
        ]
    return reasons


def cases_reasons(data) -> List[str]:
    """§12.3, ``<domain>.cases.json``: a list of cases, each with a name, one kind of test and what it expects."""
    if not isinstance(data, list):
        return ["a list of cases"]
    reasons = []
    for case in data:
        label = f"case {case.get('name')!r}" if isinstance(case, dict) else "a case"
        reasons += [f"{label}: {r}" for r in case_reasons(case)]
    return reasons


def case_reasons(case) -> List[str]:
    if not isinstance(case, dict):
        return ["a case is an object"]
    reasons = []
    if not is_text(case.get("name")):
        reasons.append("name is text")
    if "realm" in case:
        try:
            source_id(case["realm"])
        except LegislationError:
            reasons.append("realm is a source id, such as eu/es/vc/46250-valencia")
    kinds = [k for k in CASE_KINDS if k in case]
    if len(kinds) != 1:
        return reasons + [f"one of {', '.join(CASE_KINDS)} says what is tested"]
    kind = kinds[0]
    if not is_text(case[kind]):
        reasons.append(f"{kind} is a name")
    expected = {"call": "returns", "guard": "denies", "forbid": "fires"}[kind]
    if expected not in case:
        reasons.append(f"{expected} is what the case expects")
    if kind == "call" and not isinstance(case.get("args", []), list):
        reasons.append("args is a list")
    if kind == "guard":
        if not isinstance(case.get("args", {}), dict):
            reasons.append("args is an object")
        if (
            not (case.get("denies") is None or is_text(case["denies"]))
            and "denies" in case
        ):
            reasons.append("denies is the reason, or null for a guard that allows")
        approvals = case.get("approvals", [])
        if not (isinstance(approvals, list) and all(is_text(a) for a in approvals)):
            reasons.append("approvals is a list of department names")
        if type(case.get("now", 0)) is not int:
            reasons.append("now is a time in seconds")
    if kind == "forbid":
        if not isinstance(case.get("facts"), dict):
            reasons.append("facts is an object")
        if "each" in case and not is_text(case["each"]):
            reasons.append("each names a list")
        fires = case.get("fires", [])
        if not (isinstance(fires, list) and all(is_text(f) for f in fires)):
            reasons.append("fires is a list of forbid ids")
    return reasons


def release_reasons(release) -> List[str]:
    """§3.2, one entry of ``releases.json``: its fields and their forms, not yet what the files say."""
    if not isinstance(release, dict):
        return ["a release is an object"]
    reasons = []
    if not is_text(release.get("version")):
        reasons.append("version is text, such as 2.4.0")
    reasons += [
        r
        for r in (
            day_reason(release, "published"),
            day_reason(release, "effective_from"),
        )
        if r
    ]
    commit = release.get("commit")
    if not (isinstance(commit, str) and len(commit) == 40 and set(commit) <= HEX):
        reasons.append("commit is the 40 hex digits of a git commit")
    chapters = release.get("chapters")
    if not isinstance(chapters, dict) or not chapters:
        reasons.append("chapters is {domain: sha256:<hex>}")
    else:
        for domain, checksum in chapters.items():
            if domain == REALM:
                reasons.append(
                    "chapters lists the realm domain, which only the realm's own layer fills"
                )
            elif domain not in GGG_DOMAINS:
                reasons.append(f"chapters lists {domain}, which is not a GGG domain")
            if not is_checksum(checksum):
                reasons.append(f"chapters.{domain} is sha256:<64 hex digits>")
    if release.get("calendar") is not None and not is_checksum(release["calendar"]):
        reasons.append("calendar is sha256:<64 hex digits>, or null")
    if "notes" in release and not by_language(release["notes"]):
        reasons.append("notes are text by language")
    return reasons


def releases_reasons(data) -> List[str]:
    """§3.2, ``releases.json``: a list of releases, newest last, each version once."""
    if not isinstance(data, list) or not data:
        return ["a list of releases, newest last"]
    reasons = []
    for release in data:
        label = (
            f"release {release.get('version')}"
            if isinstance(release, dict)
            else "a release"
        )
        reasons += [f"{label}: {r}" for r in release_reasons(release)]
    if reasons:
        return reasons
    versions = [r["version"] for r in data]
    if len(set(versions)) != len(versions):
        reasons.append("a version is released twice")
    published = [parse_day(r["published"]) for r in data]
    if published != sorted(published):
        reasons.append("releases are newest last: published goes up")
    return reasons


def day_reason(release: dict, field: str) -> Optional[str]:
    try:
        parse_day(release.get(field))
    except LegislationError:
        return f"{field} is a day, YYYY-MM-DD"
    return None


def is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def by_language(value) -> bool:
    """A non-empty object of text by language."""
    return (
        isinstance(value, dict)
        and bool(value)
        and all(is_text(k) and is_text(v) for k, v in value.items())
    )


def is_checksum(value) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and set(value[7:]) <= HEX
    )


# ── The releases against the files they name ─────────────────────────────────────────────────────────────────────


def history_reasons(tree: Path, source: str, releases: list) -> List[str]:
    """Each release against the source's files at its commit (``releases`` has the shape ``releases_reasons`` wants)."""
    return [
        f"release {release['version']}: {reason}"
        for release in releases
        for reason in release_history_reasons(tree, source, release)
    ]


def release_history_reasons(tree: Path, source: str, release: dict) -> List[str]:
    """What the source's folder held at the release's commit against the release: every chapter and the calendar
    listed, and each checksum the sha256 of that file's bytes."""
    commit, short = release["commit"], release["commit"][:7]
    try:
        if not is_ancestor(tree, commit):
            return [f"commit {short} is not in the history of HEAD"]
        names = names_at(tree, commit, source)
    except LegislationError as e:
        return [f"commit {short} cannot be read ({e}); check out the full history"]
    held = {n[:-3] for n in names if n.endswith(".py")} - {REALM}
    listed, calendar = release["chapters"], release.get("calendar")
    reasons = [
        f"does not list {d}, but {source}/{d}.py is in commit {short}: a release lists all of the source's chapters"
        for d in sorted(held - set(listed))
    ]
    reasons += [
        f"lists {d}, but {source}/{d}.py is not in commit {short}"
        for d in sorted(set(listed) - held)
    ]
    wanted = {f"{d}.py": listed[d] for d in sorted(held & set(listed))}
    if ("calendar.json" in names) != (calendar is not None):
        reasons.append(
            f"lists a calendar, but {source}/calendar.json is not in commit {short}"
            if calendar is not None
            else f"does not list the calendar, but {source}/calendar.json is in commit {short}"
        )
    elif calendar is not None:
        wanted["calendar.json"] = calendar
    for name, checksum in wanted.items():
        found = "sha256:" + sha256(bytes_at(tree, commit, source, name)).hexdigest()
        if found != checksum:
            reasons.append(
                f"{source}/{name} at commit {short} is {found}, not the {checksum} the release lists"
            )
    return reasons


def names_at(tree: Path, commit: str, source: str) -> List[str]:
    """The file names directly in ``source``'s folder at ``commit``."""
    out = git(tree, "ls-tree", "--name-only", commit, f"./{source}/")
    return [line.rsplit("/", 1)[-1] for line in out.decode().splitlines()]


def bytes_at(tree: Path, commit: str, source: str, name: str) -> bytes:
    return git(tree, "show", f"{commit}:./{source}/{name}")


def is_ancestor(tree: Path, commit: str) -> bool:
    """Whether ``commit`` is ``HEAD`` or in its history. A commit git does not have is a ``LegislationError``."""
    done = run_git(tree, "merge-base", "--is-ancestor", commit, "HEAD")
    if done.returncode > 1:  # 1 is "not an ancestor"; anything else is git failing
        raise LegislationError(refusal(done))
    return done.returncode == 0


def git(tree: Path, *args: str) -> bytes:
    """git's output, run from ``tree`` so that ``./`` paths are the tree's. A refusal is a ``LegislationError``."""
    done = run_git(tree, *args)
    if done.returncode:
        raise LegislationError(refusal(done))
    return done.stdout


def run_git(tree: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(tree), *args], capture_output=True)
    except FileNotFoundError:
        raise LegislationError("git is not installed")


def refusal(done: subprocess.CompletedProcess) -> str:
    """The last line git said on stderr."""
    return (
        done.stderr.decode().strip().splitlines()[-1] if done.stderr else "git failed"
    )


def shown(tree: Path, path: Path) -> str:
    return path.relative_to(tree).as_posix()
