# GGG: Generalized Global Governance

GGG describes what a realm is, independently of the software that runs it. Realms GOS is one
implementation of it, and other Governance Operating Systems can be written against it too. GGG
lives in its own repository, [smart-social-contracts/ggg](https://github.com/smart-social-contracts/ggg),
which is checked out here as the `ggg/` submodule:

| Part | What | Where |
|---|---|---|
| **The model** | Entities, operations, lifecycles, invariants and permissions | [`ggg/model/README.md`](https://github.com/smart-social-contracts/ggg/blob/main/model/README.md) |
| **The interface** | The model in Candid: a record for every entity and a method for every operation | [`ggg/ggg.did`](https://github.com/smart-social-contracts/ggg/blob/main/ggg.did) |
| **The conformance suite** | Checks a canister against `ggg.did` | [`ggg/conformance/`](https://github.com/smart-social-contracts/ggg/tree/main/conformance) |

What Realms GOS adds to the model, and how it binds it, is in [Realms GOS and the GGG model](spec.md).

## What the model defines

| Part | Example | In Realms GOS |
|---|---|---|
| **Entities**: what a realm stores | `Proposal`, `Vote`, `Department`, `Invoice`, `Dispute` | [`src/realm_backend/ggg/`](../../src/realm_backend/ggg), one class per entity |
| **Operations**: what changes them | `proposal_vote` (permission `proposal.vote`, delegable) | `@op` functions in [`src/realm_backend/api/`](../../src/realm_backend/api) |
| **Lifecycles**: the allowed statuses | `voting → accepted → executed` | checked by the operations |
| **Invariants**: what must always hold | I-ALL-3: nobody decides a matter they are a party to | Cedar guardrails and the acceptance tests |

It describes behaviour, not code: another implementation that follows it is a realm too.

## Reading the model

The model has one section per domain: identity, departments, governance, finance, assets,
licensing, justice, procurement, federation, codex and audit, setup, private content. Each section
lists:

1. **Entities** with their fields.
2. **Lifecycles** of the entities that have a status.
3. **Operations** in a table: name, permission, flags, arguments and result.
4. **Invariants**, numbered (`I-GOV-2`, `I-EXT-2`, …). Tests are named after them.

§1 sets conventions that apply everywhere: ids are text, times are nanoseconds, money is an
integer in the token's smallest unit, every update writes one `Event`, and no call reads a whole
collection. §14 lists the permissions, §15 the cross-cutting invariants, and §16 the decisions
taken.

Extensions are a Realms GOS feature, not part of GGG: they are on [the Realms page](spec.md).

Realms GOS still uses seconds, text principals and `from`/`count` paging on its `read`/`run`
interface. [The binding](spec.md#1-how-realms-gos-binds-the-model) lists the differences.

## The `ggg.did` methods

The realm canister implements every method of `ggg.did`, next to `read` and `run`. Each method calls
its operation through the same dispatcher, so permissions, governance, delegation and the audit log
apply exactly as they do for `read` and `run`. The GGG method changes only the shape of the call and
of the reply.

- [`ggg_did.py`](../../src/realm_backend/ggg_did.py) and
  [`ggg_methods.py`](../../src/realm_backend/ggg_methods.py) are generated from the submodule by
  `python scripts/ggg_candid.py`. Rerun it after updating `ggg`; CI checks they are current.
  - `ggg_did.py` holds the types twice. The `Record` and `Variant` classes are what basilisk reads
    when it builds the canister. `SCHEMA` and `METHODS` are what the code reads at run time, because
    the canister's Python keeps no types.
  - `ggg_methods.py` holds one line per method, each a call into `ggg_api`.
- [`ggg_api.py`](../../src/realm_backend/ggg_api.py) converts in both directions. Most of it is
  generic and driven by the types:
  - nanoseconds and seconds;
  - `principal` and text;
  - variants and lower-case statuses;
  - `Ref` and `Kind:id`;
  - basis points and percentages;
  - `cursor`/`limit` and `from`/`count`;
  - lists of pairs and maps.

  The rest are short tables:
  - field renames;
  - argument adapters, for operations whose arguments are shaped differently;
  - reply builders, for operations whose replies are.

  The operations say the rest themselves. Each `@op` names with `returns` the key of its reply that
  holds the result, and a record named by id is read from its row, so it comes back whole. The
  exception an operation raises names the `GggError` (`Conflict`, `Forbidden`, ...), and the
  dispatcher passes it on as the refusal's `kind`.
  [`tests/ggg/test_candid.py`](../../tests/ggg/test_candid.py) checks that every operation whose
  reply is a record, a page or a value names its key, and
  [`tests/ggg/test_refusals.py`](../../tests/ggg/test_refusals.py) that no plain `Fail` describes a
  record's state or who owns it.
- A proposal's `Call` carries Candid. `ggg_api` decodes it with the target method's argument type
  and stores the operation and its JSON arguments, as Realms GOS always has. It encodes them back
  when the proposal is read.

[`tests/ggg/test_candid.py`](../../tests/ggg/test_candid.py) builds the Candid the way basilisk
does and compares every method with `ggg.did`.

`GGG_MIRROR=1 pytest tests/ggg` runs the acceptance tests through the GGG methods instead. Each
operation a test calls goes through its `ggg_` method, its reply is checked strictly against the
types, and the test still asserts on the operation's own result. The run writes a report to
`.ggg-mirror.txt`, listing problems by method and fields that came back empty in every reply. CI runs
it as `GGG_MIRROR=strict`, where the first problem fails the test.

[`tests/ggg/test_ggg_methods.py`](../../tests/ggg/test_ggg_methods.py) covers what the mirror can't
reach, and the [conformance suite](https://github.com/smart-social-contracts/ggg/tree/main/conformance)
checks a built canister.

basilisk reads a dotted name in a method signature (`ggg_did.Realm`) as `text`, and finds a method
only among `main`'s globals. That is why the generated methods annotate with bare names and `main.py`
star-imports them.

## The model and the code stay in step

[`tests/ggg/test_spec.py`](../../tests/ggg/test_spec.py) parses the operation tables of the model
and of [the Realms page](spec.md), and compares them with the `@op` registry: same names,
permissions and flags (`read`, `delegable`, `governed`, `signing`, public). Add an operation to the
code without either document (or the reverse) and the test fails. So neither can drift silently.

Invariant tests follow the model's numbering:

```python
def test_I_ORG_3_agents_have_a_seated_human_supervisor():
    ...
```

The rule: **when the model and the code disagree, the code is wrong.** Change the model first
(in the `ggg` repository, then the submodule reference here), then the code. An operation only
Realms GOS has goes in [the Realms page](spec.md#5-operations-realms-gos-adds) instead.

## A running example

The model and the tests share one small world
([`tests/ggg/world.py`](../../tests/ggg/world.py)): **Agora**, with departments Civil Registry,
Infrastructure, Finance, Health and Justice; members Alice, Bob, Dana, Carla, Dr. Ana, Judge Elena
and Mallory (who gets banned); the organizations Clinic B and BuildCo; and two AI agents,
`claude-conn` (acting for Alice) and `records-bot` (Clinic B's records agent).
