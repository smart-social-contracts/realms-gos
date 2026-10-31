# Design choices

This page explains *why* Realms GOS is built the way it is: the goals, the criteria used to decide,
and the decisions that follow from them.

## Goals

Realms GOS aims to be the first governance operating system of its kind: software a community or
a government can run its institutions on. To make that easy to adopt, it deliberately reproduces
the structures people already have (departments, positions, budgets, courts, licences), even ones
that may seem old-fashioned. For now it is run by humans, with optional AI assistance.

## Criteria

Every design decision is weighed against these criteria, roughly in this order:

1. **Legitimacy.** Anyone must be able to see which rules were in force, who acted, and why it was
   allowed. Records and rules must be readable without special tools.
2. **Safety.** Mistakes and malicious code must not be able to break the realm's basic guarantees.
   Fail closed.
3. **Longevity.** Governance data must last decades and outlive any one version of the code.
4. **Amendability.** Changing a law is a governance act (a vote), not a software release.
5. **Accessibility.** Lawyers, civil servants and analysts, not only programmers, should be able to
   read the rules. AI should be able to read, draft and review them well.
6. **Simplicity.** One way to do each thing. Less code means fewer bugs and an easier audit.
7. **Cost and speed.** Important, but traded away first when they conflict with the above.

## Decisions

### Python on stable memory

- **Storage over speed.** Governance data (records, votes, registries, audit logs) grows
  indefinitely. Stable structures scale to hundreds of GiB per canister; the heap is far smaller.
  The cost is speed: Python on Basilisk uses more instructions per call, so every list is paginated
  and work over many records runs in batches. *(Longevity over speed.)*
- **Constant-time upgrades.** Data stays in stable memory, so `post_upgrade` only reconnects to
  it and re-arms timers, however large the data gets. *(Safety, longevity.)*
- **Data outlives code.** Records are plain JSON in documented formats (the [GGG spec](../ggg/spec.md)),
  so they stay readable, exportable and auditable without depending on one runtime's memory
  layout. *(Legitimacy.)*
- **Laws change without redeploying.** A codex's source is stored as data and run in a sandbox, so
  amending a law is a vote to install a new codex version, not a Wasm upgrade. Motoko has no
  runtime eval. *(Amendability.)*
- **Auditable laws.** Every codex is stored with its source, version and checksum, and links to the
  version it replaced, so the rules in force at any time can be shown exactly. *(Legitimacy.)*
- **Readable by non-specialists.** Python is readable by lawyers and analysts, and LLMs write and
  review it well. *(Accessibility.)*

Rust also has stable structures, so storage and upgrades alone do not decide the language. Running
laws as data, and keeping them readable to non-programmers, do.

### One operation registry, one entry point

Every capability is an [operation](operations.md): one Python function with `@op`, declared once.
The canister exposes only `read` and `run`. Extensions, codices, proposals, the REPL and MCP tools
all call the same registry through the same `dispatch`.

*Why:* the previous backend had three ways into its logic (Candid endpoints, extension bridge
verbs, codex bridge verbs), each with its own checks. One entry point means authorization, audit
and governance are written once and cannot be skipped. The AI tool list (`ops_list`) is
generated, not hand-written. *(Safety, simplicity.)*

### Cedar, and only Cedar, for authorization

Every call is decided by [Cedar](authorization.md). Operation functions contain no permission
checks. The non-negotiable rules are Cedar `forbid` guardrails that nothing at runtime can
override.

*Why:* a declarative policy can be read, reviewed and tested as a whole, and a `forbid` cannot be
bypassed by a forgotten `if`. It fails closed. *(Safety, legitimacy.)*

### Governance as a flag

An operation marked `governed` becomes a proposal when its department decides by vote, and is
replayed when the proposal passes. The same code serves a small realm where an admin acts directly
and a large one where every change is voted. *(Simplicity, legitimacy.)*

### Codices produce effects, not changes

A codex hook cannot write anything. It returns a list of `(op, args)` effects that the realm then
applies through `dispatch`, as itself, with Cedar refusing any operation the codex did not
declare. A failing hook never fails the call that triggered it. *(Safety, legitimacy.)*

### Sandboxed, metered, deterministic code

Codex and extension code runs in a fresh subinterpreter per call, with an instruction budget and an
allow-list of actions. Code is stateless, uses deterministic iteration and integer amounts, never
floats. See [sandboxing](sandboxing.md). *(Safety.)*

### Trust is explicit

A realm accepts code only under its trust policy: approved by a trusted approver (by default its
marketplace) or pinned by checksum. Installing code is itself a governed operation. See
[trust and the registry](trust-and-registry.md). *(Safety, legitimacy.)*

### Shard by quarters, not by tables

When data outgrows one canister, the realm splits into [quarters](quarters-and-federation.md):
full realm canisters that share a codex. There is no central user directory, so no canister grows
with the whole population. *(Longevity, simplicity.)*

### Small on purpose

The backend has a hard budget of 10,000 lines (it is about 6,600). Entities hold data only; there
are no response classes, no `try/except` in operations, no defensive code for impossible states.
See [contributing](../development/contributing.md). *(Simplicity.)*

### No compatibility layers

The project is not live yet, so the refactor kept no aliases and no data migrations. Callers that
broke were fixed. The plan behind it: [refactor plan](../project/refactor-plan.md).
