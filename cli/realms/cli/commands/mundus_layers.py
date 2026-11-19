"""Legislation in layers across the realms of a mundus, and a capital's quarters.

The flow of ``tests/deployed/test_valencia.py`` (docs/codices/layered-codex.md §3.3, §7, §8), on a mundus' live
network instead of a test's PocketIC. A realm of the mundus file with a ``layer`` (its source id in realms-codices'
``legislation/`` tree) and ``follows`` (the realms it syncs from) is set up after the realms it follows:

1. **Links.** It votes to sync from each realm it follows, and each of them votes to accept it.
2. **Layers from above,** top to bottom, at the versions its sync sources' records list: the first by a change list
   with the chapters' URLs (a realm's first layered codex names its own layer, so it needs one); the others by
   "Propose install", whose chapters come from the sync source's capital by checksum.
3. **Its own layer,** by a change list of its chapters: those of the ``release`` the mundus file names, else of the
   newest release, else the checkout's.
4. **Publication.** A source with releases publishes that release, so that its followers can take it.

All of it happens with the network held (``mundus_held``): the mundus serves the change lists and answers the
outcalls for chapters. Each step is a call of the founder's: while the root department decides alone (a realm's
until its codex or its members give it a policy), a governed call runs at once, with no proposal. So an own layer is
installed at once too: the legislation that makes an ordinance wait (Spain's local regime asks for 15 working days
after the vote) judges proposals, and there is none. Where the root department does not decide alone, the call is
filed as a proposal and the founder votes; one that needs other votes is left open and the mundus says so.

A realm with ``quarters`` asks Casals for that many quarters first (``quarter_add``), with the network live, since
Casals builds them on its own. First, because a capital gives its quarters a codex when it installs one: a quarter
added later gets none until the next install. A federation installs every layer by a change list.

Later, ``publish`` has a source adopt and publish another of its releases, and ``install`` has a realm take what its
sync sources relay and it does not run yet. A realm that is no federation does that by "Propose install", which its
members can file themselves (geister's swarm does). A federation cannot: its quarters stage only what a change list
names, so ``install`` writes one, and its founder files it and votes, with the network held.
"""

import calendar
import hashlib
import json
import subprocess
import time
from pathlib import Path

from . import mundus_realms as realms
from .mundus_held import Network

RAW = "https://raw.githubusercontent.com/smart-social-contracts/realms-codices"
SERVED = "https://mundus.localhost"  # what only the mundus answers, while the network is held
ACCEPTED = ("executed", "execution_scheduled")
QUARTER_WAIT = 1800  # seconds Casals may take to build the quarters of a realm


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- the realms-codices tree, as the checkout has it


class Tree:
    """The ``legislation/`` tree of the realms-codices checkout that realms-gos pins (``codices``)."""

    def __init__(self, codices: Path):
        self.root = codices

    def git(self, *args) -> bytes:
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True).stdout

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").decode().strip()

    def releases(self, source: str) -> list:
        path = self.root / "legislation" / source / "releases.json"
        return json.loads(path.read_text()) if path.exists() else []

    def release(self, source: str, version: str = None) -> dict:
        """The entry of ``source``'s releases.json for ``version``, or its newest; None when it has none."""
        found = self.releases(source)
        if version:
            found = [r for r in found if r["version"] == version]
            if not found:
                raise SystemExit(f"{source} has no release {version} in releases.json")
        return found[-1] if found else None

    def file(self, source: str, name: str, commit: str = None) -> bytes:
        """``legislation/<source>/<name>`` at ``commit``, or as the working tree has it."""
        if commit:
            return self.git("show", f"{commit}:legislation/{source}/{name}")
        return (self.root / "legislation" / source / name).read_bytes()

    def url(self, source: str, name: str, body: bytes, commit: str = None) -> str:
        """Where a realm fetches the file: GitHub at the commit that holds these bytes, else the mundus."""
        commit = commit or self.head()
        try:
            if self.file(source, name, commit) == body:
                return f"{RAW}/{commit}/legislation/{source}/{name}"
        except subprocess.CalledProcessError:
            pass
        return f"{SERVED}/legislation/{source}/{name}"

    def own_chapters(self, source: str) -> list:
        """The subject matters a source without releases writes: every chapter file of its folder."""
        folder = self.root / "legislation" / source
        return sorted(p.stem for p in folder.glob("*.py"))

    def has_calendar(self, source: str, commit: str = None) -> bool:
        try:
            self.file(source, "calendar.json", commit)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False


# -- the steps, each on a held network


class Realm:
    """A realm of the mundus as its founder acts in it, on the held network."""

    def __init__(self, net: Network, record: dict, entry: dict, voting_window: int):
        self.net, self.record, self.entry, self.window = net, record, entry, voting_window
        self.name, self.cid, self.founder = record["name"], record["backend"], record["founder"]
        self.source = entry["layer"]

    def call(self, op, args=None, read=False) -> dict:
        return self.net.call(self.cid, self.founder, op, args, read=read)

    def ok(self, op, args=None, read=False) -> dict:
        out = self.call(op, args, read)
        if not out.get("ok"):
            raise SystemExit(f"{self.name} {op}: {out.get('error') or out}")
        return out

    def proposal(self, proposal_id) -> dict:
        return self.ok("proposal_get", {"proposal": proposal_id}, read=True)["proposal"]

    def govern(self, op, args) -> dict:
        """``op`` as the founder: at once where the root department decides alone, else filed as a proposal, which the
        founder votes for and which must then be decided."""
        out = self.call(op, args)
        if not out.get("ok"):
            raise SystemExit(f"{self.name} {op}: {out.get('error')}")
        if out.get("requires_vote"):
            self.decided(out["proposal"])
        return out

    def decided(self, proposal_id) -> None:
        if self.proposal(proposal_id)["status"] == "voting":
            self.call("proposal_vote", {"proposal": proposal_id, "choice": "yes"})
        status = self.proposal(proposal_id)
        if status["status"] == "voting":
            raise SystemExit(f"{self.name}: proposal {proposal_id} needs more votes than the founder's; it is open until "
                             f"{time.strftime('%H:%M:%S', time.localtime(status.get('deadline') or 0))}")
        if status["status"] not in ACCEPTED:
            raise SystemExit(f"{self.name}: proposal {proposal_id} ({status.get('action')}) ended {status['status']}: "
                             f"{status.get('result') or status.get('error')}")

    def codex(self) -> dict:
        return self.ok("codex_status", read=True)

    def settled(self) -> dict:
        """The install job, run until it is done, failed or staged for its time."""
        def done():
            job = self.codex().get("job") or {}
            return job.get("status") in ("done", "failed") or job.get("step") == "wait"
        self.net.run_until(done)
        return self.codex()["job"]

    def installed(self, what: str) -> None:
        job = self.settled()
        if (job["status"], job.get("error") or "") != ("done", ""):
            raise SystemExit(f"{self.name}: installing {what}: job {job['status']} in {job.get('step')}: {job.get('error')}")
        print(f"    {self.name}: {what} installed", flush=True)

    def active_id(self):
        codex = self.codex().get("codex")
        return codex["id"] if codex else None

    def layers(self) -> list:
        codex = self.codex().get("codex") or {}
        return codex.get("layers") or []

    def running(self) -> dict:
        """source -> the version of it the realm runs (its own layer has none)."""
        chapters = (self.codex().get("codex") or {}).get("chapters") or {}
        return {c["source"]: c.get("version") for c in chapters.values()}

    def federated(self) -> bool:
        """A capital with quarters: it installs a release only by a change list."""
        return len(self.ok("quarter_directory", read=True).get("quarters") or []) > 1

    def record_of(self) -> list:
        """``[(source, release)]`` this realm publishes and relays, top to bottom, the newest of each source."""
        newest = {}
        for r in self.ok("legislation_releases", read=True)["releases"]:
            newest[r["source"]] = r
        return list(newest.items())


def change_list(net: Network, tree: Tree, realm: Realm, files: dict, release: dict = None) -> dict:
    """Serve ``files`` ({(source, file name): (bytes, commit)}) and a change list that sets them, as the realm's next
    codex; returns ``codex_install``'s arguments."""
    entries = []
    for (source, name), (body, commit) in sorted(files.items()):
        url = tree.url(source, name, body, commit)
        net.serve(url, body)
        chapter = f"{source}/{Path(name).stem}"
        entries.append({"chapter": chapter, "url": url, "mirrors": [], "sha256": sha(body), "size": len(body)})
    text = json.dumps({"kind": "realms-gos/legislation-change", "format": 1, "realm": realm.source,
                       "replaces": realm.active_id(), "set": entries, "remove": [], "release": release,
                       "summary": {"en": f"set up by realms mundus: {release['source'] if release else realm.source}"}}
                      ).encode()
    url = f"{SERVED}/changes/{realm.name}/{sha(text)[:16]}.json"
    net.serve(url, text)
    return {"url": url, "checksum": "sha256:" + sha(text)}


def release_files(tree: Tree, release: dict) -> dict:
    """The chapters and calendar of ``release`` (a releases.json entry with its ``source``), at its commit."""
    source, commit = release["source"], release["commit"]
    files = {(source, f"{sm}.py"): (tree.file(source, f"{sm}.py", commit), commit) for sm in release["chapters"]}
    if release.get("calendar"):
        files[(source, "calendar.json")] = (tree.file(source, "calendar.json", commit), commit)
    for (src, name), (body, _) in files.items():
        listed = release["calendar"] if name == "calendar.json" else release["chapters"][Path(name).stem]
        if "sha256:" + sha(body) != listed:
            raise SystemExit(f"{src}/{name} at {commit[:10]} is not the file release {release['version']} lists")
    return files


def own_files(tree: Tree, source: str, release: dict = None) -> dict:
    """The realm's own layer: the files of ``release``, or every chapter of the checkout's folder and its calendar."""
    if release:
        return release_files(tree, {**release, "source": source})
    files = {(source, f"{sm}.py"): (tree.file(source, f"{sm}.py"), None) for sm in tree.own_chapters(source)}
    if tree.has_calendar(source):
        files[(source, "calendar.json")] = (tree.file(source, "calendar.json"), None)
    return files


def link(follower: Realm, source: Realm) -> None:
    follower.govern("sync_source_add", {"source": {"Realm": source.cid}, "name": source.record["display_name"]})
    source.govern("follower_accept", {"follower": follower.cid})
    print(f"    {follower.name} syncs from {source.name}", flush=True)


def layers_from_above(net: Network, tree: Tree, realm: Realm, sources: list) -> int:
    """Every release the sync sources' records list, top to bottom, that the realm does not run: by a change list for
    its first layered codex and in a federation, else by "Propose install". Returns how many it installed."""
    wanted = {}
    for source in sources:
        for layer, release in source.record_of():
            wanted.setdefault(layer, release)
    done = 0
    for layer, release in wanted.items():
        if realm.running().get(layer) == release["version"]:
            continue
        what = f"{layer} {release['version']}"
        if realm.active_id() is None or realm.federated():
            args = change_list(net, tree, realm, release_files(tree, release),
                               release={"source": layer, "version": release["version"]})
        else:
            args = {"source": layer, "version": release["version"]}
        realm.govern("codex_install", args)
        realm.installed(what)
        done += 1
    return done


def own_layer(net: Network, tree: Tree, realm: Realm, release: dict = None) -> None:
    """The realm's own chapters, from the release or the checkout."""
    realm.govern("codex_install", change_list(net, tree, realm, own_files(tree, realm.source, release)))
    realm.installed(f"its own layer {realm.source}" + (f" {release['version']}" if release else ""))


def publish(realm: Realm, release: dict) -> None:
    """``legislation_release_publish`` for an entry of the source's releases.json, by the realm's vote."""
    effective_from = calendar.timegm(time.strptime(release["effective_from"], "%Y-%m-%d"))
    realm.govern("legislation_release_publish", {
        "source": realm.source, "version": release["version"], "effective_from": effective_from,
        "commit": release["commit"], "calendar": release.get("calendar"), "notes": release.get("notes") or {},
        "chapters": [{"domain": sm, "checksum": c} for sm, c in release["chapters"].items()]})
    print(f"    {realm.name}: published {realm.source} {release['version']}", flush=True)


def in_order(entries: list) -> list:
    """The layered realms, each after those it follows."""
    done, ordered, pending = set(), [], [e for e in entries if e.get("layer")]
    while pending:
        ready = [e for e in pending if set(e.get("follows", [])) <= done]
        if not ready:
            raise SystemExit(f"the realms {[e['name'] for e in pending]} follow each other in a circle, or a realm "
                             "without a layer")
        for e in ready:
            ordered.append(e)
            done.add(e["name"])
            pending.remove(e)
    return ordered


def realm_of(m, net: Network, name: str) -> Realm:
    entry = realms.entry(m, name)
    if not entry.get("layer"):
        raise SystemExit(f"{name} has no layer in the mundus file")
    return Realm(net, realms.load(m, name), entry, m.spec.get("voting_window", 180))


def set_up_layers(m) -> None:
    """Links, layers from above, own layers and publications of every layered realm that does not run its own yet."""
    entries = in_order(m.spec["realms"])
    if not entries:
        return
    tree, net = Tree(m.realms_dir / "codices"), Network(m.casals_home)
    with net.held():
        made = {}
        for e in entries:
            realm = realm_of(m, net, e["name"])
            made[e["name"]] = realm
            if realm.source in realm.layers():
                print(f"  {realm.name}: runs {realm.source} already")
                continue
            print(f"  {realm.name}: {realm.source}", flush=True)
            sources = [made[name] for name in e.get("follows", [])]
            linked = {s["source"].get("Realm") for s in realm.ok("realm_info", read=True).get("sync_sources", [])}
            for source in sources:
                if source.cid not in linked:
                    link(realm, source)
            layers_from_above(net, tree, realm, sources)
            release = tree.release(realm.source, e.get("release"))
            own_layer(net, tree, realm, release)
            if release:
                publish(realm, release)
            print(f"    {realm.name} runs {' > '.join(realm.layers())}", flush=True)


def publish_release(m, name: str, version: str = None) -> None:
    """``name`` adopts its own layer at ``version`` (default: the newest of its releases.json) and publishes it."""
    tree, net = Tree(m.realms_dir / "codices"), Network(m.casals_home)
    with net.held():
        realm = realm_of(m, net, name)
        release = tree.release(realm.source, version)
        if not release:
            raise SystemExit(f"{realm.source} has no releases.json: the realm's own layer is published by nobody")
        own_layer(net, tree, realm, release)
        publish(realm, release)


def install_relayed(m, name: str) -> None:
    """``name`` installs what its sync sources relay and it does not run yet."""
    tree, net = Tree(m.realms_dir / "codices"), Network(m.casals_home)
    with net.held():
        realm = realm_of(m, net, name)
        sources = [realm_of(m, net, s) for s in realm.entry.get("follows", [])]
        if not layers_from_above(net, tree, realm, sources):
            print(f"  {name} runs what its sync sources relay: {realm.running()}")


# -- quarters


def add_quarters(m) -> None:
    """For each realm with ``quarters``, ask Casals for the ones it lacks (``quarter_add``, the founder's call), wait
    until the capital has registered them, turn its auto-scale off, and furnish them."""
    for e in m.spec["realms"]:
        want = e.get("quarters", 0)
        if not want:
            continue
        record = realms.load(m, e["name"])
        call = realms.as_founder(m, record)
        have = quarter_rows(call)
        print(f"  {e['name']}: {len(have)} of {want} quarters", flush=True)
        for _ in range(want - len(have)):
            asked = call("quarter_add", {})
            if not asked.get("ok"):
                raise SystemExit(f"{e['name']}: quarter_add: {asked.get('error')}")
            if asked.get("requires_vote"):  # the root department does not decide alone
                call("proposal_vote", {"proposal": asked["proposal"], "choice": "yes"})
            until(lambda: len(quarter_rows(call)) > len(have) or not pending(call), QUARTER_WAIT,
                  f"{e['name']}: Casals building a quarter")
            have = quarter_rows(call)
        record["quarters"] = [{"name": q.get("name"), "backend": q.get("id"), "frontend": q.get("frontend_canister_id")}
                              for q in have]
        realms.save(m, record)
        # The mundus made the quarters; one the capital grew by itself later (members join the capital, and a full
        # capital asks for another) would run no codex: a quarter added after the capital's codex never gets one
        # (realms-gos#745).
        off = call("quarter_configure", {"auto_scale_enabled": False})
        if not off.get("ok") or off.get("requires_vote"):
            raise SystemExit(f"{e['name']}: turning auto-scale off: {off.get('error') or off}")
        print(f"  {e['name']}: quarters {', '.join(q['name'] or q['backend'] for q in record['quarters'])}", flush=True)
        furnish_quarters(m, record)


def furnish_quarters(m, record: dict) -> None:
    """What a new quarter lacks for a person to live in it, which nobody can give it but a controller: Casals builds a
    quarter and the capital adopts it, but no step gives it the capital's invite codes, file registry, trust policy
    or extensions. A person joins a quarter of their choice from the capital's join page, with an invite that is the
    canister's own, and then sees the screens of the extensions their quarter has. The mundus gives each quarter the
    capital's, as the capital, which is a controller of each quarter, with the network held."""
    import base64

    from ic.principal import Principal

    registry = realms.platform(m).bindings(realms.platform(m).conductor_backend())["file-registry"]
    net = Network(m.casals_home)
    with net.held():
        extensions = net.call(record["backend"], record["founder"], "extension_list", {"count": 500}, read=True)
        for q in record.get("quarters", []):
            listed = net._rest("post", "read/get_controllers",
                               {"canister_id": base64.b64encode(Principal.from_str(q["backend"]).bytes).decode()})
            controllers = [Principal(bytes=base64.b64decode(c["principal_id"])).to_str() for c in listed or []]
            if record["backend"] not in controllers:
                raise SystemExit(f"{record['name']}: the capital is no controller of {q['name']}: {controllers}")

            def do(op, args, q=q):
                out = net.call(q["backend"], record["backend"], op, args)
                if not out.get("ok") and not any(done in str(out.get("error")) for done in ("exists", "taken")):
                    raise SystemExit(f"{record['name']}: {op} in {q['name']}: {out.get('error')}")

            for profile, code in (record.get("browser_invites") or {}).items():
                do("invite_create", {"profile": profile, "code": code, "max_uses": 50 if profile == "member" else 5})
            do("realm_configure_infrastructure", {"file_registry_canister_id": registry})
            do("realm_configure_trust_policy", {"require_marketplace_approval": False})
            have = {x["id"]: x.get("version") for x in
                    net.call(q["backend"], record["backend"], "extension_list", {"count": 500}, read=True)
                    .get("extensions", [])}
            for x in extensions.get("extensions", []):
                if have.get(x["id"]) != x.get("version"):
                    do("extension_install", {"extension": x["id"], "version": x["version"]})
            print(f"  {record['name']}: {q['name']} has the capital's invite codes and extensions", flush=True)


def quarter_rows(call) -> list:
    """The quarters a capital's directory lists, without the capital's own row."""
    found = call("quarter_directory", read=True)
    return [q for q in found.get("quarters") or [] if q["id"] != found.get("capital")]


def pending(call) -> bool:
    scale = call("scale_status", read=True)
    return bool(scale.get("in_flight") or scale.get("pending"))


def until(reached, timeout: int, what: str) -> None:
    started = time.time()
    while time.time() - started < timeout:
        if reached():
            return
        time.sleep(10)
    raise SystemExit(f"{what}: not done in {timeout} s")
