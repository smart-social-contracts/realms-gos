#!/usr/bin/env python3
"""Generate src/realm_backend/zonedata, the IANA time zones a realm release bundles (realms-gos#511).

A realm keeps the zone file of its own ``Realm.timezone`` and nothing else, but any realm may set any zone, so every
release carries the tz database: one module per distinct zone file, loaded only when a realm uses that zone, with the
file as hex text that ``bytes.fromhex`` decodes into raw bytes (about 60k instructions per 900 B; ``base64`` costs 17M for
a few KB). Names that share a zone file (the tz database's links) share a module, named after the zone they link to.

The data does not depend on the machine that builds it. The script downloads the pinned release of the tz database from
data.iana.org (``tzcode`` and ``tzdata``, in a temporary directory), checks their sha512 against the pin in
``tzdata-pin.json``, and builds its own ``zic`` from the tzcode: a distribution's ``zic -b slim`` has dropped the 2073 to
2086 transitions of Asia/Gaza and Asia/Hebron, and its tzdata.zi differs from IANA's (backzone). It needs ``make`` and a
C compiler. Every zone file is then read back with the realm's own TZif reader (``core.tzif``) and compared with the
"fat" file zic makes of the same zone, at each of its transitions and the second on either side.

    python3 scripts/build_zones.py                       # rewrite the package from the pinned release
    python3 scripts/build_zones.py --check               # fail if the package is not what the pinned release gives
    python3 scripts/build_zones.py --release 2027a       # pin another release, then rewrite the package

Run it with ``--release`` before every release of the realm, when IANA has published a new tz database: the release
workflow fails otherwise. ``--release`` pins the sha512 of what it downloaded; compare it with IANA's signature of the
same files (``.asc``) before committing.

The names are those GGG accepts for ``Realm.timezone`` (ggg I-ADM-4): ``Area/Location``, with ``Area`` one of the
tz database's. ``UTC`` needs no data. The zone files are compiled with ``zic -b slim -r @0``: the transitions up to the
last change of rule, and the POSIX TZ string at the end of the file for the years after, and nothing before 1970 (a
third of the data). Names only grow: a release that would drop one the package has is refused.
"""

import argparse
import ast
import hashlib
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "realm_backend" / "zonedata"
PIN = pathlib.Path(__file__).with_name("tzdata-pin.json")
URL = "https://data.iana.org/time-zones/releases/{kind}{release}.tar.gz"
KINDS = ("tzcode", "tzdata")
AREAS = ("Africa", "America", "Antarctica", "Arctic", "Asia", "Atlantic", "Australia", "Europe", "Indian", "Pacific", "Etc")
SINCE = "@0"  # zic -r: the data starts at 1970-01-01 UTC
sys.path.insert(0, str(ROOT / "src" / "realm_backend"))

from core import tzif  # noqa: E402


# ---- The pinned tz database


def download(release, kind, directory):
    """The file of ``kind`` (tzcode or tzdata) of ``release``, and its sha512."""
    path = directory / f"{kind}{release}.tar.gz"
    with urllib.request.urlopen(URL.format(kind=kind, release=release), timeout=120) as reply:
        path.write_bytes(reply.read())
    return path, hashlib.sha512(path.read_bytes()).hexdigest()


def unpack(archive, destination):
    """Extract ``archive`` into ``destination``: plain files and folders, and nothing outside it."""
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            inside = (destination / member.name).resolve().is_relative_to(destination.resolve())
            if not (member.isfile() or member.isdir()) or not inside:
                sys.exit(f"{archive.name} holds an unexpected entry, {member.name}")
        tar.extractall(destination, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))


def build_zic(release, sha512, directory):
    """``(zic, tzdata.zi)``: the zic built from the tzcode of ``release`` and the compact form of its data, once the
    sha512 of both downloads is what ``sha512`` (the pin) says."""
    source = directory / "tz"
    source.mkdir()
    for kind in KINDS:
        archive, found = download(release, kind, directory)
        if found != sha512[kind]:
            sys.exit(f"{archive.name} is not the file pinned in {PIN.name}: sha512 {found}")
        unpack(archive, source)
    subprocess.run(["make", "-C", str(source), "zic", "tzdata.zi"], check=True, stdout=subprocess.DEVNULL)
    return source / "zic", source / "tzdata.zi"


# ---- The zone files


def names_of(tzdata_zi):
    """``(version, zones, names)``: the tz database's release, its zones, and every zone or link name GGG accepts."""
    lines = pathlib.Path(tzdata_zi).read_text().splitlines()
    version = lines[0].removeprefix("# version ")
    zones = {line.split()[1] for line in lines if line.startswith("Z ")}
    links = {line.split()[2] for line in lines if line.startswith("L ")}
    return version, zones, sorted(n for n in zones | links if n.partition("/")[0] in AREAS and "/" in n)


def compile_zones(zic, tzdata_zi, names, form, directory):
    """The zone file of each name, compiled by ``zic`` in ``form`` (slim or fat) from 1970."""
    subprocess.run([str(zic), "-b", form, "-r", SINCE, "-d", str(directory), str(tzdata_zi)], check=True)
    return {name: (directory / name).read_bytes() for name in names}


def verify(slim, fat):
    """Read every slim zone file with the realm's TZif reader and compare it with the fat file of the same zone, at each of
    its transitions and the second before and after. A fat file lists its transitions up to 2037; the slim file gets
    the later years from the POSIX TZ string, which the reader works out. Returns the names that differ."""
    wrong = []
    for name, data in slim.items():
        try:
            small, large = tzif.parse(data), tzif.parse(fat[name])
            instants = [t + d for t in large.times for d in (-1, 0, 1)] + [0, 1_000_000_000, 2_000_000_000]
            if any(small.offset(t) != large.offset(t) for t in instants):
                wrong.append(name)
        except Exception:  # a file the reader cannot read is as wrong as one it reads differently
            wrong.append(name)
    return wrong


def module_name(name):
    """A module name for a zone name; ``+`` is the only character that would make two names collide."""
    return name.lower().replace("/", "_").replace("-", "_").replace("+", "_plus_")


def committed_names():
    """The zone names the package has now."""
    init = PACKAGE / "__init__.py"
    if not init.exists():
        return set()
    for node in ast.parse(init.read_text()).body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "MODULE":
            return set(ast.literal_eval(node.value))
    return set()


def generate(zic, tzdata_zi):
    """``(version, {file name: text})``: the package for the tz database at ``tzdata_zi``, which ``zic`` compiles."""
    version, zones, names = names_of(tzdata_zi)
    gone = sorted(committed_names() - set(names))
    if gone:
        sys.exit(f"release {version} would drop {', '.join(gone)}: a name a realm may have set cannot go (post_upgrade "
                 f"would trap), so keep it as a link in the generator")
    with tempfile.TemporaryDirectory() as slim_dir, tempfile.TemporaryDirectory() as fat_dir:
        slim = compile_zones(zic, tzdata_zi, names, "slim", pathlib.Path(slim_dir))
        wrong = verify(slim, compile_zones(zic, tzdata_zi, names, "fat", pathlib.Path(fat_dir)))
    if wrong:
        sys.exit(f"the realm's reader and zic disagree about {', '.join(wrong)}")
    sharing = {}
    for name, data in slim.items():
        sharing.setdefault(data, []).append(name)
    modules, where = {}, {}  # the module of each distinct zone file, named after the zone the other names link to
    for data, group in sharing.items():
        zone = min(group, key=lambda n: (n not in zones, n))
        modules[module_name(zone)] = (zone, data)
        where.update({name: module_name(zone) for name in group})
    assert len(modules) == len(sharing), "two zones would share a module name"
    files = {f"{module}.py": f'"""{zone}"""\nDATA = bytes.fromhex("{data.hex()}")\n'
             for module, (zone, data) in sorted(modules.items())}
    files["__init__.py"] = (
        f'"""The IANA time zones of release {version}, generated by scripts/build_zones.py: do not edit.\n\n'
        f"``MODULE`` maps each zone name to the module that holds its zone file in ``DATA``; core.zones reads one on\n"
        f'demand. ``_bundle`` makes the bundler ship all of them."""\n\n'
        f'VERSION = "{version}"\nMODULE = {{\n' + "".join(f'    "{n}": "{m}",\n' for n, m in sorted(where.items())) + "}\n")
    files["_bundle.py"] = (
        '"""Imports every zone module, so the bundler ships them. Nothing runs this: a module loads when it is read."""\n\n'
        + "".join(f"import zonedata.{m}  # noqa: F401\n" for m in sorted(set(where.values()))))
    return version, files


# ---- The command


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if the package is out of date")
    parser.add_argument("--release", metavar="VERSION", help="pin this release of the tz database, then rewrite the package")
    args = parser.parse_args()
    pin = json.loads(PIN.read_text())
    with tempfile.TemporaryDirectory() as temporary:
        directory = pathlib.Path(temporary)
        if args.release:
            pin = {"release": args.release, "sha512": {kind: download(args.release, kind, directory)[1] for kind in KINDS}}
            PIN.write_text(json.dumps(pin, indent=2) + "\n")
            print(f"pinned {args.release}; compare the sha512 in {PIN.name} with IANA's signatures before committing")
        zic, tzdata_zi = build_zic(pin["release"], pin["sha512"], directory)
        version, files = generate(zic, tzdata_zi)
    if version != pin["release"]:
        sys.exit(f"the tz database says it is release {version}, the pin says {pin['release']}")
    current = {p.name: p.read_text() for p in PACKAGE.glob("*.py")} if PACKAGE.exists() else {}
    if args.check:
        stale = sorted(n for n in files.keys() | current.keys() if files.get(n) != current.get(n))
        sys.exit(f"out of date: {', '.join(stale[:10])}{' ...' if len(stale) > 10 else ''}" if stale else 0)
    PACKAGE.mkdir(exist_ok=True)
    for name in current.keys() - files.keys():
        (PACKAGE / name).unlink()
    for name, text in files.items():
        (PACKAGE / name).write_text(text)
    print(f"wrote {len(files)} files to {PACKAGE.relative_to(ROOT)}: {len(files) - 2} zone files for release {version}")


if __name__ == "__main__":
    main()
