# The layered codex

A realm's legislation as a **layered codex**: chapters from several sources, pinned by checksum and
installed by vote. This page is the design of the layered codex in Realms GOS: the repository layout,
the chapter format, the `F` language for forbids, the change list, the install task, the runtime, the
revert floor, compliance, the official journal and the `realms legislation` tools. What GGG provides
for it (the operation names, the links between realms, `effective_at`, the realm's time zone, the
Period units) is in the GGG repository:
[What GGG provides for a layered codex](https://github.com/smart-social-contracts/ggg/blob/main/docs/layered-codex.md).

Status: approved design, November 2026. Nothing here is built yet;
[#480](https://github.com/smart-social-contracts/realms-gos/issues/480) tracks each part (§14). Today's
single-file codex ([Codices](README.md)) keeps working beside it.

The word is **legislation**, in code, docs and UI. Proper names of legal texts (Ley 39/2015, ...) are
unchanged.

---

## 1. Summary

- **Layers** are **sources**: the bodies whose legislation applies to the realm (the EU, Spain, the
  Comunitat Valenciana, EMSHI, València), from top to bottom. Which ones, and in which order, comes
  from the realm's **links**: a realm picks by vote the realms it syncs from, and each accepts it by
  vote. There is no parent.
- **Domains** are subjects of legislation (tax, procedure, register, water, ...), named by GGG.
- **A chapter** is one layer's legislation in one domain: one Python file, `<source>/<domain>.py`, in
  the repository [realms-codices](https://github.com/smart-social-contracts/realms-codices). It
  holds one class. Its methods are named after GGG's own operations: hooks `on_<operation>`, guards
  `check_<operation>` and `compute_<operation>`, plus forbids, settings and helpers.
- **Within a domain,** each chapter's class extends the chapter one layer up. Marks (`fixed`,
  `extension`, `overridable`) say what lower layers may do, and the runtime enforces them.
- **Across domains,** a chapter calls another domain through the realm (`self.legislation.<domain>`),
  never by import.
- **Chapters use GGG directly.** Effects are GGG operations (`self.ggg.<operation>`), entity reads are
  read-only, and a `forbid` names a GGG operation and reads its arguments. There is no catalogue of
  slots between the chapters and GGG.
- **`forbid` rules** are expressions (`F.rate > pct("1.30")`). The runtime turns them into Cedar, so
  Cedar's validator runs during the install and Cedar's analysis runs off-chain.
- **People edit in the repository first.** A proposal names a change list (URL + sha256). If it
  passes, the realm downloads the chapters, verifies every checksum, runs the checks, and switches all
  at once, at the proposal's `effective_at` or on acceptance. Each chapter is pinned by checksum. A
  proposal's change list never changes: to change it, withdraw and propose again.
- **A revert is always possible:** a fast one by the IT admin team (defined by the realm admins), back
  to the previous state, or any revert by a member vote. No legislation can block either.
- **The tools are the `realms` CLI:** `realms legislation check|resolve|propose|diff`, on one library,
  `realms.legislation`, which the realm canister also runs.
- **The official journal** is a core extension, one per realm: a report of the latest news and changes,
  nothing else. Legislation applies when the proposal passes and its changes are executed; no
  publication, in the official journal or in an outside gazette, plays a role in that.

| Term | Meaning |
|---|---|
| source | a body that legislates; in a realm's codex it is a **layer**. Id = its folder path, e.g. `eu/es/vc`. The id names the source and does not place it |
| sync source | a realm that another realm takes legislation from, chosen by the follower's vote and accepted by the sync source's vote |
| follower | a realm that syncs from a given sync source. València is a follower of the Comunitat Valenciana (CV below) |
| link | the relation between a follower and one of its sync sources; active once both have voted |
| domain | a subject of legislation, named by GGG, e.g. `tax` |
| chapter | one source's legislation in one domain: `legislation/eu/es/tax.py` |
| layer path | the sources one domain of a realm passes through, top to bottom; it comes from the links |
| calendar | a source's holidays: `calendar.json`, beside its chapters. A realm's calendar is the union of those of the layers it runs |
| codex | what a realm stores: its layer paths plus every pinned chapter and calendar. A **layered codex**, as opposed to a legacy single-file codex |
| release | a published version of all of one source's chapters and its calendar, with an effective date. Installed whole |
| change list | the JSON file a proposal points to: which chapters to set or remove |
| mark | `fixed`, `extension` or `overridable`, on a method or setting |
| zone | a named area inside a realm with no canister of its own (GGG's `Zone`): a district |
| official journal | the realm's official journal, the core extension `journal`: a report of the latest changes, one issue a day, served by the canister as JSON (§9.6). It plays no role in when legislation applies |

---

## 2. Design rules

These are settled. Each later section applies them.

1. **Hosting is by URL and checksum.** Any public URL works, because the sha256 pins the bytes. There
   is no file-registry upload for legislation. GGG's existing `ProposalAction.Code { url; checksum }`
   carries it. A follower may also take the bytes from its sync source's capital (§9.2); the checksum
   is checked either way.
2. **The layout of the layered codex belongs to Realms GOS, not GGG.** GGG owns the domain names, its
   operations with their arguments and events, and the additions listed in §13.
3. **Legislation from above:** a follower votes only on *when* to install it, before its effective
   date. It does not vote on whether it applies while it syncs from that source. A challenge goes to a
   justice case. If the realm does not install in time, it keeps its old legislation and is flagged
   **non-compliant** on its public record. What follows is decided by the sync source's own
   legislation, never by the follower's votes.
4. **Conflicts:** an install whose layer paths conflict is refused. This happens per install, not per
   domain, for now. Paths from several sync sources conflict unless, in each domain, one extends the
   other (the one-chain rule, §3.3).
5. **First scope:** hooks, guards, `compute_` hooks, forbids and settings. Arithmetic in `forbid`
   expressions is allowed (§6).
6. **Revert floor:** `codex.revert` always stays proposable and votable, whatever any chapter says. The
   IT admin team can also revert quickly (§10).
7. **Composition:** classes, single inheritance down the layers of a domain, composition across
   domains, and dependencies declared and shown on proposals. No copying of code between chapters.
8. **Keep Cedar:** `forbid` rules are expressions translated to Cedar, not Python functions.
9. **Chapters use GGG directly.** GGG's operations, events and arguments are the interface. No slot
   catalogue, no middle layer.
10. **Realms sync from sources chosen by vote.** No parent; either side may end a link (§11).
11. **World facts live in the realm's own layer.** GGG is not a general data store.
12. **Numbers and calendars:** fact names carry no unit suffix; holidays are `calendar.json` beside the
    chapters; one rounding rule, set by GGG; `S.<setting>` reads only its own domain.
13. **Districts are zones, not quarters.** The realm's codex maps its own subdivisions, under their
    local name, to GGG zones.
14. **The tools are `realms legislation check|resolve|propose|diff`** in the existing `realms` CLI; the
    library is the self-contained module `realms.legislation`, and the canister's copy imports no CLI
    dependency.
15. **A revert vote** follows the voting rules of the codex being restored.
16. **A proposal's change list never changes.** Withdraw and propose again; proposals link, "replaces"
    or "relates to".
17. **Every ratio is an integer in hundred-millionths,** GGG's one scale; chapters write `pct("0.1")`.
    Setting names carry no unit suffix. The calendar is pinned by a release's `calendar` field and set as
    `<source>/calendar`.
18. **No rule** on hook order between domains, and none on lower layers filling settings that forbids
    above read.
19. **The realm domain's entry points are GGG hooks:** `on_codex_activate` and `compute_realm_join`. A
    follower leaving is a GGG event of the links, `link_notify`, which a chapter may hook and never guard
    or forbid. `sync_source_remove` is reserved: no chapter can forbid, guard or hook it.
20. **`each="taxes"`** ranges over the saved TaxPolicy's `taxes`, and the same check runs when the
    draft is saved.
21. **Each realm sets its own `Realm.timezone`** (an IANA name) and never takes it from another realm,
    from legislation or from a sync source; timestamps stay UTC. A quarter has a zone of its own: a new
    one starts on its capital's (a realm that joins keeps a zone it already chose), it may change it,
    and the realm may set it by a realm-wide vote. The
    capital's zone is the realm's official time, a quarter's its local time (§4.5). Realm-local time
    is computed in Realms GOS, with no change to the Python runtime
    ([#503](https://github.com/smart-social-contracts/realms-gos/issues/503)).
22. **One official journal per realm,** the core extension `journal`. Publication never decides when
    legislation applies.
23. **`on_codex_activate` runs once per realm,** not once per quarter, and not on a revert. Zones are
    keyed by their primary ids, so creating one twice cannot happen.
24. **A setting's value carries its unit** the way GGG writes a Period, with the unit constructors GGG
    defines for Period and no others: `default_decision_deadline = months(3)`. No setting shares a name
    with a GGG fact.
25. **A sync source's own release goes in its own official journal too.**
26. **A proposal can state `effective_at`** (UTC ns, optional). Once accepted, its action executes at
    that time, whatever the kind: transfers, codex installs and updates, settings, any call. An
    install's switch time is its proposal's `effective_at`, or the acceptance without one
    ([#504](https://github.com/smart-social-contracts/realms-gos/issues/504)). **`effective_at` never
    moves after acceptance.** If it proves too early, another proposal stops the scheduled one, and a
    new proposal sets the right date, so proposers leave a margin.
27. **Working days are a GGG Period unit,** `WorkingDay`, defined on
    [the GGG page](https://github.com/smart-social-contracts/ggg/blob/main/docs/layered-codex.md#7-the-workingday-unit-ggg7)
    against the realm's `calendar.json` and the time zone of the clock that counts (§4.5). GGG names
    the unit and realms count it
    ([#505](https://github.com/smart-social-contracts/realms-gos/issues/505)); the library gains
    `working_days(n)`. If the calendar has no entries for a year the count reaches, the
    count fails closed. The platform supports working days, and each realm's legislation decides
    whether to use them.

---

## 3. realms-codices: repository layout

New top-level folder `legislation/`, next to the existing `codices/`. The `codices/` folder (agora,
dominion, syntropia) is unchanged. There is no `tools/` folder: the tools are the `realms` CLI (§12).

```
legislation/
  README.md                       rules for authors; how to propose a change
  eu/
    source.json
    releases.json
    data_protection.py
    es/
      source.json
      releases.json
      calendar.json               Spain's holidays
      tax.py  procedure.py  register.py  local_regime.py  data_protection.py  language.py
      vc/
        source.json
        releases.json
        calendar.json
        local_regime.py  language.py  procedure.py
        emshi/                    a body beside the line: created by CV legislation
          source.json
          releases.json
          water.py
        46250-valencia/           a municipality: INE code + slug
          source.json
          calendar.json
          realm.py  tax.py  procedure.py  local_regime.py  data_protection.py
changes/
  eu/es/vc/46250-valencia/
    2026-11-20-ibi-bonus-2027.json   change lists, one per proposal (optional location, see §7)
```

The rules:

- **Source id** = folder path under `legislation/`, e.g. `eu/es/vc/46250-valencia`. Only lowercase
  letters, digits and `-` are allowed in each folder name. The id names the source; it does not say
  which realms run it. The links do (§3.3). The INE code in a folder name is only a name: GGG and the
  registry carry no national codes.
- **Chapter file** = `<domain>.py`, where `<domain>` is a domain id from the GGG list. A missing file
  is an empty chapter. No other `.py` files are allowed in a source folder.
- **Calendar file** = `calendar.json`, one per source at most, beside its chapters (§3.4). A source
  that sets no holidays, such as EMSHI, has none.
- **Test cases** = `<domain>.cases.json`, beside the chapter they test (§12.1).
- **The `realm` domain** (the realm's own hooks, such as `on_realm_join`; its entry point
  `on_codex_activate`, the hook on GGG's `codex_activate`; and `compute_realm_join`, which GGG asks for
  the quarter of a new member) may only be filled in the realm's own layer.
- **Districts are zones.** A district is not a quarter and not a source. The realm's codex maps its
  own subdivisions, under their local name (*distritos*, *Kreise*, districts), to GGG zones in its
  `realm` domain. `compute_realm_join` may keep neighbours together, but capacity decides first.

### 3.1 `source.json`

```json
{
  "id": "eu/es/vc/emshi",
  "name": {"ca-valencia": "Entitat Metropolitana de Serveis Hidràulics",
           "es": "Entidad Metropolitana de Servicios Hidráulicos"},
  "created_by": "Llei 2/2001",
  "publisher": {"realm": null}
}
```

| Field | Rule |
|---|---|
| `name` | official names, by language |
| `created_by` | the act that created the source; left out when none did, such as a municipality older than any statute |
| `publisher.realm` | the canister id of the source's own realm, if it has one, else `null` (§8) |

A source carries no time zone and no zone file: a realm's time zone is its own `Realm.timezone`
(§4.5), never a layer's, and the realm reads only its own zone from the release's tz data.

### 3.2 `releases.json`

One entry per release, newest last:

```json
[
  {
    "version": "2.4.0",
    "published": "2026-12-01",
    "effective_from": "2027-01-01",
    "commit": "<40-hex git commit>",
    "chapters": {
      "tax":       "sha256:<hex>",
      "procedure": "sha256:<hex>",
      "register":  "sha256:<hex>"
    },
    "calendar": "sha256:<hex>",
    "notes": {"es": "Actualiza el IBI tras el Real Decreto-ley 29/2026"}
  }
]
```

- **A release lists all of the source's chapters,** changed or not, and its calendar if it has one.
  It never lists the `realm` domain, which only the realm's own layer fills (§3). A follower that finds
  one in a release skips it and reports it. The source adopts the release by its own vote. A follower
  installs a release whole, never only some of its chapters, but downloads only the chapters on its own
  layer paths: EMSHI's water chapter reaches only the realms that sync from EMSHI.
- **The repository file is a convenience copy.** The authority is the source's published record (§8).
- **The realm's own layer needs no releases:** its proposals are its record.
- **`calendar.json` is released and pinned like a chapter:** the release's own `calendar` field holds
  its sha256, beside `chapters`
  ([realms-codices#10](https://github.com/smart-social-contracts/realms-codices/issues/10)). A change
  list sets it under the id `<source>/calendar` (§7).

### 3.3 Layer paths come from the links

A realm's layer path in a domain is the path its sync sources relay for that domain, followed by its
own layer. Each sync source relays its own paths, so a path grows by one layer at each link:

| Realm | Syncs from | Its water path |
|---|---|---|
| Spain | the EU | EU → Spain |
| CV | Spain | EU → Spain → CV |
| EMSHI | CV | EU → Spain → CV → EMSHI |
| València | CV and EMSHI | EU → Spain → CV → EMSHI → València |

- EMSHI writes water chapters only, so in every other domain its path equals CV's. Empty chapters are
  skipped, so València's tax path is Spain → València. A layer is on a domain's path only if its
  release has a chapter in that domain; the table above shows a domain every layer writes in.
- **One chain:** when a realm has several sync sources, their paths in one domain must form one
  chain, each the start of the longest, which the realm uses. Paths that differ are refused unless one
  extends the other, and the install fails. València passes, because EMSHI's water path extends CV's.
  Across domains, too, the records of the sync sources must, taken together, put every layer with a
  chapter or a calendar above or below every other: one order, no ties. Two layers that no record
  puts in order, such as CV and a metropolitan area that both sync from Spain, are refused, so
  `self.layer` has one order to count by.
- A CV town that does not sync from EMSHI gets a water path without EMSHI.

### 3.4 `calendar.json`

Holidays are data beside the chapters, not a domain: a holiday stops the clock for tax, procedure and
transparency alike.

```json
{
  "weekend": ["sat", "sun"],
  "years": {
    "2027": [
      {"date": "2027-01-01", "name": {"es": "Año Nuevo"}},
      {"date": "2027-10-12", "name": {"es": "Fiesta Nacional de España"}}
    ]
  },
  "legislation": "Ley 39/2015 Art. 30.7 (illustrative dates)"
}
```

| Field | Holds |
|---|---|
| `weekend` | the days of the week that are not working days |
| `years` | the holidays of each year, each with its date and its name by language |
| `legislation` | the provision the dates come from |

The realm's calendar, `self.calendar`, is the union of the calendars of the layers it runs, top to
bottom. Holidays are settled by proposing a change to the realm's own `calendar.json`, like any other
change to its layer; the change list names it `<source>/calendar` (§7), and a release pins it by its
`calendar` field (§3.2).

`self.calendar.add(day, period)` adds a GGG Period to a realm-local day (§4.5). For `working_days(n)` it
steps over the weekend days and the holidays of the realm's calendar, in the time zone of the clock
that counts (§4.5): a **working day** is a day that is neither, as
[GGG defines it](https://github.com/smart-social-contracts/ggg/blob/main/docs/layered-codex.md#7-the-workingday-unit-ggg7).
GGG names the unit `WorkingDay` ([ggg#7](https://github.com/smart-social-contracts/ggg/issues/7)) and
the realm counts it ([#505](https://github.com/smart-social-contracts/realms-gos/issues/505)). If the
calendar has no entries
for a year the count reaches, the count fails closed: the realm refuses to compute the date and says so,
and a guard that depends on it denies.

---

## 4. A chapter: what the file contains

```python
# legislation/eu/es/procedure.py
"""Estado: procedimiento administrativo común. Ley 39/2015."""

from realms.legislation import F, above, fixed, forbid, months, overridable, working_days


class Procedure(above("procedure")):
    uses = []                                             # domains this chapter calls; see §4.4

    default_decision_deadline = overridable(months(3), legislation="Ley 39/2015 Art. 21.3")   # a GGG Period

    over_six_months = forbid("procedure_define",
        F.decision_deadline.is_some() & (F.decision_deadline > months(6))     # GGG's fact, a Period: another unit fails closed (§6)
        & F.legislation.is_none(),
        legislation="Ley 39/2015 Art. 21.2")

    @fixed(legislation="Ley 39/2015 Art. 30")
    def working_days_after(self, day, n):
        return self.calendar.add(day, working_days(n))    # holidays come from calendar.json (§3.4)
```

Methods are named after GGG operations. `<op>` is the name of a GGG operation, such as `tax_assess`:

| Kind | Name | Runs | Gets | Returns |
|---|---|---|---|---|
| hook | `on_<op>(self, event)` | after GGG has done the operation | GGG's `Event`: `event.user`, `event.args`, `event.result`, `event.at` | nothing; queues GGG operations with `self.ggg.<op>(...)` |
| guard | `check_<op>(self, args)` | before the operation | the operation's args | `None` to continue, or `self.deny(reason)` |
| compute | `compute_<op>(self, ...)` | during the operation, when GGG asks | what GGG passes (for `tax_assess`: an entry and a subject) | what the operation asks for (for `tax_assess`: invoice items) |

Any domain's chapter may define a method for any GGG operation (water and waste both hook
`tax_policy_propose`). This design sets no order between domains.

The realm domain's two entry points are methods of these kinds, so they follow the same naming rule:

- `on_codex_activate(self, event)`: the hook on GGG's `codex_activate`. It runs once per realm when a
  codex becomes active, not once per quarter, and not on a revert. It replaces today's `init` hook. A
  zone set-up in it needs no check against creating a zone twice: zones are keyed by their primary ids.
- `compute_realm_join(self, ...)`: GGG asks it, during `realm_join`, which quarter a new member goes to.
  It may prefer the quarter that holds the member's zone; capacity decides first. It replaces today's
  `assign_quarter` hook.

The rules (the checker enforces them, §12):

1. **One class per file,** named after the domain in CamelCase (`tax` → `Tax`, `data_protection` →
   `DataProtection`), with the base `above("<its own domain>")`. Nothing else at module level except
   imports from the SDK and constants. The constants are frozen once the file has loaded (a list is a tuple, a
   dict read-only: write `list(CODES) + [...]` to build a longer list), so a method cannot leave a change in one
   for the next call. An imported name is bound by its import and never assigned to.
2. **Imports only from the SDK** (`realms.legislation`) and from `ggg`, for read-only access to GGG's
   entities (`from ggg import Land, Member`). No other import, anywhere in the file. A chapter cannot
   write an entity; it queues operations.
3. **Public members** are hooks, guards and `compute_` methods named after GGG operations, helpers
   (other public methods), settings, or forbids. A method named `on_x`, `check_x` or `compute_x` must
   name a GGG operation `x`. Names starting with `_` are private: other chapters can't call them.
4. **Every public member in a layer other than the realm's own** carries `legislation="..."`, the
   citation of the provision it implements. It is optional in the realm's own layer.
5. **Single inheritance only.** No metaclasses, no class decorators, no nested classes, no `exec`,
   `eval`, `compile`, `globals`, `locals`, `setattr`, `__import__`, `open`, no dunder attribute access
   (`__class__`, `__dict__`, `__bases__`, `__subclasses__`, ...). These are the same restrictions as
   codex code today ([Design choices](../architecture/design-choices.md)). No default arguments either,
   on a method, a nested function or a lambda. An attribute is set only on the event a hook was given, by plain
   assignment (`event.order = [...]`, never `+=`), and never on `self` or on anything else: one instance runs every
   layer's code of a domain in a call, so what one layer stored on `self` would change what another reads, a
   `fixed` method of a layer above included. `self` and that event are bound once, as the method's parameters, and
   never rebound, deleted or reused as a nested function's parameter. A chapter's class lives as long as the codex
   runs, so a default or an attribute on an SDK object, a parameter or a view would be state shared by every call.
   A method is defined under its own name (`charge = on_user_add` is refused), and a hook runs with the
   operation's own event, and with nothing a chapter makes up.
6. **No floats.** Money is whole units of the token and a rate is `pct("1.10")`, so a chapter holds no float
   literal, calls no `float()` or `complex()`, and writes no `/` or `/=`, which make a float of two integers
   (`//` divides whole numbers). It writes no `**`, `**=` or `pow()` either: a negative power is a float and a
   negative base to a fractional power a complex number, and the power can come at run time, where no check of a
   constant would hold; a chapter needs no power. The names `float`, `complex` and `pow` are neither read nor bound
   anywhere in a chapter, and `type(x)` of a float or a complex number is refused, so the data a chapter reads
   cannot hand it the class either. A share of money is `amount.times(rate)`, rounded by GGG's one rule (§4.5).

### 4.1 Marks

| Mark | On a method | On a setting | A lower layer may |
|---|---|---|---|
| `fixed` | only this definition runs | this value holds | not define the same name (refused) |
| `extension` | the runtime runs every layer's definition, top to bottom | values merge | add its own definition; it must not call `super()` |
| `overridable` | the lowest definition runs | the lowest value holds | redefine it, and call `super()` to use the one above |
| (none) | as `overridable` | as `overridable` | redefine it |

**`extension` in detail:**

- **Hooks:** every layer's definition runs, top to bottom. Each queues GGG operations with
  `self.ggg.<op>(...)`. The event object is shared down the chain: a layer may add attributes to it,
  and lower layers can read but not change attributes set above. Example: CV sets
  `event.languages_missing`, and València reads it.
- **Guards:** every layer's definition runs, top to bottom. Returning `None` continues; returning
  `self.deny(reason)` stops and refuses the operation. If all return `None`, the guard allows.
- **Helpers and settings returning values:** results merge in layer order.
  - Lists are concatenated, without duplicates.
  - Sets are joined.
  - Dicts merge, but a lower layer may only add new keys; reusing an existing key is an error.
  - Any other type is an error.

**Forbids** are never overridable and never removable. A lower layer that defines a member with the
same name as a forbid above is refused. Forbids from every layer of every domain all apply.

### 4.2 `above()`

`above("procedure")` returns the class of the nearest layer above this one, in this realm's procedure
layer path, that has a procedure chapter. The top layer gets the SDK's `Chapter` base. The file never
names a version: the realm's codex decides which class that is. That's why one València file works
whatever version of the CV chapter is pinned.

### 4.3 Settings

```python
default_decision_deadline = overridable(months(3), legislation="Ley 39/2015 Art. 21.3")   # in eu/es
default_decision_deadline = months(2)                                                     # in València
co_official = fixed(["ca-valencia", "es"], legislation="Estatut d'Autonomia Art. 6")      # in eu/es/vc
```

Settings are class attributes, read as `self.default_decision_deadline`, or across domains as
`self.legislation.procedure.default_decision_deadline`. Values must be JSON-serialisable: str, int,
bool, null, lists or dicts of those, and a Period as GGG stores it (count and unit). Floats are not
allowed. A number GGG types, such as a rate or an amount, is an integer in the scale GGG's fact schema
declares for it, and its name carries no unit suffix: `rate`, not `rate_bp`. Setting names carry none
either. **A setting's value carries its unit,** the way GGG writes a Period, and settings use only the
unit constructors GGG defines for Period, one per unit of GGG's `PeriodUnit` (Day, WorkingDay, Month,
Year): `days()`, `working_days()`, `months()`, `years()`. The SDK adds none. **No setting shares a name
with a GGG fact:** the setting is `default_decision_deadline`; `decision_deadline` is the
`procedure_define` fact.

**Rates**, like every GGG ratio, are integers in hundred-millionths (0.123456 % is 123 456); the
rule's text is on
[the GGG page](https://github.com/smart-social-contracts/ggg/blob/main/docs/layered-codex.md#33-the-fact-schema-units-rates-money-and-periods).
Integers inside, decimals for people: a chapter writes a rate as the legislation writes it,
`pct("0.1")`, and the SDK stores the integer:

```python
large_family_bonus = {"general": pct("60"), "special": pct("90")}    # 60 % and 90 % of the quota
```

**`check=`.** A setting may carry `check=`, a test of its value, run at install on the value that wins
in the realm's layer path. A codex whose value fails is refused:

```python
comment_period = overridable(working_days(30), legislation="Ley 7/1985 Art. 49",
                             check=lambda v: v >= working_days(30))   # a lower layer may lengthen it, never shorten it
```

Periods compare only within one unit. `days(45) >= working_days(30)` fails closed, with no conversion
through the calendar, so a lower layer that sets `comment_period = days(45)` is refused.

**Working days:** GGG's `Period` has a `WorkingDay` unit, defined on
[the GGG page](https://github.com/smart-social-contracts/ggg/blob/main/docs/layered-codex.md#7-the-workingday-unit-ggg7);
the realm's calendar (§3.4) and the time zone of the clock that counts (§4.5) decide which days count.
GGG names the unit; the realm counts it, and `working_days(n)` writes it. Each realm's legislation
decides whether to use working days at all.

**Plenary meetings are out of date in a GOS:** the plenary is the online vote, and a notice period is
only the gap before the vote opens (`Proposal.opens_at`). The example above is the public comment
period of a new ordinance: at least 30 working days for objections before the final vote (Ley 7/1985
Art. 49). Spain's `local_regime` chapter enforces it with a guard on `proposal_create`: for a change of
legislation, the vote may not open before `comment_period` has run on the realm's calendar from the
proposal's creation.

**World facts are settings of the realm's own layer.** Population, capital status, surface public
transport and the like are read only by the realm's own legislation, so GGG and the registry do not
hold them. A source above asks for the figure and leaves it empty; the realm's own layer fills it, and
a change list updates it like any other legislation:

```python
# legislation/eu/es/tax.py: Spain asks for the facts and leaves them empty
population = overridable(None, legislation="Ley 7/1985 Art. 17 (official population figures)")
capital = overridable(False, legislation="TRLRHL Art. 72.3 (capital de provincia)")

# legislation/eu/es/vc/46250-valencia/tax.py: València fills them
population = 807_693
capital = True
```

A forbid reads a setting of its own domain with `S.<setting>` (§6).

### 4.4 Calling another domain

A chapter declares the domains it calls in `uses` and reaches them through the realm. §5.2 shows both
forms: Spain's `local_regime` declares `uses = ["procedure"]` and calls
`self.legislation.procedure.working_days_after(...)`; CV's declares `uses = ["language"]` and reads
`self.legislation.language.co_official`.

| Call | Resolves to |
|---|---|
| `self.legislation.<d>` | domain d through all of the realm's layers: whatever applies in this realm, including lower layers' overrides |
| `self.layer.<d>` | domain d resolved only down to this chapter's own layer: the source's own view, ignoring lower layers |

- **Only declared domains.** A chapter may only reach domains listed in `uses`. Anything else is an
  error at load time.
- **No cycles.** If tax uses procedure and procedure uses tax, the checker refuses the cycle, and so
  does the install. The install also refuses a `uses` of the chapter's own domain, or of a domain the
  realm has no chapter in.
- **Depth 8.** Calls between domains stop at depth 8 (`MAX_DEPTH` in `realms.legislation.context`):
  the runtime refuses a ninth nested call, and the install refuses a `uses` chain that could reach it.
- **What's protected.** `fixed` members of the called domain can't be changed by lower layers, so a
  source that needs a guaranteed answer marks it `fixed`, as `working_days_after` is in §4.

### 4.5 The `self` API (at least)

| Call | Does |
|---|---|
| `self.ggg.<op>(**args)` | queues GGG operation `<op>` as an effect, as codex effects work today |
| `self.deny(reason)` | a guard's refusal |
| `from ggg import ...` | read-only access to the fields of GGG's entities that legislation may read: `get(id)`, `each()` and `find(field, value)` on an indexed field. A list reads as a tuple and a mapping as `row["code"]`; values are as the realm stores them (an attribute's status is `verified`, a parcel's owner `User:<id>`) |
| `Ref(kind, id)` | GGG's `Ref`, e.g. `Ref("external", "INE")` |
| `self.now()` | the current time, a UTC timestamp, as today |
| `self.approved_by(department)` | whether the current operation carries that department's approval |
| `self.legislation.<d>`, `self.layer.<d>` | §4.4 |
| `self.calendar` | the realm's calendar: the union of the `calendar.json` of the layers it runs (§3.4); `self.calendar.add(day, period)` adds a Period to a realm-local day, counting working days on it |
| `self.local_day(at)` | the realm's local day of UTC timestamp `at`, `YYYY-MM-DD`, computed by the realm from its own time zone (below): the day a working-day count starts from (§5.2) |
| `pct("1.10")` | a rate literal, stored as the integer in hundred-millionths (§4.3) |
| `days(n)`, `working_days(n)`, `months(n)`, `years(n)` | a Period, as GGG writes it: the unit constructors GGG defines, for settings (§4.3); the SDK adds none |
| `Money(units, decimals)` | an amount in the token's smallest unit: it adds, compares and `times(rate)`; an amount GGG hands a chapter (a parcel's `assessed_value`, below) is one |
| `amount.times(rate)` | money times a rate, rounded as GGG says |

**Rounding is one rule, set by GGG:** nearest cent, an exact half away from zero, stated with the money
kind of GGG's fact schema so that every implementation rounds the same way. The rule's text is on
[the GGG page](https://github.com/smart-social-contracts/ggg/blob/main/docs/layered-codex.md#33-the-fact-schema-units-rates-money-and-periods).
`times()` takes no rounding argument and a chapter names no rule.

**Money in a compute.** The subject `tax_assess` asks about reaches `compute_tax_assess` as the view of its row, the
object `Land.get(id)` gives, and not as the reference `"Land:L-1"`. A parcel's `assessed_value` is in the accounting
currency (GGG's model), and it reaches the chapter as a `Money` of that currency's decimals, so that
`subject.assessed_value.times(entry.rate)` rounds to its cent. Every amount a chapter reads is in it, so a codex
computes a tax only when it is billed in the accounting currency, whatever the tax's subject: `tax_assess` refuses a
codex's answer for one billed in another token, and such a tax has its own rate, amount or brackets. Only a `compute_`
method knows the token: anywhere else an amount read is a `Money` without decimals, and `times()` says so instead of
guessing. A chapter's own amounts are `Money(units, decimals)`, the decimals those of the amounts it read
(`subject.assessed_value.decimals`). `sum()` of amounts needs a start of the same kind:
`sum(amounts, Money(0, decimals))`. The realm's own tax code rounds with the same function
(`realms.legislation.rates.times`): there is one implementation.

**Round once.** GGG rounds an amount where it is complete (D23), not at each step: a tax by brackets adds the exact
products of each bracket, whole numbers in hundred-millionths of a unit, and rounds the total once. Rounding each
bracket and adding the cents gives another total. A tax of 0.1 % on the first 25.00 and 0.3 % above, on 40.00, is
2.5 cents and 4.5 cents: 7 cents rounded once, and 8 when each is rounded first. A chapter writes it the same way:

```python
def brackets_tax(self, value, brackets):       # value: a Money; brackets: up_to and rate, the last up_to None
    exact, lower = 0, 0                        # whole numbers, in hundred-millionths of a unit: nothing rounded yet
    for bracket in brackets:
        upper = value.units if bracket["up_to"] is None else min(value.units, bracket["up_to"])
        exact += max(upper - lower, 0) * bracket["rate"]
        lower = max(lower, upper)
    return Money(exact, value.decimals).times(1)    # the one rounding, of the total
```

**Time zones.** GGG's `Realm.timezone` is an IANA name such as `Europe/Madrid`
([ggg#5](https://github.com/smart-social-contracts/ggg/issues/5)). Timestamps stay UTC nanoseconds,
and an effective time is an exact UTC instant (§9.5). Realm-local time is computed in Realms GOS
([#503](https://github.com/smart-social-contracts/realms-gos/issues/503)): a pure-Python zone-file
reader plus date arithmetic, with no change to the Python runtime. Every release bundles the IANA tz data
for every zone, one module per zone file, as hex text that `bytes.fromhex` decodes (never base64, which costs 17M
instructions for a few KB), so the rules are refreshed whenever a realm is upgraded: there is no upload and no
checksum. A quarter is a full realm canister, so it has every zone too. A canister reads only its own zone, once,
when it is set and again when it starts after an upgrade, never in a query, and keeps the parsed zone in memory.
A change of `Realm.timezone` applies from that moment: stored times are UTC, so nothing is rewritten, and a
deadline or an `effective_at` already set does not move; only what is computed in local time from then on uses
the new zone. It is a governed realm setting. The day of a timestamp, for the working-day count of §5.2 or the
official journal's daily issue (§9.6), is the local day computed this way, on the clock that counts (below).

**Where a zone comes from.** Always from the realm itself, never from outside it:

- **A realm sets its own** with `realm_configure`. It never takes a zone from another realm, from
  legislation or from a sync source ([GGG I-ADM-4](https://github.com/smart-social-contracts/ggg/blob/main/model/README.md)).
  **The codex never sets a zone:** the runtime refuses any effect a hook queues that carries
  `timezone`. Its guards (`check_realm_configure`, `check_quarter_configure`) may constrain who may
  change a zone, like those of any governed operation.
- **A new quarter starts on its capital's zone.** `quarter_join_federation` copies it once, when the
  joining realm is still on `UTC` (GGG I-ADM-4: `UTC` means not yet chosen), as a freshly built quarter
  is. A realm that already chose a zone keeps it when it joins as a quarter. After that the zone is
  the quarter's own, and a later change of the capital's does not follow.
- **A quarter may change its own zone** with `realm_configure` on the quarter, governed there as today.
- **The realm may set a quarter's zone** by a realm-wide vote: `quarter_configure` on the capital takes
  a `timezone` for the `quarter` it names; the capital's own zone is set with `realm_configure`. A
  `timezone` with no `quarter` is refused. So is a malformed name, or one the release has no data for:
  `quarter_configure`'s `check=` also runs `realm_configure`'s zone test (`known_timezone`). Both are
  refused before the call becomes a proposal, so before any vote. When the proposal executes, the
  capital sends the setting to the quarter over the trusted capital-to-quarter channel, the
  `quarter.sync` permission the federation already uses
  ([Quarters and federation](../architecture/quarters-and-federation.md)), and the quarter applies it
  as its own zone. Only the name travels: the quarter's release already holds the data of every zone.
  If the quarter refuses it or cannot be reached, the operation fails and nothing changes.

**Settings the realm sets for its quarters.** A realm has one face, so its quarters share some settings
and keep others. The table and the mechanism are in
[Quarters and federation](../architecture/quarters-and-federation.md#what-the-realm-sets-for-its-quarters).

| Group | Settings | Rule |
|---|---|---|
| Identity | the realm's name (each quarter keeps its own quarter label), logo, background image and colours (branding), manifesto, the realm's terms text, lifecycle stage | kept in sync from the capital: the capital owns them, a quarter cannot change them, and a realm-wide vote changes them on the capital, which pushes them to every quarter |
| Shared foundations | accounting currency, the languages offered | kept in sync from the capital, as above |
| Local, with a default | time zone (above), welcome message | copied once when a quarter joins, then the quarter's own: the quarter changes it through its own governance, and the realm may set it by a realm-wide vote through `quarter_configure`; a joining realm that already chose a value keeps it |
| Not pushed as settings | voting rules (window, threshold, quorum) | they belong in the codex, which reaches every quarter at one switch time (§9.5); a pushed setting would bypass the legislation |
| Not pushed | registration (`quarter_join_mode` is the capital's already, through `quarter_configure`; `open_registration` is each quarter's own), capacity (the capital's already), notification settings, departments, members, treasury | local, or the capital's already |

The capital pushes a changed synced setting to its quarters over `quarter.sync`, and the answer to
`quarter_sync` carries the current values, so a quarter that missed a push catches up. On a quarter,
`realm_configure` (and its branding and token variants) and `setup_step` refuse a synced field with `Forbidden`,
naming the capital.

**Two clocks, with clear jobs.**

| Clock | Zone | Used for |
|---|---|---|
| the realm's official time | the capital's | realm-wide votes: their deadlines and working-day counts; a realm-wide `effective_at` as people see it; the realm's single official journal (§9.6) |
| a quarter's local time | the quarter's | its own departments' deadlines and working days; the local time its members see |

Realm-wide counts are made on the capital. A federal vote's deadline is set there once, as an instant,
and every quarter's leg keeps it. A quarter never counts on the capital's clock, and its own votes, its
root department's included, use its own zone. The calendar's holidays are the realm's, the same in
every quarter (§3.4): a quarter's zone decides only where its days begin and end.

The UI shows a realm-wide time in the official time (the capital's), with the quarter's local time
beside it when they differ.

For example, a codex switch set for 1 December 2026, 00:00 in Europe/Madrid is the instant 23:00 UTC
on 30 November. It is 08:00 on 1 December in a quarter on Asia/Tokyo and 17:00 on 30 November in one
on America/Mexico_City, and every quarter switches at that one instant (§9.5).

The SDK also provides general helpers that are the same for every source: dates, adding a Period to a
day (working days included), money, rates. Legislation-specific helpers, such as the day a procedure's
deadline falls on, live in a domain's chapter, never in the SDK.

This replaces today's [`ggg_sdk`](sdk.md) calls:

- `layer()`: the folder path is the source id;
- `setting()`: class attributes;
- `realm.extend()`: event attributes;
- `chain()` / `slot()`: done by the runtime.

---

## 5. Examples

The sources, provisions and figures below are illustrative. They show the mechanics, not the
legislation in force.

### 5.1 Model legislation: most towns write nothing

```python
# legislation/eu/es/vc/46250-valencia/procedure.py
class Procedure(above("procedure")):
    default_decision_deadline = months(2)
```

- **València:** two months. It is written in months because Spain's Art. 21.2 forbid compares a
  deadline in months and refuses any other unit (§6). The six-month forbid on `procedure_define` still
  applies.
- **A town with no `procedure.py`:** three months, Spain's default (Ley 39/2015 Art. 21.3).

### 5.2 A chain down the layers

A hook per layer on GGG's `codex_install`: an ordinance's adoption.

```python
# legislation/eu/es/local_regime.py
class LocalRegime(above("local_regime")):
    uses = ["procedure"]

    @extension(legislation="Ley 7/1985 Art. 65.2")
    def check_proposal_create(self, args):
        if "Code" not in args.action:                        # only this realm's own change of legislation (see below)
            return None
        earliest_close_day = self.local_day(self.now())      # a vote may close as soon as its outcome is certain
        if args.effective_at is None \
                or self.local_day(args.effective_at) < self.legislation.procedure.working_days_after(earliest_close_day, 15):
            return self.deny("an ordinance takes effect at the earliest 15 working days after the vote closes (Art. 65.2)")

    @extension(legislation="Ley 7/1985 Art. 65.2")
    def on_codex_install(self, event):
        for authority in ("AGE", "GVA"):
            self.ggg.notification_send(recipient=Ref("external", authority), topic="ordinance",
                                       title="Comunicació d'acord", body=event.result["codex"])
```

```python
# legislation/eu/es/vc/local_regime.py
from ggg import Document

class LocalRegime(above("local_regime")):
    uses = ["language"]

    @extension(legislation="Llei 4/1983; Llei 8/2010")
    def on_codex_install(self, event):
        languages = Document.get(event.args["document"]).languages
        event.languages_missing = [l for l in self.legislation.language.co_official
                                   if l not in languages]
```

```python
# legislation/eu/es/vc/46250-valencia/local_regime.py
from ggg import Member

class LocalRegime(above("local_regime")):

    def on_codex_install(self, event):
        title = "Nova ordenança" + (" (traducció pendent)" if event.languages_missing else "")
        for m in Member.each():                              # a member's id is its user's id
            self.ggg.notification_send(recipient=Ref("user", m.id), topic="ordinance",
                title=title, body=event.result["codex"])
```

The runtime runs Spain's, then CV's, then València's `on_codex_install` when the proposal is accepted,
and the communications of Art. 65.2 go out then. The codex waits `pending` and switches at the
proposal's `effective_at` (§9.2 step 6). Spain's guard on `proposal_create` makes the proposer set it
at least 15 working days after the vote can close (`working_days(15)`, counted on the realm's calendar
by Spain's procedure chapter, §4), so the Art. 65.2 period is expressed by the proposal, not by a
chapter calling `codex_activate` after a wait. The period really runs from the authorities' receipt of
the communication, which the guard cannot know at creation, so the proposer leaves a margin:
`effective_at` never moves after acceptance. If it proves too early, another proposal stops the
scheduled one, and a new proposal sets the right date. The realm domain's `on_codex_activate` hook runs
once per realm when the codex becomes active. The install appears in the realm's official journal
(§9.6), which reports it and plays no role in when it applies. The event's arguments and result are
GGG's.

The guard applies to the realm's own ordinances only. A proposal that installs a release from a sync
source (§11.4) runs no guard of the codex on `proposal_create`: the follower votes only on when to
install (§2, rule 3), and the source's own legislation set the release's date. A proposal whose action
is a reserved operation, such as `codex.revert`, runs no legislation at all (§10.4), so this guard can
neither refuse nor delay a revert.

### 5.3 Across domains: working days with each level's holidays

Holidays are data, one `calendar.json` per source:

```json
// legislation/eu/es/vc/calendar.json
{"years": {"2027": [{"date": "2027-10-09", "name": {"ca-valencia": "9 d'Octubre"}}]},
 "legislation": "Calendari laboral de la Comunitat Valenciana (illustrative)"}

// legislation/eu/es/vc/46250-valencia/calendar.json
{"years": {"2027": [{"date": "2027-01-22", "name": {"ca-valencia": "Sant Vicent Màrtir"}},
                    {"date": "2027-04-05", "name": {"ca-valencia": "Sant Vicent Ferrer"}}]},
 "legislation": "illustrative"}
```

`working_days_after` (Spain's procedure chapter, `fixed`, §4) counts on `self.calendar`, which for
València is the union of Spain's, CV's and València's holidays. No chapter lists a holiday. Spain's
`local_regime` calls `self.legislation.procedure.working_days_after` (§5.2), so the earliest
`effective_at` its Art. 65.2 guard accepts in València falls on the right day. A count that reaches a
year with no calendar entries fails closed, and the guard denies (§3.4). The dates are illustrative.

### 5.4 Guards stacking: sharing personal data

```python
# legislation/eu/data_protection.py
class DataProtection(above("data_protection")):
    no_basis = forbid("document_share", F.basis.is_none(), legislation="GDPR Art. 6")

    @extension(legislation="GDPR Art. 5-6")
    def check_document_share(self, args):
        if args.basis is not None and args.basis.legal_basis == "gdpr-6-1-a":
            return self.deny("consent is not a basis between public authorities")

# legislation/eu/es/data_protection.py
class DataProtection(above("data_protection")):
    @extension(legislation="Ley Orgánica 3/2018 Art. 8")
    def check_document_share(self, args):
        basis = args.basis
        if basis is not None and basis.legal_basis in ("gdpr-6-1-c", "gdpr-6-1-e") and basis.legislation is None:
            return self.deny("name the legislation that sets this public task")

# legislation/eu/es/vc/46250-valencia/data_protection.py
from ggg import External

class DataProtection(above("data_protection")):
    def check_document_share(self, args):
        # the recipient's kind is read from GGG's entity, never from the args
        recipient = External.get(args.recipient.id) if args.recipient.kind == "external" else None
        if recipient is not None and recipient.kind != "authority" \
                and not self.approved_by("Junta de Govern Local"):
            return self.deny("a recipient that is not an authority needs the Junta de Govern Local")
```

The guards run EU, then Spain, then València, and the first deny stops the operation. The arguments
are those GGG defines for sharing a document (§13): `basis {legal_basis, purpose, legislation,
special_category}`, `recipient` (a `Ref`) and `facts`.

### 5.5 Refused: overriding fixed legislation

```python
# legislation/eu/es/register.py
class Register(above("register")):
    authority = fixed(Ref("external", "INE"), legislation="Ley 7/1985 Art. 17.3")

    @fixed(legislation="Ley 7/1985 Art. 17.3")
    def on_user_add(self, event):
        self.ggg.notification_send(recipient=self.authority, topic="padron", title="alta", body=event.args["user"])

# legislation/eu/es/vc/46250-valencia/register.py   (refused)
class Register(above("register")):
    def on_user_add(self, event):
        pass
```

The checker reports, in CI and again in the realm:

```
eu/es/vc/46250-valencia/register.py:3: on_user_add is fixed by eu/es/register.py (Ley 7/1985 Art. 17.3)
```

### 5.6 The IBI rule as forbids, and the quota computed by GGG's rounding

Forbids name GGG's `tax_policy_propose` and range over the `taxes` list of the saved TaxPolicy that
the call's policy id names (`each`). The fact `rate` is an integer in hundred-millionths, and the
chapter writes it as the statute does, with `pct()`: the urban IBI rate lies between 0.40 % and
1.30 %, and its cap of 1.10 % rises by 0.07 % for a provincial capital, 0.07 % for surface public
transport and 0.06 % for services beyond Art. 26.

```python
# legislation/eu/es/tax.py
class Tax(above("tax")):
    capital = overridable(False, legislation="TRLRHL Art. 72.3")
    surface_public_transport = overridable(False, legislation="TRLRHL Art. 72.3")
    services_beyond_art26 = overridable(False, legislation="TRLRHL Art. 72.3")
    rustic_over_80pct = overridable(False, legislation="TRLRHL Art. 72.3")

    ibi_rate_required = forbid("tax_policy_propose",
        F.code.in_(["ibi_urban", "ibi_rustic"]) & F.rate.is_none(),
        each="taxes", legislation="TRLRHL Art. 72")

    ibi_urban_range = forbid("tax_policy_propose",
        (F.code == "ibi_urban") & F.rate.is_some() & ~F.rate.between(pct("0.40"), pct("1.30")),
        each="taxes", legislation="TRLRHL Art. 72.1, 72.3")

    ibi_urban_cap = forbid("tax_policy_propose",
        (F.code == "ibi_urban") & F.rate.is_some() & (F.rate > pct("1.10")
            + when(S.capital, pct("0.07")) + when(S.surface_public_transport, pct("0.07"))
            + when(S.services_beyond_art26, pct("0.06"))),
        each="taxes", legislation="TRLRHL Art. 72.3")

    ibi_rustic_cap = forbid("tax_policy_propose",
        (F.code == "ibi_rustic") & F.rate.is_some() & (F.rate > pct("0.90")
            + when(S.capital, pct("0.06")) + when(S.surface_public_transport, pct("0.05"))
            + when(S.services_beyond_art26, pct("0.06")) + when(S.rustic_over_80pct, pct("0.15"))),
        each="taxes", legislation="TRLRHL Art. 72.3")

    @fixed(legislation="TRLRHL Arts. 61, 65, 71 and 74")
    def compute_tax_assess(self, entry, subject):
        """GGG asks for the invoice items of each subject of each entry."""
        if entry.code != "ibi_urban" or subject.assessed_value is None:     # a parcel never assessed owes no IBI yet
            return []
        # Each invoice line is rounded once: the quota, then the bonus on the rounded quota.
        gross = subject.assessed_value.times(entry.rate)            # nearest cent, half away from zero
        net = gross - gross.times(self.ibi_bonus(subject.owner))
        items = [{"concept": entry.code, "amount": gross, "legislation": "TRLRHL Art. 71.1"}]
        if net < gross:
            items.append({"concept": entry.code + "_bonus", "amount": net - gross,
                          "legislation": "TRLRHL Art. 74"})
        return items

    @overridable(legislation="TRLRHL Art. 74")
    def ibi_bonus(self, owner):
        return 0                                        # owner: "User:<id>", as a parcel's owner is written

# legislation/eu/es/vc/46250-valencia/tax.py
from ggg import Attribute, Human

class Tax(above("tax")):
    capital = True
    surface_public_transport = True
    large_family_bonus = {"general": pct("60"), "special": pct("90")}      # 60 % and 90 % of the quota

    def ibi_bonus(self, owner):
        kind, _, user = owner.partition(":")
        if kind != "User":
            return 0
        for human in Human.find("user", user):                          # find: an indexed field and the value it has
            for attribute in Attribute.find("human", human.id):
                if attribute.kind == "large_family" and attribute.status == "verified":
                    return self.large_family_bonus.get(attribute.value, 0)
        return 0
```

- **The rate lives in one place:** the entry of the adopted policy, proposed with
  `tax_policy_propose`. The forbids check it. València's chapter holds no rate.
- **An IBI entry must state its rate:** `ibi_rate_required` forbids an `ibi_urban` or `ibi_rustic` entry
  with no `rate`. The rate is an optional fact, so the other three forbids test `F.rate.is_some()` (§6,
  rule 3), and an entry with no rate would pass all of them. The requirement is Spain's legislation,
  not GGG's: GGG stays generic.
- **The same forbids run when the draft is saved** (`tax_policy_draft`), so an entry they refuse never
  reaches a proposal.
- **The settings** `capital`, `surface_public_transport` and the rest are world facts of the realm's
  own layer (§4.3), read by Spain's forbids through `S.` and fixed per realm at build. They never come
  from whoever proposes the rate.
- **The amount** is computed by `compute_tax_assess`, which GGG calls for each entry and subject, page
  by page. The subject is the view of the parcel (`Land.get`'s, with its `assessed_value` and `area`), whose
  `assessed_value` is a `Money` in the accounting currency's decimals, the currency a tax computed by a codex is
  billed in (§4.5), and GGG's rounding rule applies to `times()`.

### 5.7 A body beside the line

```python
# legislation/eu/es/vc/emshi/water.py
from ggg import TaxPolicy

class Water(above("water")):
    bulk_supplier = fixed(Ref("external", "EMSHI"), legislation="Llei 2/2001; EMSHI statutes (illustrative)")

    @fixed(legislation="EMSHI statutes (illustrative)")
    def on_tax_policy_propose(self, event):
        for entry in TaxPolicy.get(event.args["policy"]).taxes:     # the saved policy's entries
            if entry["code"] == "water_supply":
                self.ggg.notification_send(recipient=self.bulk_supplier, topic="tariff",
                                           title="Water tariff proposed", body=entry["code"])
```

València's water layer path goes through EMSHI, because València syncs from EMSHI and EMSHI from CV
(§3.3). A CV town that doesn't sync from EMSHI gets a water layer path without EMSHI.

### 5.8 The generated Cedar

From 5.6, inside València. `S.capital` and the other settings are folded to their values in the
realm's layer path. A forbid without `each` names GGG's operation. With `each`, it names the per-list
actions `<operation>.<list>`, here `tax_policy_propose.taxes` and, since the same forbids run at draft,
`tax_policy_draft.taxes` (§9.4). Cedar sees one request per list element on that action, with the
element's fields as facts. The numbers are `rate`'s integers in hundred-millionths: `pct("1.10")` is
1100000.

```
@id("eu/es/tax/ibi_urban_cap")
@legislation("TRLRHL Art. 72.3")
forbid (principal,
        action in [Realm::Action::"tax_policy_propose.taxes",
                   Realm::Action::"tax_policy_draft.taxes"],
        resource)
  when { context.facts.code == "ibi_urban"
         && context.facts has rate
         && context.facts.rate > 1100000
              + (if true then 70000 else 0)
              + (if true then 70000 else 0)
              + (if false then 60000 else 0) };
```

### 5.9 What a member sees: the flattened view

```
tax, as it applies in València (codex sha256:3f1c…), path Spain → València:
  capital, surface_public_transport = true          València   (Spain asks, València fills)
  ibi_rate_required     forbid                      Spain      TRLRHL Art. 72
  ibi_urban_range       forbid                      Spain      TRLRHL Art. 72.1, 72.3
  ibi_urban_cap         forbid                      Spain      TRLRHL Art. 72.3
                        for València: capital + transport → maximum 1.24 % (1240000)
  ibi_rustic_cap        forbid                      Spain      TRLRHL Art. 72.3
  compute_tax_assess    fixed                       Spain      TRLRHL Arts. 61, 65, 71, 74
  ibi_bonus             València                    (overrides Spain's 0)
```

---

## 6. The `F` expression language for forbids

`forbid(op, expression, each=None, legislation=...)`:

- **`op`** must be a GGG operation (`tax_policy_propose`). Its facts are the operation's args, as GGG
  defines them. A chapter may forbid any GGG operation except the reserved ones (§10.4), whatever its
  own domain.
- **`each`** names the list the forbid ranges over. The forbid is then evaluated once per element,
  with the element's fields as the facts (`F.code`), and Cedar sees one request per element. Without
  `each`, a forbid sees the whole args. For `tax_policy_propose`, whose args hold only a policy id,
  `each="taxes"` is the `taxes` list of the saved TaxPolicy that the id names, and the same forbids run
  when the draft is saved with `tax_policy_draft`.
  - **The Cedar action.** A forbid with `each` names the action `<operation>.<list>`, e.g.
    `tax_policy_propose.taxes`, and also `tax_policy_draft.taxes`, since the same forbids run at draft.
    That action's `facts` are the element's type. A forbid without `each` names the operation, whose
    `facts` are its args. A Cedar action has one context type, and the operation's own args (for
    `tax_policy_propose`, only the policy id) are not the element's fields, so the per-element requests
    need actions of their own.
- **`expression`** is built from `F` and `S`, and is true when the operation is forbidden.

| Write | Means | Cedar |
|---|---|---|
| `F.rate` | a fact of the operation | `context.facts.rate` |
| `S.capital` | a setting of the chapter's own domain, as it resolves in this realm | its value, as a literal |
| `pct("1.10")` | a rate, as the legislation writes it | its integer in hundred-millionths, `1100000` |
| `==  !=  <  <=  >  >=` | comparison | same |
| `+  -` | integer arithmetic | same |
| `a & b`, `a \| b`, `~a` | and, or, not | `&&`, `\|\|`, `!` |
| `F.x.between(a, b)` | `a <= x <= b` | `x >= a && x <= b` |
| `F.x.in_(["a", "b"])` | x is one of | `["a","b"].contains(x)` |
| `F.s.contains("a")` | set fact contains | `s.contains("a")` |
| `F.x.is_none()`, `F.x.is_some()` | optional fact absent or present | `!(context.facts has x)`, `context.facts has x` |
| `when(c, a, b=0)` | if c then a else b | `if c then a else b` |
| `F.p > months(6)` | a Period fact against a Period of the unit the rule names; another unit makes the comparison true (polarity below) | `context.facts.p.unit != "Month" \|\| context.facts.p.count > 6` |

**Periods compare only within one unit** (§4.3), and a forbid fails closed: a Period comparison is true,
in the forbid's sense (it counts towards the forbid firing), for a deadline in any other unit. A
deadline must therefore be written in the unit the rule uses, because days and months don't convert
cleanly: a month is not a fixed number of days, and a count of days in legislation is often a count of
working days. Art. 21.2 (§4) is in months, so `days(60)` is refused unless the call names the
legislation that allows a longer deadline, and València's default is `months(2)` (§5.1).

The generator writes a Period comparison by its polarity, the number of `~` above it. Under an even
number, it is the row above. Under an odd number, it writes `p.unit == "Month" && p.count > 6` and
keeps the `!` above it, so `~(F.p > months(6))` is `!(p.unit == "Month" && p.count > 6)`, that is,
`p.unit != "Month" || p.count <= 6`. The same holds under other facts:

```
~(F.d.is_some() & (F.d >= months(1)))
!(context.facts has d && (context.facts.d.unit == "Month" && context.facts.d.count >= 1))
```

A Period comparison is therefore true, in the forbid's sense, for every other unit, negated or not. The
Python evaluation follows the same rule (rule 7).

A Period comparison can't be the condition of `when()`: inside arithmetic it has no polarity, so another
unit could raise a cap.

The rules:

1. **Literals:** int, str, bool, and lists of those. No floats, since Cedar has none. A number is an
   integer in the scale GGG declares for the fact; the fact's name has no unit suffix. A rate is
   written `pct("...")`, and the SDK stores the integer in hundred-millionths.
2. **No plain `and`, `or`, `not`, `if`, or chained comparisons.** Python can't redefine them, so an
   expression refuses to become a bool and raises: "use &, | and ~ in a forbid; see
   legislation/README.md". The same error catches `pct("0.40") <= F.x <= pct("1.30")`.
3. **Optional facts:** an optional fact may only be compared after `is_some()` in the same `&` chain.
   Cedar's validator enforces this too.
4. **`S.<setting>` reads only a setting of the chapter's own domain,** even when another domain is in
   `uses`. The generated Cedar therefore differs per realm, and the flattened view shows which value was
   folded in, and from which layer.
5. **The output is canonical:** the same expression and the same settings always give byte-identical
   Cedar, with fixed parentheses and order. A laptop and the realm produce the same text.
6. **A forbid does not round.** It compares integers. Rounding happens only where money is computed
   from a rate (§4.5), by GGG's one rule.
7. **The same tree evaluates in plain Python** (`expr.evaluate(facts)`) for tests. CI checks that
   Python and Cedar give the same answer on generated facts, among them each literal ±1, 0 and the
   64-bit extremes: Python integers never overflow, so only such facts show where the two differ.
8. **Ids and annotations:** each forbid's Cedar `@id` is `"<source>/<domain>/<attribute>"`, plus a
   `@legislation("<citation>")` annotation.
9. **A forbid never errors.** Cedar skips a policy whose evaluation errors, so an overflowing forbid
   would allow. `check` proves it for every forbid (`never-errors`, §12.1), and the generator bounds
   arithmetic on facts by the fact schema's ranges (a value out of range makes the forbid fire).

**When a forbid, when a guard:** use a `forbid` whenever the rule fits the expression language, so
that it's analysed. Guards (Python methods) are for rules that need loops, other domains, or the
realm's state (`approved_by`). Hooks are for effects. `compute_` methods are for amounts GGG cannot
know.

---

## 7. Proposals and the change list

A proposal changes the codex through GGG's existing action:

```
Code { url = "<URL of the change list>"; checksum = "sha256:<sha256 of its exact bytes>" }
```

Realms GOS already maps `Code` to `codex_install` in
[`ggg_api.py`](../../src/realm_backend/ggg_api.py).

A proposal may state `effective_at`, the exact time it takes effect (UTC ns, optional; GGG's field,
[ggg#6](https://github.com/smart-social-contracts/ggg/issues/6)). Once accepted, its action executes
at that time; without it, on acceptance. For an install, that is the switch time (§9.2 step 6). Once
accepted, `effective_at` never moves: if it proves too early, another proposal stops the scheduled
one, and a new proposal sets the right date.

The change list:

```json
{
  "kind": "realms-gos/legislation-change",
  "format": 1,
  "realm": "eu/es/vc/46250-valencia",
  "replaces": "sha256:<checksum of the realm's current codex>",
  "set": [
    {
      "chapter": "eu/es/vc/46250-valencia/tax",
      "url": "https://raw.githubusercontent.com/smart-social-contracts/realms-codices/<commit>/legislation/eu/es/vc/46250-valencia/tax.py",
      "mirrors": [],
      "sha256": "<hex>",
      "size": 412
    }
  ],
  "remove": [],
  "release": null,
  "report": {"url": "https://.../report.md", "sha256": "<hex>"},
  "summary": {"ca-valencia": "IBI 2027: bonificació per família nombrosa", "es": "IBI 2027: bonificación por familia numerosa"}
}
```

| Field | Rule |
|---|---|
| `realm` | must equal the realm's own source id |
| `replaces` | must equal the current codex's checksum; otherwise the install is refused as stale |
| `set[].chapter` | `<source id>/<domain>`. The source must be in one of the realm's layer paths (§3.3); the domain must be in the GGG list. A calendar is set the same way, under the id `<source id>/calendar`, e.g. `eu/es/calendar` |
| `set[].url`, `mirrors` | https only. The first URL whose bytes match `sha256` and `size` wins. For a layer from above, the bytes may instead come from the sync source's capital (§9.2) |
| `remove` | chapters to empty. Allowed only for the realm's own layer. A chapter is not both in `set` and in `remove` |
| `release` | for legislation from above: `{"source": "eu/es", "version": "2.4.0"}`. `set` must then contain exactly that release's chapters on the realm's layer paths, and its calendar (§3.2) |
| `report` | optional analysis report (§12.3). The UI shows it, labelled as not checked by the realm |
| `summary` | shown in the proposal: the text of the summary per language, `{"es": "..."}`, or left out |

The realm reads the list strictly: a JSON object that repeats a key is refused (a tool that kept the first value
would show voters something other than what the realm installs, which keeps the last).

Limits:

- change list ≤ 64 KB;
- each chapter ≤ 256 KB;
- ≤ 50 chapters per change list;
- ≤ 4 mirrors per chapter: every URL the realm tries is an outcall it pays for.

These all fit within one HTTPS outcall each (2 MB maximum). A canister-to-canister fetch from a sync
source has no 2 MB limit; the 256 KB chapter limit stays.

**A proposal's change list never changes.** The proposal pins one checksum for good. To change the
list, the proposer withdraws the proposal and creates a new one. Proposals link to each other: the new
one "replaces" the withdrawn one, or "relates to" another (GGG proposal links,
[ggg#4](https://github.com/smart-social-contracts/ggg/issues/4)). The change list's own `replaces`
field names a codex checksum, not a proposal.

Change lists may live anywhere public. By convention they live in realms-codices under
`changes/<realm source id>/`, generated by `realms legislation propose` (§12).

**The town's own layer:** someone from the community edits the chapter in realms-codices (a PR, or
any commit or fork, since the checksum pins the bytes). They make the change list, then submit the
proposal. The realm's own voting rules apply. Merging the PR is not required for the proposal, but
should follow once it passes, so `main` shows what is in force.

---

## 8. The public record

The realm needs, from outside its own state:

| What | Used for | Source in this design |
|---|---|---|
| the realm's links | layer paths | the realm's own GGG state: its sync sources, each active after both votes |
| published releases of each sync source, and of the layers it relays | checking layers from above; compliance | the sync source's published record (below) |
| world facts (`capital`, `population`, ...) | `S.` in forbids, and chapters | the realm's own layer: settings (§4.3). No record is read |
| the time zones | showing the realm's official time (the capital's zone), with a quarter's local time beside it when they differ, in the UI; the local day for the official journal's issues (the capital's) and for working-day counts (the zone of the matter's clock, §4.5) | the canister's own `Realm.timezone`: the realm's, set by the realm; a quarter's, copied from its capital when it joins while still on `UTC`, then changed by the quarter or by a realm-wide vote. Never taken from a layer, another realm or a sync source. The tz data ships with the realm release, as hex text that `bytes.fromhex` decodes, and a canister reads only its own zone with Realms GOS's pure-Python reader |

GGG and the registry hold no national codes (INE, BFS, ISO). A gateway that needs them reads a Realms
GOS extension.

**The published record of a source:** a query on the source's realm,
`legislation_releases() -> vec Release`, returning the same entries as `releases.json`. A release
becomes official only when the source publishes it by its own vote. A sync source's record also lists
the releases of every layer it relays, as it installed them. The record is what followers check
against; the official journal (§9.6) is where people read the same releases.

**What the resolver takes.** The library function `realms.legislation.plan(realm, relays, own)` (the
install task's step 5, `realms legislation resolve`) computes the layer paths from plain data. Each
chosen sync source, one with an active link, is its record as the releases it runs, in layer order:

```json
{"source": "eu/es/vc",
 "layers": [
   {"source": "eu",       "version": "1.0.0", "chapters": {"data_protection": "sha256:<hex>"}, "calendar": null},
   {"source": "eu/es",    "version": "2.4.0", "chapters": {"tax": "sha256:<hex>", "procedure": "sha256:<hex>"}, "calendar": "sha256:<hex>"},
   {"source": "eu/es/vc", "version": "1.1.0", "chapters": {"local_regime": "sha256:<hex>"}, "calendar": "sha256:<hex>"}
 ]}
```

- `layers` runs **top to bottom**: the layers the sync source relays, its own layer last. An entry is a
  release as in `releases.json` (§3.2) with its `source` added. The order is the source's own layer
  path; a source id does not place it (§3).
- A layer is on a domain's path only if its entry has a chapter in that domain.
- Taken together, the records must put every layer with a chapter or a calendar above or below every
  other: one order, no ties. A record may order two layers through a third (`[a, b]` and `[b, c]` give
  `a, b, c`), but records that contradict each other, or leave two layers with neither above the
  other, are refused.
- A relay's `source` is not the realm itself, and a record that lists its own layer lists it last.
- The realm's own layer is passed beside them, without a version (§3.2):
  `{"chapters": {domain: sha256}, "calendar": sha256}`.
- Two sync sources that relay one layer must relay the same release, with the same version and
  checksums. Otherwise `plan` refuses (§9.2 step 4).
- `plan` returns the layer paths, the chapters to fetch (`source`, `domain`, `version`, `sha256`, in the
  order they load), the calendars and the realm chapters it skipped (§3.2). `resolve` loads them into a
  codex and gives the flattened view of §5.9 as plain data.

A follower checks each layer from above against one record:

- **its publisher's**, when the follower syncs from the publisher directly;
- **otherwise the record of the sync source that relays it.** València, syncing from CV alone, takes
  Spain's layer as CV's record lists it, and trusts CV for it. A follower that does not want to depend
  on CV for Spain's layer also syncs from Spain.

While a source has no realm (the real EU and Spain don't run Realms GOS), it has a stand-in record at
a public URL. It cannot vote to accept anyone, so a realm that syncs from it relies on its own vote
alone: Spain's members pin the EU's stand-in record.

Layer paths are computed at install time and **stored in the codex**, with each layer's source,
version, checksum, the record that vouched for it, the route and the fetch time. At run time the realm
never needs the record, except to check compliance (§11).

**How the realm keeps its links and its record** ([`api/links.py`](../../src/realm_backend/api/links.py)):

- A link is a row on each side, made by that side's vote: `SyncSource` on the follower (a realm by its
  capital, a stand-in record by its url), `Follower` on the source. Both are `requested` until the
  other side's vote is in, then `active`; a stand-in record is `active` on the follower's vote alone.
  Either side ends it (`ended`, with who and when), and an ended link is replaced by a new pair of votes.
  The follower keeps the layers it installed.
- The side whose vote changes a link tells the other side's capital with `link_notify` and reads the
  answer, `active` meaning that their vote is in. A notice nobody answered (the call is rejected) is kept
  as `unsent` and sent again every minute, also after an upgrade; a refusal is an answer. A vote still
  `checking` when the realm starts has no walk left (none outlives an upgrade): it ends, by this realm, and
  the other side is told, so that its key is not locked for good. Only the
  capital at the other side of a link this realm has voted on may send `link_notify` and
  `follower_report` (permission `link.sync`); a quarter links to no realm and makes no call to one.
- A vote refuses a cycle: it reads the `sync_sources` of `realm_info` along the sources of the other
  realm, at most 32 realms, and a realm it cannot read fails the vote with `Unavailable`. The link is
  recorded before the walk, marked `checking` while the walk is out (the other side's notice is refused
  then, as for a vote not cast), and undone if the walk refuses it. A realm that is already a follower
  counts as taking legislation from this one. The walk runs when the vote applies, not when a proposal that
  carries it is filed: filing makes no call to another realm.
- Calls between realms are **not bounded-wait**, which the model asks for: basilisk 0.15 has no call with
  a timeout, so a canister that never answers holds the vote's message open and blocks stopping the realm
  for an upgrade. A rejected call (a stopped realm) is handled, and a vote should name only realms its
  members trust.
- A follower's report is kept on its `Follower` row as the follower's own claim, with its time. The
  `follower_compliance` fact of a transfer to `Follower:<capital>` and of a quarter's report is the
  compliance last reported, `Unknown` for any other recipient or a link that is not active.
- The record is `Release` rows: the realm's own, published by `legislation_release_publish` (every
  checksum already in `ChapterSource`, one source id, a version once, never edited), and the releases of
  the layers it runs from above, which the install task keeps with `links.relay_release` as the record that
  vouches for them lists them. `legislation_releases` answers the relayed ones top to bottom, then its own,
  oldest first; `legislation_chapter` answers the text stored under a checksum. A release is kept under
  `<source>@<version>`, so neither holds an `@`.
- `Quarter.compliance` is stored and read like a follower's; nothing writes it yet (the scheduled checks,
  D7, and the switch of a codex across the quarters will).

---

## 9. The realm canister

### 9.1 Codex storage

New entities, beside today's `Codex` ([`api/codex.py`](../../src/realm_backend/api/codex.py)):

```
LayeredCodex { id; layers; layer_paths; chapters: {chapter id → {source, version, sha256, vouched_by, route, fetched_at, url, size}};
           previous (codex id); status: pending | active | inactive | reverted; activates_at; installed_at; proposal }
ChapterSource { sha256 (key); source text }            the downloaded bytes, stored once
InstallJob { id; proposal; change_list {url, checksum}; step; staged; status: running | paused | failed | done; error; started_at; finished_at }
```

`pending` is a codex installed but not yet in force (GGG's `Pending` codex status, §13);
`activates_at` is its switch time, the adopting proposal's `effective_at`.

`layers` is every source with a chapter or a calendar, top to bottom (`plan`'s `layers`, §8), and
`layer_paths` each domain's path (`plan`'s `paths`). Both are stored: the paths alone do not place a
layer that has only a calendar, or two layers that share no domain, and `resolve` needs the one order
(§3.3) to load the codex again after an upgrade.

**The id** is `sha256:` and the hex sha256 of the canonical JSON of
`{"layers": ..., "layer_paths": ..., "chapters": {chapter id → sha256}}`. Canonical JSON means sorted
keys, no spaces and UTF-8. The id names the legislation and nothing else: a chapter's version, who
vouched for it, the route, the fetch time and the URL, and `previous`, `status`, `activates_at`,
`installed_at` and `proposal`, are this realm's own history and stay out of it. So a capital and each of
its quarters, which fetch the texts themselves, at their own time and by their own route (§9.5), have
the same id; it does not change when the codex goes from `pending` to `active`; and it is what `replaces`
refers to and what calls between quarters carry.

A `ChapterSource` is keyed by `sha256:` and the hex sha256 of its text's UTF-8 bytes, the form the
records of §8 use for a chapter. A text above 256 KB, or one whose checksum is not its key, is refused.
`previous` is a codex id of either kind (`name@version` for a legacy codex, `sha256:…` for a layered
one), so the history of §10.5 runs through both.

**The legacy path stays:** `codex_install` with `manifest` and `source`, or with `codex` and `version`
from the file registry, keeps working for agora, dominion and syntropia. A change-list URL selects the
new path. A realm runs either a legacy codex or a layered codex, never both.

### 9.2 The install task

`codex_install` with a change-list URL starts an `InstallJob` and returns. A timer then runs one step
per message:

1. **Fetch the change list** by HTTPS outcall (at most 64 KB, https only), from the first URL whose sha256
   matches the proposal's checksum. The realm exports the `http_transform` query the outcall names, which
   drops the reply's headers so that every node sees the same reply.
2. **Check the change list:** `realm`, `replaces`, chapter ids, the release rule, and limits. `realm` is the
   realm's own source id: the first layered install sets it from the change list (the vote approved it), every
   later one must name the same, and an id that is a layer of one of its sync sources is refused. `replaces`
   is the active codex's id as `codex_status` shows it (a layered codex's checksum, a legacy codex's
   `name@version`), or null when none is active. A chapter from a layer above comes only as a release of
   that layer, whole.
3. **Fetch each chapter,** one or a few per message, by the route available: a canister call to the
   sync source's capital, or an HTTPS outcall to a URL. Check size and sha256, then store it in
   `ChapterSource`. The realm skips the download if `ChapterSource` already has that sha256.
4. **Check sources:**
   - each layer from above must match the record that vouches for it: its publisher's, when the realm
     syncs from the publisher directly, otherwise the relaying sync source's, whichever route the bytes
     came by; a copy that does not match is refused;
   - a release install must match one release exactly and be whole;
   - if two sync sources relay different releases of the same layer, the follower's own legislation
     decides which one it installs. **If its legislation does not choose, the install is refused and
     the old codex keeps running.**
5. **Build:**
   - compute the new layer paths from the paths the sync sources relay, then the realm's own layer
     (`plan`, shape in §8);
   - refuse paths that do not form one chain (§3.3);
   - load every chapter of the new codex into a fresh sandbox, top to bottom, binding `above()`
     (`resolve`), and refuse `uses` cycles and chains deeper than 8 (§4.4);
   - run the in-canister checks (§12.2), including every setting's `check=`;
   - generate the Cedar, folding the `S.` settings;
   - parse and validate it against the schema in strict mode (the canister's Cedar engine has the
     validator).
6. **Switch:** the switch time is the adopting proposal's `effective_at`; without one, the switch
   happens on acceptance. Until then the new codex is `pending`, with steps 1 to 5 done ahead of the
   time, as a quarter stages ahead of T (§9.5): acceptance starts the job, which runs steps 1 to 5 and
   leaves the codex `pending` with `activates_at` = `effective_at`; the proposal's scheduled execution
   at that time is the switch. If the job is not done by then, the switch fails, the proposal ends
   `failed` and the old codex keeps running (proposers leave a margin, rule 26). A proposal stopped with
   `proposal_stop` discards its pending codex and its staging. Without `effective_at` the job runs on
   acceptance and switches as soon as step 5 is done. At the switch, in one message, set the new codex
   active and keep the previous one; the realm loads the parsed policy set and the realm domain's
   `on_codex_activate` hook runs once per realm, not in each quarter and not on a revert. The hook runs in
   the message after the switch, so a hook that fails, or traps after a call it made, never fails the
   switch, the proposal's execution or the install step that made it.
   **Legislation applies when the proposal passes and its changes are executed,** which is at that
   time. No chapter calls `codex_activate`, and no publication, in the official journal (§9.6) or in an
   outside gazette, plays a role in when the legislation applies.

The job's other rules:

- **Failure:** at any step, the job records the reason, the staging is discarded, and the codex is
  unchanged. Executing the same proposal again is safe.
- **Safe to run twice:** every step can run again after a canister upgrade. Staging is keyed by
  checksum, and the switch checks the active codex first. A job caught mid-way by an upgrade stays
  paused until an admin of the realm resumes it by hand with `codex_install_resume { job }` (permission
  `codex.install`, not governed: it only re-runs the step the job stopped in and cannot change what the
  vote approved); the step it stopped in then runs again.
- **One job at a time:** a second install while one runs is refused with `Conflict("an install is
  running")`, the legacy path included. A staged install holds that place until its switch time; stopping its
  proposal frees it. A revert is not an install and stays allowed.
- **Timeout:** a job not finished within 24 hours fails.
- **Status:** `ggg_codex_status` shows the job, its step and its error.

### 9.3 The runtime

Built: `core/legislation.py` (the realm's side) and `core/legislation_sandbox.py` (the program that runs inside
the codex subinterpreter), on the library of §12 ([#488](https://github.com/smart-social-contracts/realms-gos/issues/488)).

- **Loading:** after every canister upgrade (`post_upgrade`, where the realm starts), or on first use, the realm
  loads the active codex's chapters from `ChapterSource` into **one codex subinterpreter kept across calls**
  (`core/sandbox.py`'s `Resident`), never into its own interpreter: the subinterpreter imports the library from
  the sources the realm bundles, builds the `Codex`, loads the chapters top to bottom and runs the checks of
  §12.2, then answers each call. A query cannot keep what it loads, so the load happens where the realm starts.
  Cedar is parsed once per activation (§9.4). That matches today's lazy-load pattern in
  [`core/auth.py`](../../src/realm_backend/core/auth.py).
- **Methods:** around a GGG operation `op`, the realm runs the guards `check_<op>` before it, the
  `compute_<op>` methods during it when GGG asks, and the hooks `on_<op>` after it succeeds. Each is answered by
  the resolved class of its domain, applying the marks (§4.1). Every domain whose chapter defines the guard or
  the hook runs, by domain name (the design orders domains no other way); a `compute_` is answered by one
  domain, and two domains defining it is an error. Guards get the operation's arguments as `args` (attribute
  access, `args.basis.legal_basis`; an argument the call did not give reads None); a guard returns None or
  `self.deny(reason)` and queues no effect. Hooks get GGG's `Event`, one per domain, shared down the layers of
  that domain. Read operations run no legislation, and nor do the effects a chapter queues (origin `codex`):
  a hook's effect never runs another guard or hook, so chapters cannot cascade. A governed call that becomes a
  proposal is a `proposal_create` to the legislation: its guards and hooks run on the filing. What a chapter
  reads of GGG's entities is an allowlist of entities and fields (`VIEWS` in `core/legislation.py`): what
  legislation needs to decide, never a key, a code, a person's private data, a sealed bid, who voted what, a
  grant, a payment account or what one member was told. So a notification shows its recipient, topic, sender and
  time and not its title; a request shows when it was filed, is due and was decided, and not its body, reply or
  decision (free text); a dispatch shows its recipient, topic and status and not its body; a procedure and a loan
  show no `facts`; a document shows its title only when it is public, and a dispatch only when it carries a
  document (only the publication of a document does); and a chapter sees the zones the realm defined and never a member's home cell, which is as
  private as `Human.h3_index` (a chapter reads a member's district as `User.home_zone`, the id of a zone the realm
  defined). The arguments and results it gets carry no key material or credential (`secret=` on the operation: a
  code, keys, private data, a request's body and reply, the home cell `zone_set` and `zone_clear` take), and neither
  do the replies of its effects or the call a proposal carries and the result of running it, nor any call that an
  operation carries into a guard or a hook (the steps of a task, what `call_for` forwards, a pending action's card),
  while the personal data the audit log keeps out (`private=`) still reaches the legislation that decides on it
  (I-PRV-1); and no read is answered while the codex loads.
- **Failures:**
  - a hook that raises never fails the caller; it marks the codex unhealthy, as today, and the other domains'
    hooks still run;
  - a guard that raises **denies** (fail closed), with "legislation error in <chapter>: <error>"; so does a
    codex that does not load, for every operation its legislation could guard, until a revert;
  - calls between domains stop at depth 8; the install refuses a `uses` chain that could reach it;
  - a chapter that exhausts the sandbox's instruction budget fails that call; the realm closes the spent
    interpreter, loads the codex again and asks once more, so the calls before it never cost a later one its
    answer. The reads of one call are metered on the realm's side too: past 2 B instructions they are refused
    and the call fails, so a chapter that loops on reads costs the caller that much and never the whole message.
    The hooks of an operation run one domain per call, so a domain that loops costs no other domain its
    effects;
  - a failure on a query path (`compute_realm_join`, asked by `join_targets`) is not kept: a query keeps no
    writes.
- **Making "unhealthy" visible.** `LayeredCodex.failures` holds, per chapter file and method, the failure
  count, the last error and when; `ggg_codex_status` shows it as `failures`, and `healthy` is false while it is
  not empty. The flag clears when that method next succeeds. There is no automatic revert. Still to add (D5, D8):
  - **a notification** to the IT admin team (§10) on the first failure, and again after 24 hours if it
    still fails;
  - **on the codex page,** the failing chapters and methods, and for IT team members a "revert"
    button that starts `codex.revert_fast`.

  No banner for members.
- **Across domains:** what crosses a domain boundary is plain data, frozen on the way (a list is a tuple, a
  dict read-only), or the called chapter seen through its public view (its public members only, never the `self`
  API): a method that returns `self` hands back the view, a bound method or a view of other domains is refused
  ([#543](https://github.com/smart-social-contracts/realms-gos/issues/543),
  [#621](https://github.com/smart-social-contracts/realms-gos/issues/621)).
- **Effects** go through the codex caller as a legacy codex's do: origin `codex`, the realm as signer, Cedar's
  guardrails. A layered codex declares no capability list: the GGG page (§1, "Effects are operations") has a
  hook queue GGG operations "which the GOS runs as the realm, under the same permissions as any caller", and
  rule 9 of §2 and the `self` API of §4.5 put no catalogue between a chapter and GGG, so the operation an effect
  names is the one declared for it. Plainly: **a chapter can do, as the realm, anything the realm itself may do
  directly** through a GGG operation, and nothing that takes a permission it does not hold: a layered codex holds
  none, so an effect hands out no permission (I-ID-3) and no invite to a profile that carries one (a legacy
  codex's effects keep acting for the realm as before, marked as such by the legacy hook path alone). **A governed
  operation it queues is never run, whatever the department's policy: it becomes a proposal the realm files**,
  decided by the vote that operation needs. Effects apply in order and stop at the first that fails, and
  whatever one fails with is the chapter's failure, never the caller's; the operation's own audit event precedes
  theirs. Refused before they run and counted against the chapter: an effect that names a reserved operation
  (§10.4), one about a proposal or a federal vote whose action is reserved (a stop, a withdrawal, a vote, a
  quarter's report of its leg), and one that carries a `timezone` (§4.5). The security review of #512 and #544
  decides whether chapters get capabilities of their own, and reviews the allowlist of what they read.
- **Facts:** the facts of a forbid are the operation's args, as GGG supplies them. The settings it
  reads with `S.` were folded in at build.
- **Costs**, measured on PocketIC (basilisk 0.15.1, the realm's production wasm, 8 November 2026):

  | What | Instructions |
  |---|---|
  | load: spawn the subinterpreter with the program and the library's sources | 379 M |
  | load: import the library from text inside it | 556 M |
  | load: 9 chapters loaded (the readable-subset check, the marks) and the checks of §12.2 | 64 M |
  | one `activate`: the `on_codex_activate` hook and its one effect, a notification | 50 M |
  | one guard call, 3 domains (the round trip, before the realm acts on the answer) | 2.5 M |
  | the hooks of one operation, 2 domains, one call each, 2 effects queued (before the realm applies them) | 7.2 M |
  | one `build` of the example: 7 chapters, a calendar, the flattened views | 1,045 M |

  A load, about 1 B in all, is paid once per activation and once per upgrade (in `post_upgrade`, where the
  realm starts), never in a query; the library's import from text is most of it. A codex that holds forbids adds
  about 0.5 B: the Cedar generated in the sandbox and parsed by the realm's engine (§9.4). A guard or a hook costs
  about as much as a small database read.

### 9.4 Cedar changes

In [`core/auth.py`](../../src/realm_backend/core/auth.py) and [`core/forbids.py`](../../src/realm_backend/core/forbids.py)
(see [Authorization](../architecture/authorization.md)):

1. **One Cedar action per GGG operation,** named as the operation, e.g.
   `Realm::Action::"tax_policy_propose"`, each `in [Realm::Action::"call"]`. Each action's Cedar
   context includes `facts`, typed from the operation's args. A forbid with `each` names the action
   `<operation>.<list>`, e.g. `tax_policy_propose.taxes`, and also `tax_policy_draft.taxes`, since the
   same forbids run at draft. Like the others, those actions are `in [Realm::Action::"call"]`; their
   `facts` are the element's type, and one request is made per element. A forbid without `each` names
   the operation, whose `facts` are its args (§6).
   - **The fact schema** is GGG's `facts.json`, which the realm bundles as
     [`ggg_facts.py`](../../src/realm_backend/ggg_facts.py), generated by `scripts/ggg_candid.py` (CI checks it is
     current). It keeps the facts Cedar can compare: text (a principal, a variant's tag, with the words GGG allows
     as `values`, which the realm does not use and `realms legislation diff` assumes, §12.3), whole numbers with
     their ranges, Booleans, Periods, sets of text, and records, which a forbid can only test for (`F.basis.is_none()`,
     §5.4). A `Value`, a blob or a list of records is not a fact, so a rule on one is a guard. What an operation
     keeps from the legislation (`secret=`: an invite code, a request's reply, keys) is no fact either, and no list of
     it is one: a forbid that names it is refused when the codex is built.
   - **The schema the realm loads declares `call` and the actions its forbids name,** not the 254 of every operation:
     that schema is 101 KB, which costs 5.4 B instructions for the engine to parse, against 94 M for the one that is
     loaded (measured below). `realms legislation check` (§12.1) will import the same fact schema and validate against
     all of them.
2. **Today's `POLICIES`** use `action == Realm::Action::"call"`; they become
   `action in Realm::Action::"call"`. `GUARDRAILS` (`principal, action, resource`) are unchanged.
   `context.op` stays for compatibility.
3. **Load order:** `GUARDRAILS + POLICIES + generated forbids of every chapter`, parsed once per codex
   activation. The generated rules name their action in the head (the operation, or
   `<operation>.<list>`), so a decision only evaluates rules for that action.
   - The Cedar is generated in the codex sandbox, which hands over the policy text and nothing else. The realm builds
     the schema and the fact types itself, from its own copy of GGG's fact schema (`ggg_facts`, less what operations
     keep secret) and the context of `core.auth`, for the actions the policies name, with the library's schema writer;
     a sandbox that said otherwise would change nothing. The realm's engine then parses and validates the policies
     strictly against that schema (`core.auth.use_forbids`), and every decision after that evaluates what it holds,
     with no parse. `load` does it at the switch, so a codex
     whose Cedar does not validate raises before the switch; `warm` does it at the realm's start, and the same
     forbids again (a reload after the sandbox spent its budget) are not parsed again. Step 5 of the install (`build`)
     validates the same Cedar and puts the running policies back, so an install that would not validate fails there.
     A codex that forbids nothing leaves the realm's own policies in force, and a legacy codex or none loads what it
     loads today.
   - **The realm loads forbids from the sandbox and nothing else.** It accepts the text only as the grammar the
     generator writes (`core.forbids.only_forbids` and `targets`), and no more: outside strings only words, spaces,
     line breaks and Cedar's punctuation, so no comment of either kind (a `//` comment ends at `\r` as at `\n`, and a
     scan that missed that let a `permit` through), no non-ASCII; inside strings no control character; no `permit`
     word; each policy a forbid whose scope is exactly what the generator writes for the actions it names, with the
     principal and the resource unconstrained, so no forbid of one person, and a condition that never uses either
     word (the generator cannot write them); and the operation of each action one of the
     realm's own (`ops()`), never `call` (a forbid of which would refuse every permission decision) and never a
     reserved one, whatever the sandbox says. A smuggled `permit` fails the switch, the realm's start and install
     step 5. The engine's API does not yet list the effect and scope of a policy; when it does, the engine will check
     the same (realms-gos#611, smart-social-contracts/basilisk#64).
   - **No generated forbid may target a reserved operation (§10.4).** The library refuses it when the chapter loads;
     the realm checks the policies it is about to load as well (`core.forbids.targets`), and refuses a policy that
     names an action without facts or no action at all (that would forbid every operation). And no forbid is asked of
     a reserved operation, nor of the `proposal_create`, the vote or the execution of a proposal about one.
   - **A codex that does not load still refuses.** Its forbids cannot be read then, but each successful load keeps the
     operations they name on the codex row (`LayeredCodex.forbids`). Until the codex is reverted, a call of one of
     them is refused, a read as well as a write: what the forbids would have judged is not let through unjudged. The
     operations no forbid names are read as before, and a revert is a reserved operation.
4. **The requests of a call.** The permission decision is what it was: one request on `call`, before anything else. A
   call whose operation a generated forbid names then makes, under the context `origin: "forbids"` (nothing in it but
   the operation and its facts; one `permit` for that origin is loaded with the forbids, so that only a forbid can
   refuse it, and never for `call` itself, the permission decision): one request on the operation's own action, if a forbid names it, with its args in GGG's form
   (`ggg_api.ggg_args`: renamed arguments, nanoseconds, variants), the principal the call acts for (`on_behalf_of`,
   when the call names someone other than its signer, so that a rule can refuse a delegate's vote) and the facts the
   operation derives (`derived=` on
   `@op`), and one request per element for each list a forbid ranges over: the element of the argument, or, where the
   list is not an argument, what `lists=` on `@op` gives (`tax_policy_propose`: the saved policy's entries). A list of
   more than 500 elements is refused with `invalid_argument` where it is given, at draft and at propose, so that no
   replay is asked for more. Any refusal is a `Denied` that names the list and the position of the element, and the
   forbids that fired: `taxes[1]: refused by eu/es/tax/ibi_urban_cap (TRLRHL Art. 72.3)`. The engine says only allow
   or deny, so the names are found after the refusal, by asking the sandbox, whose bound forbids evaluate the same
   facts in plain Python as Cedar does (§6, rule 7); an allowed call never pays for it, and a sandbox that cannot
   answer, or answers anything but a short list of ids and citations, leaves the plain reason. An operation no forbid
   names is decided once, on `call`, as before.
5. **A proposal is asked when it is filed.** A governed call that becomes a proposal is asked as `proposal_create`
   before it is filed, as well as under its own operation; `proposal_create` and `federal_vote_propose` ask the
   forbids of the action they carry. A proposal that could only fail is never put to the vote, and the records its
   action would fix (a budget's draft lines) stay as they were. A call a delegate prepares for the member's approval
   (a pending action) is asked when it is prepared, and so is the action a prepared `proposal_create` or
   `federal_vote_propose` carries, so a forbidden action is never prepared; it is asked again when the member approves
   it. Both name the member as `on_behalf_of`.
6. **The replay of an accepted proposal** meets the same forbids, with no permit or guardrail in the way: the vote
   was the permission, but the forbids of the legislation in force when it executes still hold. The facts it
   derives are computed for that proposal (`derived(args, proposal)`: a budget's totals are those of the lines
   the proposal fixed). A replay a forbid refuses ends the proposal `failed` with the forbid's reason, and
   `proposals.end` releases what it fixed. An operation no forbid names asks Cedar nothing at replay.

Costs, measured on PocketIC (basilisk 0.15.1, the realm's production wasm, 9 November 2026), with the forbids of §5.6
(three policies on two actions, 1.2 KB of Cedar and 1.3 KB of schema); the design had estimated parsing at about 20 M
instructions and a decision at about 0.7 M:

| What | Instructions |
|---|---|
| engine: parse and validate the realm's policies, the schema and the forbids, once per activation | 94 M |
| the same with the schema of every operation (101 KB), which the realm does not load | 5,435 M |
| the sandbox generates the Cedar and hands over the policy text: mostly reading GGG's fact schema (compile and its actions, about 175 M) and importing the generator (about 230 M) | 437 M |
| the realm builds the schema and the fact types of the actions the forbids name, in its own interpreter | 2.5 M |
| the same the first time since the realm last started or was upgraded: it compiles the library's Cedar module (497 M) and reads GGG's fact schema (194 M) and the schema writer's other modules (239 M) | 930 M, once |
| the same for a codex that holds no forbid | 1 M |
| `cedar()` in the sandbox, of its 10 M bytecode budget per spawn (shared with every guard and hook after it): the example above / the whole València corpus (14 chapters, 6 forbids) | 0.27 M / 0.85 M |
| a permission decision, one request on `call`: no layered codex / forbids in force | 3.5 M / 3.8 M |
| the forbids of one call: one request on the operation's action, or per list element | 4.1 M each |
| the forbids of a draft of 1 / 2 / 5 / 20 entries | 4.4 / 8.4 / 20.5 / 81.2 M |
| the forbids of a proposal of a saved policy of 2 entries (it reads the policy too) | 9.7 M |
| the forbids of a realm with no layered codex | 9 k |
| naming the forbids behind a refusal (one call to the sandbox), only when a request is refused | 0.9 M |
| install step 5 (`build`) of a codex with these forbids, in all: the first in a heap / after that (its 930 M is once) | 2,669 M / about 1,740 M |
| a codex with these forbids, loaded and activated: the sandbox 1,116 M, the Cedar and the realm's schema 442 M, the engine 95 M, in all (the sandbox of a codex without forbids: 1,103 M). The first load since the realm last started or was upgraded also pays the 930 M above: about 2.6 B | 1,658 M |

The engine does not parse again: the forbids of a draft of 5 entries cost 20.5 M each time, against 94 M for one
parse. A request costs about 4 M, not 0.7 M: that is the whole crossing into the engine, with the request and its
entities validated against the schema. The cost of a call's forbids grows with the elements it carries (4.1 M each),
so the most a call can ask, 500 elements, is about 2 B; an update call has 40 B. All of it is small next to an update
call's base fee of about 5 M cycles.

### 9.5 Updating a realm's own quarters

A realm's quarters ([Quarters and federation](../architecture/quarters-and-federation.md)) run its
codex, and a new codex reaches them in four steps:

1. **One realm-wide vote** adopts the new codex. The switch time T is the proposal's `effective_at`,
   or the acceptance when it has none.
2. **Each quarter stages at its own pace:** it downloads the change list and chapters from its capital
   and checks every byte (§9.2, steps 1 to 5). Nothing changes while it stages.
3. **At T, each quarter switches by itself,** from a persisted schedule that survives canister upgrades
   (`tasks.rearm()` re-arms it). A task caught mid-way by an upgrade stays paused until an admin of the
   realm resumes it by hand; it never resumes by itself, and the step it stopped in runs again, which
   every step is safe for. The realm domain's `on_codex_activate` runs once per realm, not in each
   quarter.
4. **A quarter not ready by T** keeps the old codex and is flagged non-compliant, the same flag as a
   follower's. The realm's codex decides what follows. The legs and moves of a non-compliant quarter
   are decided by the legislation of the realms involved, not by GGG: GGG accepts the legs and records
   on each its codex checksum and compliance.

The switch cannot be atomic across canisters. During it, calls between quarters carry the sender's
codex checksum, so a receiver can tell which legislation the sender ran.

**Quarters in different time zones.** T is an exact UTC instant, so every quarter switches at the same
moment, whatever its zone (§4.5). The vote's deadline, and any working days counted up to T, belong to
a realm-wide vote and are counted on the capital's clock. The UI shows T in the realm's official time,
with the quarter's local time beside it when they differ. A quarter that changes its zone, or has it
changed, moves no T.

### 9.6 The official journal (extension `journal`)

Every realm has one official journal, a new core extension `journal`, installed by default like the
other core extensions of [`core-extensions.json`](../../core-extensions.json)
([realms-extensions#54](https://github.com/smart-social-contracts/realms-extensions/issues/54)):

- **One official journal per realm.** It is a report of the latest news and changes, nothing else.
  Its entries include every release of the realm's sync sources, as the scheduled check finds them
  (§11.2), and the realm's own releases when it is a sync source itself. A federation has one journal,
  the capital's. The journal on a quarter issues nothing and shows the capital's.
- **One issue every day,** even on a day with no entries. The day is the realm's local day on the
  capital's clock (the realm's official time), computed in Realms GOS from the capital's own zone
  (§4.5). A quarter's zone plays no part in it.
- **The canister serves the JSON, which is the authority.** The PDF is a browser print route with the
  realm's logo; it is never checksummed.
- **Personal-data rules live in the extension's config,** not in a chapter.
- **The official journal plays no role in when legislation applies.** Legislation applies when the
  proposal passes and its changes are executed (§9.2 step 6). No publication gates, in the official
  journal or in an outside gazette such as a provincial bulletin: a dispatch to such a gazette is a
  plain notice to an `External` body, and a statute's condition that the text be published there
  before it enters into force is not represented.

No chapter publishes to the journal or waits for a publication: a codex install appears in the realm's
journal by the extension's own rule, and the statutory waits a chapter needs are expressed through the
proposal's `effective_at` (§5.2).

---

## 10. Revert floor and the IT admin team

1. **Member revert:** `codex.revert` (it exists, `governed=realm_wide`) can restore **any** earlier
   codex in the history. The proposal's voting rules are the governance settings of **the codex being
   restored**, so a bad codex can't change the rules for undoing itself.
2. **IT admin team:** the realm admins define who it is and how many of them must approve. This is
   **left open on purpose:** no structure (department, positions, list), no size and no m-of-n is
   hardcoded. [#490](https://github.com/smart-social-contracts/realms-gos/issues/490) proposes the
   form of the team before any code is written. IT team members can run `codex.revert_fast`, which can
   only restore the **previous** codex. Each use is recorded on the public record.
3. **Changing the IT team** is done by the realm admins through the realm's admin operations, never by
   a chapter. That operation (called `it_team.set` below; #490 proposes its form) is reserved like the
   others.
4. **Reserved operations:** `codex.revert`, `codex.revert_fast`, `it_team.set` and
   `sync_source_remove` (no chapter can forbid a follower's leaving).
   - No chapter may forbid them, guard them or hook them. The checker refuses it, and the runtime
     never calls chapters for them.
   - A proposal whose action is a reserved operation runs no legislation at all: no chapter's guard,
     forbid or hook runs on its `proposal_create`, its vote or its execution. A guard on
     `proposal_create`, such as §5.2's, can neither refuse nor delay a revert. The same holds for a
     federal vote whose action is reserved, its legs in the quarters and the messages that carry it.
   - No chapter may change the IT team or who defines it.
   - The Cedar `GUARDRAILS` keep them permitted for their holders: no generated forbid targets their
     actions, and the checker guarantees that.
   - A sync source's legislation hooks a follower's leaving on its own side, as a GGG event of the
     links, named in [ggg#3](https://github.com/smart-social-contracts/ggg/issues/3) (§13). That
     operation, `link_notify`, runs on the sync source and is not reserved: a chapter may hook it
     (`on_link_notify`, reading the new `status`, such as `Ended`) and may never guard or forbid it, so a
     side always learns that a link ended. The checker refuses a guard or a forbid on it.
5. **History:** every codex ever active is kept, with its chapters in `ChapterSource`, so a revert
   needs no download. A revert does not clear a non-compliant flag: a follower that reverts below a
   release already in effect is non-compliant again. A revert does not run `on_codex_activate`.

---

## 11. Legislation from above and compliance

Kept deliberately simple: no automatic proposals. Realms exchange only what the links need.

1. **A sync source publishes a release** in its published record (§8), by its own vote: all its
   chapters' checksums, its calendar's, a commit and an effective date. The release is an entry of the
   source's own official journal and of the official journal of every realm that syncs from it (§9.6),
   once that realm's check has found it.
2. **The realm checks on a schedule.** A timer reads each sync source's record, one query per source,
   and stores what it finds with `checked_at`.
   - **The interval is set by the realm admins:** default every 24 hours, allowed from 1 hour to 7
     days. Operation `codex.check_config { interval }`; the unit, hours, is in the operation's schema,
     not in the name.
   - **Admins can force a check now** with `codex.check_now`, which runs the same check at once.
   - Both are admin operations, outside legislation. Chapters can't change them.
3. **The codex page shows the result,** for example: "Spain published 2.4.0, effective 1 Jan 2027.
   Last checked 3 Nov 2026 14:00. [Propose install]".
4. **Proposing the install:** anyone with `proposal.create` can press the button. The realm builds
   the change list itself from the release, with no repository tool needed, and opens an ordinary
   install proposal. The release is installed whole (§3.2). It is voted under the realm's normal rules
   and installed by the normal install task (§9.2). No guard of the codex runs on that
   `proposal_create`: a statutory wait in the realm's own legislation, such as §5.2's, does not apply to
   a release whose date the source's legislation set.
5. **A follower votes when, never whether** (§2, rule 3). Only an install clears the non-compliant
   flag, and it clears it at once.
6. **Compliance is measured against each sync source's record** and worked out at each check:
   - **`compliant`:** every layer the follower runs is at least the latest release in effect in that
     source's record. València, syncing from CV alone, is compliant while it runs what CV relays, even
     when CV is late with a release of Spain's; Spain's legislation deals with CV;
   - **`non_compliant` since `<effective_from>`:** otherwise, naming the stale layers;
   - **`unknown`:** a sync source's record couldn't be read at the last check.

   It is also recomputed right after every install. The status and `checked_at` are shown in
   `ggg_codex_status`. The cost is one query per sync source per check: negligible even hourly.
7. **What follows is the sync source's own legislation,** never the follower's votes. For example, a
   `forbid` in Spain's treasury chapter can test GGG's `follower_compliance` fact of the recipient of a
   transfer. Voting against the install doesn't avoid the flag. Challenging a release is a justice case
   (`ggg_dispute_file`).
8. **Followers report.** At each sync, the follower's capital reports to each sync source's capital a
   `ReportedLayer {version, checksum}` for every layer it runs, its compliance against that source and
   its population. It does not send a layer's source: the sync source finds the layer by its checksum
   in its own record. The source keeps the last report on the follower's entry, with its time, and may
   query the follower at any moment. The follower's own `ggg_codex_status` is the authority. Which
   followers a sync source counts, for its population or a vote, is its own legislation's choice, so
   nobody is counted twice.
9. **If a lower layer breaks against the new release,** say CV overrides something Spain now marks
   `fixed`, the install fails and names CV's chapter. The fix is a new CV release; nothing else is
   special. realms-codices CI shows this on Spain's PR beforehand (§12.3, point 6).
10. **Leaving a sync source:** the follower may vote to stop syncing from it, with no consent needed.
    `sync_source_remove` is reserved (§10.4), so no chapter of the follower can forbid, guard or hook
    it. The source's own legislation decides what follows, through its hook on the GGG event of the
    link that the leaving raises on its side (§13). The follower keeps the layers it had and gets no
    further releases through that link; it may remove them by a change list of its own, or sync from
    another realm that relays them. A sync source may also end a link by its own vote; the follower
    then keeps its layers the same way, and its own legislation decides what follows.

---

## 12. Tools: `realms legislation`

The tools are subcommands of the existing `realms` CLI ([`cli/`](../../cli), package `realms`):
`realms legislation check`, `resolve`, `propose` and `diff`. All of them run on one library, the
self-contained module `realms.legislation` in the same package, which also holds the SDK the chapters
import (§4). The realm canister runs a copy of that module; the copy imports nothing from the CLI's
dependencies (typer, rich, requests, ...), so the same resolver and the same Cedar generator run on a
laptop and in the realm: **one implementation.** The tracking issues are
[realms-codices#11](https://github.com/smart-social-contracts/realms-codices/issues/11) (`check` in
CI) and [realms-codices#12](https://github.com/smart-social-contracts/realms-codices/issues/12)
(`resolve` and `propose`).

### 12.1 `realms legislation check` (off-chain, CI on every realms-codices PR)

It uses Python's parser (`ast`), so it runs only off-chain:

- the layout of §3, which it checks first and stops at if the tree breaks it: folder names, a `source.json`
  whose `id` is the folder's path, only `<domain>.py` files with a GGG domain, the shapes of `source.json`,
  `releases.json` and `calendar.json`, and each release against the source's files **at the release's own
  commit**, which must be in the history of `HEAD` (read with git, so the check needs the full history): all its
  chapters and its calendar listed, never the `realm` domain, every checksum the sha256 of the bytes. A realm loads
  only its own `realm.py`: one beside a source's releases is that source's own. realms-codices CI runs this command and
  has no checker of its own;
- every rule in §4: file and class names, base, imports, banned constructs (`exec`, `eval`, `compile`,
  `globals`, `locals`, `setattr`, `open`, dunder names, defaults, attribute stores), citations, marks (an
  extension that calls `super()`), `uses`, and that every `on_`, `check_` and `compute_` method names a GGG
  operation;
- the `F` expression rules (§6), by binding every forbid to GGG's fact schema: the operation named, `each` naming
  the list it ranges over (for `tax_policy_propose`, the saved policy's `taxes`), `S.` naming a setting of the
  chapter's own domain, `pct()` literals that fit the scale, optional facts compared after `is_some()`, arithmetic
  that cannot overflow, and each setting's `check=`;
- reserved operations (§10.4);
- cycles across `uses`, an undeclared or missing domain, and a chain deeper than the limit, for every realm of the
  tree: each source is checked as a realm with the sources above it as its layers;
- the test cases of each chapter, `<domain>.cases.json` (below);
- **Python versus Cedar agreement:** generated facts, drawn around every literal and endpoint the forbids name,
  the 64-bit extremes and the edges of each fact's range, are decided in plain Python and by Cedar (`cedarpy`, the
  canister's Cedar line), with the realm's settings folded in and left open, and must agree;
- **Never errors:** the generated Cedar of every forbid is proved never to error (§6, rule 9) for each action it
  names, by the Cedar CLI's `symcc` with cvc5 (§12.3), pinned by version and checksum (`scripts/cedar_tools.sh`). A
  query the solver does not decide is a problem, never a pass. Without cedarpy, the Cedar CLI and cvc5 a tree
  with a forbid is not checked: the command exits with code 2 and says what to install, and `--no-analysis` goes
  without them.

A refusal of a layer is printed once, though every realm below it runs the layer.

**Test cases.** A chapter's cases are `<domain>.cases.json`, beside it. Each is run on the codex of one realm:
the one named by its optional `realm` (a source id), else the chapter's own source with the sources above it, in
UTC. A case has a `name` and one kind of test, evaluated in-process with the library, and says what it expects:

| Kind | Fields | Runs |
|---|---|---|
| `call` | `call`, `args` (plain data), `returns` | a public member of the resolved domain: a helper called with the arguments, or a setting read |
| `guard` | `guard` (an operation), `args`, `approvals`, `now` (seconds), `denies` (a reason, or null) | the domain's `check_<operation>` on the operation's arguments, with those departments' approvals and that time |
| `forbid` | `forbid` (an operation), `each`, `facts`, `fires` (forbid ids) | the realm's forbids on that operation (and list) in plain Python, with its settings folded: those that fire must be exactly `fires` |
| `compute` | `compute` (an operation), `args` (the arguments of `compute_<operation>`), `decimals` (the token's), `records`, `returns` | the domain's `compute_<operation>`, run by the realm's own sandbox program (`core.legislation_sandbox`), which reads GGG's entities from `records` (`{"Land": [{"id": "L-1", "owner": "User:alice", "assessed_value": 20000000}], "Human": [...]}`) and hands the chapter the subject as the view `Land.get` gives, with money in `decimals`; `returns` is the invoice items |

```json
[
  {"name": "València may charge 1.24 %", "realm": "eu/es/vc/46250-valencia", "forbid": "tax_policy_propose",
   "each": "taxes", "facts": {"code": "ibi_urban", "rate": 1240001}, "fires": ["eu/es/tax/ibi_urban_cap"]}
]
```

A case that does not come out as written is a problem, `<source>/<domain>.cases.json: case "<name>": <what>`.
Hooks are not run by `check`.

### 12.2 In-canister checks (install step 5, no parser)

These run by introspection while loading, in the canister's copy of `realms.legislation`:

- exactly one base, which is the right `above()` class, and the SDK's metaclass;
- no lower definition of a `fixed` name;
- extension methods don't call `super()` (enforced by the runtime wrapper);
- `uses` enforced at run time;
- forbid operations, `each` lists, `S.` names, and reserved operations;
- valid `F` trees;
- settings' `check=`;
- Cedar strict validation.

The sandbox allows imports only of the SDK and `ggg` (read-only), and removes the banned built-ins.

[#482](https://github.com/smart-social-contracts/realms-gos/issues/482) confirms which of the §4 rules
the sandbox enforces at run time today, and closes any gap. Until then, anything only `check` catches
is listed in the proposal's report.

### 12.3 `realms legislation diff`: the analysis report (realms-codices CI, and attached to the change list)

For a change list, it compares the old and new codices:

1. **The generated Cedar** of both, and the diff.
2. **Newly forbidden and newly allowed, per operation:** for example, "urban IBI between 1.10 % and
   1.24 % for capitals: newly allowed". This is proven by Cedar analysis, not sampled. The analysis
   proves whether anything is newly forbidden or allowed and gives an example request; the range shown
   is computed from the `F` trees and proved equal to the difference by an equivalence check.
3. **"Nothing permitted"** for an operation: no request of that operation can be allowed.
4. **"Never fires"** (a forbid that is always false) and **"overlap"** (a forbid implied by another).
5. **The flattened view per changed domain** (§5.9), before and after.
6. **Dependencies:** every realm and domain affected, through `above()` and `uses`.
7. **Test cases:** run and results.
8. **Settings folded and open:** for a source's change, the analysis runs for each affected realm, with
   its settings folded, and once with the settings left open, which gives statements such as "for
   capitals".

The tool for points 2–4 is Cedar's symbolic compiler, SymCC (`cedar symcc` of the Cedar CLI,
experimental flavour), with the cvc5 solver, both pinned by version and checksum
([R1, #481](https://github.com/smart-social-contracts/realms-gos/issues/481)). Answers hold for every
request GGG's fact schema allows: its ranges and enumerations, which a Cedar schema can't state, are
added to the analysis as assumption forbids. A query the solver doesn't finish is reported as
undecided, never as a pass.

The analysis uses the Cedar semantics of the CLI's version (4.14.1), while the canister links
`cedar-policy` 4.12.0 (basilisk's canister template). The forbids use only core operators, so the
difference is not expected to matter; the two versions are aligned at basilisk's next bump.

Built ([realms-codices#13](https://github.com/smart-social-contracts/realms-codices/issues/13)):
`realms legislation diff [TREE] --base REV [--head REV] [--realm ID]... [--change-list FILE] [--out FILE] [--budget SECONDS]`.

- **What is compared.** The tree at two commits, read with git (never the working copy), as `check` reads it: every
  source is a realm whose layers are its ancestors. The realms analysed are those with a source on their layer path
  whose chapter or calendar changed between `--base` and `--head` (default `HEAD`), or `--realm`. A change list
  (`--change-list`) names the one realm, and the commit its chapters' URLs name is the head; its `replaces` is not
  read, since a live realm's codex is not (reading it comes with the published record, D6).
- **Per realm and Cedar action** (the primary action of a forbid; the actions it also applies to run the same
  forbids), with the settings folded, and, for a realm that is itself a changed source, once more with them left
  open (`context.settings`), the report gives:
  - the **newly forbidden** and **newly allowed**, each proved (`implies` of the old set in the new, and of the new in
    the old) or given with the request that breaks it. Where the difference is a range of one number, the report
    says it: the trees compare a fact only with constants, so they are evaluated at each constant and beside it, and
    the runs where the old and new sets differ are written as forbids and proved to be exactly the difference: each
    claim lies inside what the changed set forbids (`implies(new, permit-all + the newly forbidden claim)`, and
    `implies(old, permit-all + the newly allowed claim)`), misses what the other set forbids (a permit of the claim's
    requests is allowed by it), and `equivalent(new + newly allowed, old + newly forbidden)` leaves nothing out; with
    the settings open, one statement for each combination of the Boolean settings the change reads. A range that is
    not proved, or whose other facts cannot be stated (a Period, a set, a record), is not printed: the request that
    breaks the property stands;
  - **nothing permitted** (`always-denies`), **never fires** and **overlap** (`equivalent` with and without the
    forbid), whether a forbid **can error** (`never-errors`), and each query the solver **did not decide**;
  - the **flattened view** of the domains the change reaches (those that changed, and those that call one, through
    `uses`, however far) before and after, with the folded line of each cap (`for València: capital + transport →
    maximum 1.24 % (1240000)`), proved equal to its forbid like the ranges;
  - the **generated Cedar** diff, the **dependencies** (changed sources and domains, and for each realm the domains
    reached) and the realm's **test cases** (§12.1).
- **Assumptions.** Every set under analysis is the permit-all set plus one forbid that fires on the requests GGG's fact
  schema rules out (a number outside its range, a Period of a count or a unit GGG does not have, a word outside the
  enumeration of a fact), so that every answer holds for the requests the schema allows. The enumerations are
  `facts.json`'s `values`, which `scripts/ggg_candid.py` carries into `ggg_facts.py` as `values` of a `String` fact;
  the realm's generator ignores them (Cedar's schema cannot state them), and only this analysis assumes them. The
  assumption is analysis input and is never installed.
- **Failures and warnings.** The report lists a failure, and the command exits 1, for a forbid that can error, an
  operation nothing is permitted on, a forbid that never fires, a query the solver did not decide (also one of the
  flattened view's maxima), a codex that does not load (a chapter the checker refuses, or a forbid that does not bind
  to GGG's facts: that realm fails, the others are still analysed) and a test case that fails. A forbid that reads a
  setting and never fires with the realm's settings is information (`off in this realm`: another realm that runs
  the source may switch it on); it fails when it reads no setting, or never fires with the settings left open. An
  overlap is a warning, asked of the realm as it is. Newly forbidden and newly allowed are information: they are what
  a change of legislation is for. A `--realm`, or the realm of a change list, that is no source of the tree at the
  head commit exits 2, as `check` does. A change to only the test cases of a chapter (`<domain>.cases.json`) reaches
  the realms that run the chapter, so that their cases run.
- **Tools.** The pinned Cedar CLI and cvc5 (`CEDAR_CLI`, `CVC5`; `scripts/cedar_tools.sh`); without them it exits 2 and
  says so. Up to four SymCC queries run at once.
- **Time budget.** With `--budget SECONDS` no query starts after that time and a running one is stopped. A realm the
  budget did not reach is listed as `not analysed (time budget)`, and one it stopped part-way as `not analysed (time
  budget): stopped part-way` (its partial answers are dropped); both are failures, so a pull request cannot pass on
  what was not analysed. The report is written whatever the budget does.
- **Limits.** With the settings open, the schema is the new codex's, so an old forbid that reads a setting the new
  codex no longer has is not analysed (SymCC refuses it, and it is reported as undecided). The links between realms
  are not read, as with `check`.

### 12.4 `realms legislation resolve`: resolver and flattened view

Given a realm's links and a commit, it prints the layer paths, the flattened view and the generated
Cedar, with the same `realms.legislation` code the canister runs.

Built ([realms-codices#12](https://github.com/smart-social-contracts/realms-codices/issues/12)):
`realms legislation resolve [TREE] --realm <source id> [--commit REV] [--sync-source ID]... [--record FILE]... [--own FILE] [--facts FILE]`.

- **Records.** A sync source's record (§8) is the tree's own `releases.json` (`--sync-source`: the newest release of
  the source and of each source above it) or a file in the shape of §8 (`--record`). Reading them from the sync
  sources' realms comes with the published record (D6, [#491](https://github.com/smart-social-contracts/realms-gos/issues/491)).
- **Files.** The tree is read with git, never from the working copy: a relayed layer at the commit its release
  names, the realm's own layer (its folder, or the layer in `--own`) at `--commit`, and every file against the
  checksum its record lists.
- **Output.** The codex id (§9.1), the layers, each domain's layer path and flattened view, and the Cedar of every
  forbid when `--facts` gives a fact schema in the shape the generator reads (`cedar.FactSchema`). It calls no network.

### 12.5 `realms legislation propose`

Given the realm, the chapters to set or remove, and a commit, it writes the change list (with
`replaces` read from the realm's `ggg_codex_status`) and the report. It prints the `Code { url;
checksum }` to submit.

Built ([realms-codices#12](https://github.com/smart-social-contracts/realms-codices/issues/12)):
`realms legislation propose [TREE] --realm <source id> --commit REV --set <chapter>... --remove <chapter>... --url URL --out FILE`,
with `--replaces <codex id | none>` or `--network`, `--canister` and `--identity`, and optionally
`--summary LANG=TEXT`, `--report FILE --report-url URL` and `--repo OWNER/NAME`.

- **The file.** The change list as canonical JSON (§9.1: sorted keys, no spaces, UTF-8, no newline at the end),
  so the checksum a proposal pins is the checksum of the file. Each chapter's URL names the commit in full, and its
  sha256 and size are those of the bytes at that commit. `--url` is where the change list will be served.
- **`replaces`** is given, or read from the realm's `codex_status` when `--network`, `--canister` and `--identity`
  are all given; the command says which it reads before it does. No network is reached otherwise, there is no
  default network, and no identity is chosen for the caller.
- **Refused.** What the realm would refuse (§7): a chapter outside the realm's own layer (a layer from above comes
  as a release), a name that is not a GGG domain or `calendar`, a chapter of 0 bytes, above 256 KB or not UTF-8, more
  than 50 chapters, a change list above 64 KB, an address that is not https. A `release` is always `null`:
  installing a release is the realm's own "Propose install" (§11).
- **Stricter and looser than the realm.** The same chapter in `--set` and `--remove` is refused by the command; the
  realm accepts it, if the codex has the chapter, and applies the removal first. A `--remove` of a chapter the codex does not have passes the
  command, which does not know the codex, and can only fail at the realm (§9.2 step 2).
- **The report** (§12.3, C4) is attached, not written: `--report` is the file and `--report-url` where it is served.
- **Output.** The `Code { url; checksum }` to submit.

---

## 13. What GGG provides

GGG already has operations, events and arguments, and chapters use them directly. What the layered
codex needs from GGG beyond that is specified in the GGG repository,
[What GGG provides for a layered codex](https://github.com/smart-social-contracts/ggg/blob/main/docs/layered-codex.md):

- the **domain list** ([ggg#1](https://github.com/smart-social-contracts/ggg/issues/1));
- the **entities, fields and operations real legislation needs**
  ([ggg#2](https://github.com/smart-social-contracts/ggg/issues/2)), among them the `compute_` hook
  kind, forbids that range over a list (`each`), the `Pending` codex status with `codex_activate`, the
  fact schema's rate scale (hundred-millionths), rounding rule, and ranges and allowed values, and `Period` as a count and a unit;
- the **links between realms** ([ggg#3](https://github.com/smart-social-contracts/ggg/issues/3)):
  sync sources, followers, `ReportedLayer`, the published record, the `follower_compliance` fact and
  the link event a sync source hooks;
- **proposal links** ([ggg#4](https://github.com/smart-social-contracts/ggg/issues/4));
- **`Realm.timezone`** ([ggg#5](https://github.com/smart-social-contracts/ggg/issues/5)), and a
  `timezone` on `quarter_configure` for a quarter of a federation
  ([ggg#21](https://github.com/smart-social-contracts/ggg/issues/21));
- **`effective_at`** on proposals ([ggg#6](https://github.com/smart-social-contracts/ggg/issues/6));
- the **`WorkingDay`** Period unit ([ggg#7](https://github.com/smart-social-contracts/ggg/issues/7));
- **one scale for every ratio**, hundred-millionths
  ([ggg#9](https://github.com/smart-social-contracts/ggg/issues/9)), which moves the model's
  basis-point fields: a breaking change.

Nothing else: the marks, the layered codex, the layout, the change list, the install rules and the
official journal extension belong to Realms GOS.

---

## 14. Implementation plan

The tracking issue is [#480](https://github.com/smart-social-contracts/realms-gos/issues/480). Each
issue carries acceptance criteria from the sections cited. Commits say `Refs #N` and PRs say
`Closes #N`. The letter ids (R1, G1, S1, C1, D1, J1) are labels used in the issues, not an order; there
is no G6.

| Id | Repo | Title | Depends on | Done when |
|---|---|---|---|---|
| R1 ([#481](https://github.com/smart-social-contracts/realms-gos/issues/481)) | realms-gos (research) | Cedar analysis tooling for "newly forbidden", "nothing permitted", "never fires" and "overlap" | – | **Done:** a report with a recommendation (SymCC with cvc5, §12.3) and a working sample on the three IBI range and cap forbids of §5.6 and the Art. 21.2 forbid of §4. Its findings are in §5.6, §6, §9.4 and §12.3, and in S2, C2, C4 and D4 below |
| R2 ([#482](https://github.com/smart-social-contracts/realms-gos/issues/482)) | realms-gos (research) | Which §4 restrictions the codex sandbox enforces at run time today | – | table of rule → enforced or not, with the code path |
| G1 ([ggg#1](https://github.com/smart-social-contracts/ggg/issues/1)) | ggg | Domain list | – | versioned list merged |
| G2 ([ggg#2](https://github.com/smart-social-contracts/ggg/issues/2)) | ggg | The entities, fields and operations that real legislation needs (§13), each added or narrowed; the share arguments name `legislation`; the fact schema's rate scale (hundred-millionths), unit rule, and ranges and allowed values; `each="taxes"` over the saved policy, also at `tax_policy_draft` | G1 | `.did` merged; conformance notes updated |
| G3 ([ggg#3](https://github.com/smart-social-contracts/ggg/issues/3)) | ggg | Links between realms, with `follower_compliance`, `ReportedLayer {version, checksum}` and the link event a sync source hooks | – | types, operations and the link invariants merged; the install rules (vouching record, whole releases, one chain) are Realms GOS's (D2) |
| G4 ([ggg#4](https://github.com/smart-social-contracts/ggg/issues/4)) | ggg | Proposal links: "replaces" and "relates to" (§7) | – | `.did` merged; a withdrawn proposal can be replaced by a new one that links to it |
| G5 ([ggg#5](https://github.com/smart-social-contracts/ggg/issues/5)) | ggg | `Realm.timezone`, an IANA name, set by each realm and never taken from another realm, legislation or a sync source (§4.5) | – | `.did` merged; the UI shows the realm's official time; local time is computed by Realms GOS (#503), not by GGG |
| G7 ([ggg#6](https://github.com/smart-social-contracts/ggg/issues/6)) | ggg | Proposal `effective_at`, UTC ns, optional: once accepted, the action executes at that time, for every kind of proposal (§7) | – | `.did` merged; `ProposalCreateArgs` and `Proposal` carry the field; conformance notes say when an accepted proposal executes |
| G8 ([ggg#7](https://github.com/smart-social-contracts/ggg/issues/7)) | ggg | `PeriodUnit` gains `WorkingDay` (§4.3): a day that is neither a weekend day nor a holiday in the realm's calendar, in the time zone of the clock that counts (§4.5); GGG names the unit, realms count it | G2 | `.did` and the schema docs have the unit, defined as above |
| [ggg#9](https://github.com/smart-social-contracts/ggg/issues/9) | ggg | One scale for every ratio, hundred-millionths (§4.3): the model's basis-point fields (quorum, thresholds, shares, weights) move to it; a breaking change | – | `.did`, the conventions and the conformance suite use the one scale |
| [ggg#21](https://github.com/smart-social-contracts/ggg/issues/21) | ggg | Quarters in different time zones (§4.5): I-ADM-4 clarified (a quarter starts from its capital's zone, the realm may set it by vote), and `timezone` on `quarter_configure` | G5 | model, layered-codex page and `.did` say it; the fixture and the conformance suite agree |
| [#503](https://github.com/smart-social-contracts/realms-gos/issues/503) | realms-gos | Realm-local time in Realms GOS (§4.5): a pure-Python zone-file reader plus date arithmetic; the release ships the tz data for every zone as hex text that `bytes.fromhex` decodes (never base64) and the realm reads only its own zone, parsed once and kept in memory; no change to the Python runtime | G5 | a realm with `timezone = "Europe/Madrid"` reports the right local date and offset on both sides of a DST switch, on PocketIC, including in a year after 2037 (the POSIX footer rule); no base64 and no new runtime modules are used |
| [#504](https://github.com/smart-social-contracts/realms-gos/issues/504) | realms-gos | Proposals execute at `effective_at` (§7, §9.2 step 6, §9.5): every accepted proposal's action runs at that time, or on acceptance without one; a layered-codex install switches then, and quarters switch at that T | G7 | PocketIC: a transfer, a codex install and a settings call each execute at `effective_at` and not before; without it, on acceptance; the install's `on_codex_activate` runs at the switch |
| [#505](https://github.com/smart-social-contracts/realms-gos/issues/505) | realms-gos | Count working days in the realm (§3.4, §4.3): start + n working days on the realm's calendar in its time zone, one function in the date module of #503; the library gains `working_days(n)`; a count that reaches a year with no calendar entries fails closed | #503, G8 | PocketIC: a 2-working-day count over a holiday and a weekend, and one across a summer-time change, pass; a count into a year with no entries fails with a clear error |
| [#499](https://github.com/smart-social-contracts/realms-gos/issues/499) | realms-gos | Test harness for linked realms: scripted peers in `fake_chain`, multi-canister PocketIC, outcall mocking, public fixtures | – | the workflow tests of the change list run on it |
| S1 ([#483](https://github.com/smart-social-contracts/realms-gos/issues/483)) | realms-gos | `realms.legislation`: `Chapter`, `above`, marks, settings with `check=`, `pct()`, the `self` API, hooks, guards and `compute_` methods by GGG operation name, `on_codex_activate` and `compute_realm_join`, read-only `ggg` entities, `times()`, `self.calendar`; self-contained, no CLI dependency | G2 | unit tests for every line of the §4.1 table, including the §5.5 refusal; the module imports nothing from typer, rich or requests |
| S2 ([#484](https://github.com/smart-social-contracts/realms-gos/issues/484)) | realms-gos | `realms.legislation`: `F` expressions with `each` (one Cedar action per list, §6) and `S.`, `pct()` literals, Period comparisons, canonical Cedar output, a generic output mode for analysis (settings left open, §12.3), Python evaluation | S1 | the forbids of §5.6 and §4 produce the Cedar of §5.8; Python and Cedar agree on 10 000 generated fact sets, among them the literals ±1, 0 and the 64-bit extremes; every forbid passes `never-errors`; a Period comparison is true for another unit, alone or under `~` with other facts, and one in `when()` is refused |
| S3 ([#485](https://github.com/smart-social-contracts/realms-gos/issues/485)) | realms-gos | Resolver: layer paths from the links with the one-chain rule, extension merge, `uses`, depth limit | S1 | examples 5.1–5.7 resolve as described |
| C1 ([realms-codices#10](https://github.com/smart-social-contracts/realms-codices/issues/10)) | realms-codices | `legislation/` tree, `source.json`, `releases.json` with the `calendar` field, `calendar.json`, README for authors | S3 | the València example chapters present; schema checked in CI |
| C2 ([realms-codices#11](https://github.com/smart-social-contracts/realms-codices/issues/11)) | realms-codices | `realms legislation check` in realms-codices CI (the command lives in realms-gos `cli/`), including `never-errors` for every forbid (§6, rule 9) | S1, S2, C1 | every rule of §12.1 has a failing and a passing test, among them a forbid that can overflow |
| C3 ([realms-codices#12](https://github.com/smart-social-contracts/realms-codices/issues/12)) | realms-codices | `realms legislation resolve` and `propose` (the commands live in realms-gos `cli/`) | S3, C1 | change list of §7 produced for the §5.6 bonus change |
| C4 ([realms-codices#13](https://github.com/smart-social-contracts/realms-codices/issues/13)) | realms-codices | `realms legislation diff`: the analysis report on PRs, by SymCC (the Cedar CLI 4.14.1, experimental flavour) with cvc5 1.3.1, both downloaded by version and checksum | R1, C3 | report of §12.3 posted on a sample PR |
| C5 ([realms-codices#14](https://github.com/smart-social-contracts/realms-codices/issues/14)) | realms-codices | Review agora, dominion and syntropia as realistic templates (outside this design) | – | review written; follow-up issues opened if any |
| D1 ([#486](https://github.com/smart-social-contracts/realms-gos/issues/486)) | realms-gos | LayeredCodex, ChapterSource and InstallJob entities; `pending` status and `activates_at`; legacy path kept | S3 | legacy installs still pass their tests |
| D2 ([#487](https://github.com/smart-social-contracts/realms-gos/issues/487)) | realms-gos | Install task (§9.2): routes, vouching, whole releases, one-chain rule, disagreeing relays refused, steps safe to run twice, failure, one job at a time, timeout, paused job resumed by an admin, switch at the proposal's `effective_at` | D1, #504 | PocketIC test: install, failure at each step leaves the codex unchanged, retry works, a paused job resumes by an admin only |
| D3 ([#488](https://github.com/smart-social-contracts/realms-gos/issues/488)) | realms-gos | Runtime (§9.3): loading, methods by GGG operation, marks, fail closed guards, `compute_`, `on_codex_activate` at activation | D1, S3, #503 | PocketIC: examples 5.2–5.4 behave as described; §5.2's working-day date is counted from the realm's local day |
| D4 ([#489](https://github.com/smart-social-contracts/realms-gos/issues/489)) | realms-gos | Cedar per-operation actions, per-list actions `<operation>.<list>` and generated forbids with `each` (§9.4), also run at `tax_policy_draft` | S2, D3 | existing auth tests pass; §5.6 forbids enforced at propose and at draft, on `tax_policy_propose.taxes` and `tax_policy_draft.taxes`; decision cost measured and recorded |
| D5 ([#490](https://github.com/smart-social-contracts/realms-gos/issues/490)) | realms-gos | Revert floor and IT admin team (§10): the form of the admin-defined team is proposed first; `sync_source_remove` reserved. Note: `ggg_codex_revert` takes no argument today and cannot name the codex to restore; §10.1's "any earlier codex" needs one, and a revert does not run `on_codex_activate` | D2 | PocketIC: a codex that forbids everything is still reverted, both ways, under the restored codex's voting rules; no chapter blocks `sync_source_remove` |
| D6 ([#491](https://github.com/smart-social-contracts/realms-gos/issues/491)) | realms-gos | Public record reads at install and read time: sync sources' published releases (§8) | G3 | install refuses a layer not in the record that vouches for it |
| D7 ([#492](https://github.com/smart-social-contracts/realms-gos/issues/492)) | realms-gos | Scheduled sync-source checks (admin-set interval, `codex.check_now`), "Propose install", compliance at each check, follower reports as `ReportedLayer` (§11) | D2, D6 | PocketIC: interval change and forced check work; the button's proposal installs the release; past its effective date an older pin reads non-compliant; installing clears it |
| D8 ([#493](https://github.com/smart-social-contracts/realms-gos/issues/493)) | realms-gos | UI: flattened view, proposal diff with report, compliance status and "Propose install" (§11), IT team alerts, failing chapters and revert button (§9.3), the realm's official time from `Realm.timezone` | D3, C4, G5 | each view checked in a browser on a local or staging realm |
| D9 ([#494](https://github.com/smart-social-contracts/realms-gos/issues/494)) | realms-gos | End to end: València's layered codex on PocketIC, from change list to running hooks | D2–D7 | one test runs §5's examples on a realm |
| [#500](https://github.com/smart-social-contracts/realms-gos/issues/500) | realms-gos | Linked realms: `sync_source_add`, `follower_accept`, records, serving chapter bytes, follower reports, leaving through the link event (§11) | G3, D1 | PocketIC: two realms link by both votes, the follower installs a relayed release from the source's capital, reports its layers, and leaves through the link event |
| [#501](https://github.com/smart-social-contracts/realms-gos/issues/501) | realms-gos | Propagation across a realm's own quarters (§9.5): staging mode, the switch at T (the proposal's `effective_at`), quarter compliance, paused tasks resumed by an admin | D2, #504 | PocketIC: every quarter switches at T; a quarter not ready reads non-compliant; a task interrupted by an upgrade resumes only by an admin |
| [#502](https://github.com/smart-social-contracts/realms-gos/issues/502) | realms-gos | Districts as zones: `User.home_zone`, `Zone.population`, `compute_realm_join` preferring a zone | G2 | PocketIC: a member with a home zone joins the quarter that holds it while capacity allows; capacity decides otherwise |
| [#528](https://github.com/smart-social-contracts/realms-gos/issues/528) | realms-gos | Quarters in different time zones, and the settings the realm sets for its quarters (§4.5, §9.5, §9.6): a quarter starts on its capital's zone, copied at `quarter_join_federation` unless the joining realm already chose one; `quarter_configure` gains `timezone` with a `check=`, and the capital pushes it to the quarter over `quarter.sync`; realm-wide counts use the capital's zone, local ones the quarter's; J1 uses the capital's, and a quarter's journal shows it; the identity and the shared foundations are kept in sync from the capital (pushed on change over `quarter.sync`, carried by `quarter_sync` so a quarter catches up, refused by a quarter's `realm_configure` with `Forbidden` naming the capital); the welcome message is copied at join like the zone and set through `quarter_configure`; a realm with another accounting currency code or decimals cannot join as a quarter; `adopt()` stops copying the name; a quarter in `setup` keeps `setup`, and `adopt()` launches a quarter it builds right after its first sync, and keeps it pending, looking again every `ADOPT_EVERY`, until the sync and the launch have both succeeded, so it takes the capital's stage (a quarter's `setup_launch` is refused with `Conflict` while its capital is in `setup`, and until the quarter's first sync) | [#511](https://github.com/smart-social-contracts/realms-gos/issues/511), #501, #499, ggg#21, ggg#24 (`welcome_message` on `QuarterConfigureArgs`) | multi-canister PocketIC: a quarter joins on its capital's zone and keeps it when the capital's changes, and a realm that already chose a zone keeps it on joining; the quarter and a realm-wide vote can each change it, and a malformed name, or a `timezone` with no `quarter`, is refused before any vote; a quarter that refuses or can't be reached leaves everything unchanged; a working-day count over a Madrid/Tokyo date difference uses the capital's zone for a realm-wide vote and the quarter's for its department; a switch at T is the same UTC instant in both; a hook cannot set a zone; with a capital and two quarters, a realm-wide change of the name, branding, manifesto and accounting currency reaches both, each keeps its own label, a quarter that can't be reached does not undo it and catches up at its next `quarter_sync`, and a quarter's `realm_configure` of a synced field is refused with `Forbidden` before any vote; the welcome message is copied once at join, kept by a realm that had one, and set by `quarter_configure`; a realm with another accounting currency code or decimals is refused at join; a federal vote on a synced field takes effect on the capital and reaches both quarters by the push, with no replay in them; a pushed stage change runs `on_stage_change` in the quarters; a failed first sync of a capital-built quarter is retried and the quarter launches |
| J1 ([realms-extensions#54](https://github.com/smart-social-contracts/realms-extensions/issues/54)) | realms-extensions | Core extension `journal`, the official journal (§9.6): one per realm, daily issues, sync-source releases as entries and, on a sync source, its own releases, JSON served by the canister as the authority, PDF print route with the logo, personal-data rules in config; added to `core-extensions.json` | D7, #503 | a new realm has the journal; an issue opens at midnight on the capital's clock, also across a DST change; a quarter's journal issues nothing and shows the capital's; a release found by the check appears as an entry; the print route renders; publication changes no `activates_at` |

Suggested order: R1 and R2 in parallel with G1, G4, G5, G7, ggg#9, #499, #503 and #504 → G2 and G3 → G8 →
#505 → S1 → S2 and S3 → C1–C3 → D1 → D2 to D4 and #500 → D5 to D7, #501 and J1 → D8 and D9, #502,
#528 (after #511, #501 and ggg#21) → C4 and C5.
