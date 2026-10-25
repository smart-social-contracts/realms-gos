// The realm's non-negotiable rules.
//
// These ship in the canister and change only by canister upgrade. Nobody amends
// them at runtime, including governance, and no installed extension can weaken
// them: Cedar's `forbid` beats every `permit` unconditionally, so an extension
// that installs `permit (principal, action, resource)` still cannot get past
// these. That property is what makes runtime-installable policies safe enough to
// allow at all.
//
// Kept deliberately small. Each rule below is either something the realm already
// refuses in Python today, or an escalation path that must be closed structurally
// rather than by review. Rules that merely express *who may do what* belong in
// the realm's ordinary policy set, not here — a guardrail nobody can override is
// the wrong place for a decision that might legitimately change.
//
// `context.extension` is set by the host for any call originating inside a
// sandboxed extension, and absent for host code. It is the only way a policy can
// tell the two apart, since both arrive as the same principal.
//
// G1 and G2 fail *open* without `context.extension`: an extension-originated
// call that arrives without it is indistinguishable from host code and passes
// both rules. So setting it is the enforcement point, not incidental plumbing.
// `core/call_origin.py` carries it ambiently and its `dispatch` takes the origin
// positionally, making omission a TypeError rather than a silent pass; a source
// scan in CI stops new code from reaching the verb registries directly and
// re-opening the hole. See `tests/backend/test_call_origin.py`.

// ---------------------------------------------------------------------------
// G1 — An extension may never write core realm state.
// ---------------------------------------------------------------------------
//
// Extensions write their own entities, which live in their own `ext_*`
// namespace and are untouched by this rule. Core state is different: every
// write to it goes through a host verb, which is where the checks and the audit
// trail live. This is also why the 26 verbs that do more than read or write stay
// in Python — `procurement.award` writes a Proposal, but it does so host-side.
//
// Stated as a blanket refusal rather than a list of protected types on purpose.
// A list is a promise to keep it updated, and the first entity someone forgets
// to add is the one that gets written.
forbid (
    principal,
    action in [Realm::Action::"write"],
    resource
)
when { context has extension };

// ---------------------------------------------------------------------------
// G2 — An extension may never read the data that decides authorization.
// ---------------------------------------------------------------------------
//
// Reading profiles and permissions is the reconnaissance step of an escalation:
// it tells an attacker which principals are worth targeting and which rules
// stand in the way. No extension has a legitimate need for it — an extension
// that wants to know whether the caller may do something asks the host, which
// answers for that specific question without handing over the rulebook.
forbid (
    principal,
    action,
    resource
)
when {
    context has extension
    && (resource is Realm::UserProfile || resource is Realm::Permission)
};

// ---------------------------------------------------------------------------
// G3 — An appellant may never decide their own appeal.
// ---------------------------------------------------------------------------
//
// Ported from `core/justice/cases.py`, which raises PermissionError for exactly
// this case regardless of the caller's role. It belongs here rather than in the
// ordinary policy set because it must hold even for a realm admin: an appeal is
// the mechanism for challenging a decision, and it is worth nothing if the
// person being challenged can decide it.
forbid (
    principal,
    action == Realm::Action::"appeal.decide",
    resource is Realm::Appeal
)
when { resource has appellant && resource.appellant == principal };
