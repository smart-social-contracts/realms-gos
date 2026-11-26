# A mundus on your machine: `realms mundus up`

`realms local start` gives you one realm. `realms mundus up` gives you a whole **mundus**: the GaaS portal and
the Realms product infrastructure on a local network of its own, several realms minted through the portal the way a
person makes one, the links and legislation layers between them, a capital's quarters, and simulated people
([geister](https://github.com/smart-social-contracts/geister)) living in them. It is the place to build and
rehearse a demo before anything goes to the IC.

```sh
realms mundus up valencia        # examples/mundi/valencia.json, from nothing (about 50 minutes the first time)
realms mundus urls               # the portal, every realm and quarter, invite codes, how to sign in
realms mundus up valencia        # again: only what is missing (a realm you added to the file)
realms mundus down               # stop it and forget its realms; builds and sources are kept
```

It runs from a realms-gos checkout (`pip install -e cli`), and builds that checkout as it is, uncommitted changes
included: that is the point. It needs `icp`, `dfx`, `ic-wasm`, `node`, `python3`, `make` and `git`.

## What `up` does

1. **Sources.** gos-as-a-service, Casals, file-registry and geister are cloned once into
   `~/.realms/mundi/sources/` and checked out at the refs the mundus file names, or used where the file points
   (a path instead of a ref).
2. **Python.** One environment for all of them, `~/.realms/mundi/venv`. The three Python repositories pin
   different ic-basilisk versions, so they are installed one after the other, realms-gos last. Every
   `realms mundus` command then runs inside it (it starts itself again there).
3. **Builds.** Every canister and frontend of the checkouts (`scripts/up.sh --build-only`, `realms files build`,
   the Casals conductor, the GaaS installer, registry and portal). `--rebuild` builds again.
4. **Network.** A local network of its own on a free port (8001-8099), with a local Internet Identity, the product
   orchestra and the GaaS orchestra (`scripts/local_up.sh --gaas`), under the mundus' own `CASALS_HOME`: never
   `~/.casals` or `~/casals-home`. The identity is a throwaway plaintext key, `realms-mundus`.
5. **Realms.** Each realm of the file is minted through the portal (`request_deployment` on the realm registry,
   signed by its founder, geister's key for its seed), then launched by the founder as the setup wizard does:
   extensions, branding, tokens, a short voting window, the sign-in the file asks for, two invite codes.
6. **Quarters.** A realm with `quarters` asks Casals for them (`quarter_add`) and waits until it has registered
   them. Quarters come before layers, so that they follow each install; the codex does not need that order any more,
   since a quarter added after its capital's install is sent the codex whole at its first report
   ([realms-gos#745](https://github.com/smart-social-contracts/realms-gos/issues/745)). The mundus turns the
   capital's auto-scale off: the swarm's members join the capital, and a full capital would grow quarters of its own,
   which nobody would furnish. Then it gives each quarter what no step of its adoption does, and
   only a controller can: the capital's invite codes (a person joins the quarter of their choice from the capital's
   join page, with an invite that is the quarter's own), the platform's file registry, the capital's trust policy and
   the capital's extensions (a member sees the screens of the extensions their quarter has). The mundus does it as
   the capital, a controller of each quarter, with the network held.
7. **Layers.** The links, layers from above, own layers and publications of the realms that have a `layer` (below).
   A federation installs every layer by a change list.
8. **Cycles.** Every canister of the network below 90 T is topped up to 100 T. A local network charges cycles as
   the IC does, and a realm the portal mints gets about 1.5 T: a capital the swarm lives in uses that up within a
   day, freezes, and answers every call "out of cycles" (a person who tries to join sees it). `realms mundus status`
   shows the lowest balances, `realms mundus top-up` tops them up again.
9. **Check and swarm.** The portal and every realm are opened in headless Chromium (screenshots in the mundus'
   `shots/`), and the geister swarm is started (`--no-swarm` to leave it).

Everything a mundus keeps is in `~/.realms/mundi/<name>/` (`REALMS_MUNDI_HOME` to move it): its network, bindings,
realms (`realms/<name>.json`), logs, screenshots and swarm. `~/.realms/mundi/current` names the mundus the other
commands act on; `--mundus NAME` picks another. Two mundi can run side by side, each on its own port.

## The mundus file

`examples/mundi/valencia.json` and `examples/mundi/proof.json` are the two in this repository, and `examples/mundi/episode3.json`, València's chain as the Episode 3 walkthrough films it: every realm in the test token DEMO, no quarters and no simulated people, whose names and records a seed script adds instead.

| Field | |
|---|---|
| `name` | the mundus' folder under `~/.realms/mundi` (default: the file's name) |
| `sources` | a git ref or a path for each of `gos-as-a-service`, `Casals`, `file-registry`, `geister` (default `main`) |
| `voting_window` | seconds a proposal stays open (default 180): a week cannot pass on a live network |
| `test_sign_in` | `true`: the realms and the portal show a picker of test identities; `false` (the default in both files): sign-in goes to the local Internet Identity, a test build that asks for a seed index instead of a passkey |
| `day_seconds` | real seconds a simulated day of the swarm lasts |
| `realms` | the realms, in the order they are minted |

Each realm:

| Field | |
|---|---|
| `name` | its slug: the portal shows it at `/r/<name>` |
| `display_name`, `manifesto`, `welcome_message` | what the portal and the realm show |
| `demo` | a demo realm of `examples/demo/` whose logo, background and texts it takes |
| `seed` | its keys: the founder is geister's key for `(seed, "founder")`, its people are geister's for the seed |
| `codex` | a codex package of `codices/codices` (agora, dominion, syntropia) that the installer installs |
| `extensions` | extensions to install besides the core ones and the codex's |
| `layer` | its own legislation layer: a source id of `codices/legislation` (`eu/es/vc/46250-valencia`) |
| `follows` | the realms it takes legislation from (each must have a `layer`) |
| `release` | which release of its `releases.json` it adopts and publishes (default: the newest) |
| `quarters` | how many quarters it has, besides the capital |
| `currency` | the symbol it keeps its accounts in, on a test ledger of its own (default: the one its codex bills in, else GST) |
| `population` | geister's people, by persona: `compliant`, `watchful`, `exploiter`, `apathetic` |

## Layers: the network held still

A realm with a `layer` is set up after the realms it `follows`, as `tests/deployed/test_valencia.py` does on a
test's PocketIC ([layered codex](../codices/layered-codex.md) §3.3, §7, §8):

1. it syncs from each realm it follows, and each accepts it;
2. it installs the layers from above at the versions their records list: the first by a change list (a realm's
   first layered codex names its own layer), the others by "Propose install", whose chapters come from the sync
   source's capital;
3. it installs its own layer from the realms-codices checkout;
4. if its source has releases, it publishes the one it adopted, for its followers.

Each step is a call of the founder's. At setup the root department decides alone (until a codex or the members give
it a policy), so a governed call runs at once, with no proposal. That is also why an own layer goes in at once under
Spain's layer, whose local regime makes an ordinance wait 15 working days after its vote: that rule judges
proposals, and there is none. Where the root department does not decide alone, the call is filed as a proposal and
the founder votes; one that needs other votes is left open, and the mundus says so.

A change list is fetched over HTTPS and names the codex it replaces, so it exists only for that moment and lives
nowhere on the internet. A local network is a PocketIC instance, so the mundus **holds it still** for these steps
([`mundus_held.py`](../../cli/realms/cli/commands/mundus_held.py)): it stops the instance's own rounds, makes them
itself, answers the realms' outcalls from what it serves (the change lists, and chapters changed in the checkout)
and otherwise from the internet (chapters at the commit GitHub has), and lets the network go live again. While it
is held nothing else runs, and the calls go through PocketIC's own API, as the founders.

**The clock is never moved ahead.** Every signed call, a browser's or the swarm's, carries an expiry by the wall
clock, and a network ahead of it refuses them all ("Invalid request expiry"). While held, the network's time stands
still; it is set level with the wall clock before the network goes live. `realms mundus clock` shows both.

## Legislation that changes

```sh
realms mundus publish eu 1.0.1     # the EU adopts and publishes its release 1.0.1
realms mundus install valencia     # València, a federation, installs what its sync sources relay
```

A follower finds a new release at its next check of its sync sources (the swarm's administration checks every
simulated day). In a realm that is no federation, its members install it by "Propose install", and the swarm's
members do: a release travels down the chain one vote at a time, since each realm relays what it installed. A
federation cannot install a release that way, because its quarters stage only what a change list names:
`realms mundus install` writes the change list, with the network held, and the founder makes the call. Where the
root department decides alone, it is installed at once; the members do not vote on it.

## Other commands

| | |
|---|---|
| `realms mundus status` | whether the network is up, its memory, the realms, the canisters lowest on cycles |
| `realms mundus top-up` | every canister below 90 T up to 100 T cycles, as `up` does |
| `realms mundus check` | the browser check again |
| `realms mundus sign-in test\|internet-identity` | how people sign in, on the portal and every realm |
| `realms mundus clock` | the network's time beside the wall clock |
| `geister swarm start\|status\|stop\|dashboard` | the simulated people, from the mundus' folder |
| `geister agent ls\|show NAME\|act NAME OP [JSON]` | one of them |

## What a mundus does not show

- **The IC.** The local Internet Identity, the signing key (`dfx_test_key`, where staging has `test_key_1`), cycles
  that cost something and staging's own GaaS are only the IC's. A demo built here is rehearsed once on staging
  before it is given there.
- **Members spread across quarters.** geister's members join the capital; only the web join page sends a person
  to a quarter. They live in the capital.
- **Explain.** codex_viewer's Explain asks the realm's AI assistant, which answers from an outside service that
  accepts only staging's and production's origins: a browser on a local network gets no answer, so the mundus
  leaves the assistant off.
- **A reload.** Loading a member screen's address directly fails to sign in
  ([realms-gos#737](https://github.com/smart-social-contracts/realms-gos/issues/737)): move around by clicking.
- **The registry's numbers.** The portal shows 0 users and no logo for every realm: a realm never publishes its
  listing ([realms-gos#736](https://github.com/smart-social-contracts/realms-gos/issues/736)).

The mundus was first built in a workspace's own tooling (`.cto/gaas-local-sim/`, 18 November 2026) and moved
here, into the CLI, with the layers and quarters added.
