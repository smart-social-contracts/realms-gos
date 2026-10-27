# GGG: Generalized Global Governance

GGG is the standard every realm implements. The full text is the [spec](spec.md). This page
explains what it is and how it connects to the code.

## What it defines

| Part | Example | In the code |
|---|---|---|
| **Entities**: what a realm stores | `Proposal`, `Vote`, `Department`, `Invoice`, `Dispute` | [`src/realm_backend/ggg/`](../../src/realm_backend/ggg), one class per entity |
| **Operations**: what changes them | `proposal_vote` (permission `proposal.vote`, delegable) | `@op` functions in [`src/realm_backend/api/`](../../src/realm_backend/api) |
| **Lifecycles**: the allowed statuses | `voting → accepted → executed` | checked by the operations |
| **Invariants**: what must always hold | I-ALL-3: nobody decides a matter they are a party to | Cedar guardrails and the acceptance tests |

It describes behaviour, not code: another implementation that follows it would be a realm too.

## Reading the spec

The spec has one section per domain: identity, departments, governance, finance, assets,
licensing, justice, procurement, federation, codex and extensions, setup. Each section lists:

1. **Entities** with their fields.
2. **Lifecycles** of the entities that have a status.
3. **Operations** in a table: name, permission, flags, arguments and result.
4. **Invariants**, numbered (`I-GOV-2`, `I-EXT-1`, …). Tests are named after them.

§1 sets conventions that apply everywhere: ids are text, times are integer seconds, money is an
integer in the token's smallest unit, every update writes one `Event`, and no call reads a whole
collection (lists page with `from`, `count` and `next`). §13 lists the permissions, §14 the
cross-cutting invariants, and §15 the decisions taken.

## The spec and the code stay in step

[`tests/ggg/test_spec.py`](../../tests/ggg/test_spec.py) parses the operation tables of the spec
and compares them with the `@op` registry: same names, permissions and flags (`read`, `delegable`,
`governed`, `signing`, public). Add an operation to
the code without the spec (or the reverse) and the test fails. So the spec cannot drift silently.

Invariant tests follow the spec's numbering:

```python
def test_I_ORG_3_agents_have_a_seated_human_supervisor():
    ...
```

The rule of the spec: **when it and the code disagree, the code is wrong.** Change the spec first,
then the code.

## A running example

The spec and the tests share one small world
([`tests/ggg/world.py`](../../tests/ggg/world.py)): **Agora**, with departments Civil Registry,
Infrastructure, Finance, Health and Justice; members Alice, Bob, Dana, Carla, Dr. Ana, Judge Elena
and Mallory (who gets banned); the organizations Clinic B and BuildCo; and two AI agents,
`claude-conn` (acting for Alice) and `records-bot` (Clinic B's records agent).
