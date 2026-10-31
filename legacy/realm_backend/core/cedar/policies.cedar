// The realm's ordinary policy set: who may do what, as distinct from the
// rules in guardrails.cedar that must hold no matter what.
//
// The distinction matters. A `forbid` in the guardrails cannot be overridden
// by anything, so it is the wrong place for a decision a realm might
// legitimately want to change. These permits are ordinary rules: a realm
// admin tightens them, and when one is wrong the fix is an edit, not a
// canister upgrade.
//
// Cedar is deny-by-default: without a matching permit a call is refused, and
// with no permit at all the realm refuses everything. So this file exists to
// restore the baseline a realm already had before Cedar — users can act, with
// no policy here granting an extension anything the guardrails do not already
// keep it from.

// Host-originated calls carry no `context.extension` or `context.repl`, so
// this covers ordinary host traffic and leaves extension calls to the
// guardrails and REPL calls to the policies below. The realm's own Python
// checks still run downstream; this is the authorization floor, not the ceiling.
permit (
    principal,
    action,
    resource
)
when { !(context has extension) && !(context has repl) };

// Extensions may read ordinary realm data. Scoped to `read` on purpose: a
// write from an extension is what guardrail G1 refuses outright, and reads of
// authorization data (profiles, permissions) are what G2 refuses. Everything
// left over is the data a realm exists to share.
//
// This is the baseline, not the end state. A realm that does not want
// extensions browsing its data removes this and grants reads per verb or per
// entity instead.
permit (
    principal,
    action in [Realm::Action::"read"],
    resource
)
when { context has extension };

// REPL-originated calls carry `context.repl` and may read any entity type
// except those forbidden below (authorization-sensitive types mirror G2).
permit (
    principal,
    action in [Realm::Action::"read", Realm::Action::"entity.get", Realm::Action::"entity.list"],
    resource
)
when { context has repl };

// REPL must not read authorization-sensitive types — same rationale as G2
// (profiles, permissions) plus registration codes that gate account creation.
forbid (
    principal,
    action,
    resource
)
when {
    context has repl
    && (resource is Realm::UserProfile || resource is Realm::Permission || resource is Realm::RegistrationCode)
};

// REPL create: the create decision runs against a synthetic resource with no
// attributes, so the owner conditions above cannot be evaluated at decision
// time. What makes this safe is host-side stamping: SecureORM._create forces
// the owner field (scalar) or owner relation (via the caller's principal row)
// to the caller before the row exists, so a REPL-created row is always owned
// by its creator. User is absent on purpose: users come from registration,
// not from the REPL.
permit (
    principal,
    action == Realm::Action::"entity.create",
    resource
)
when {
    context has repl
    && (resource is Realm::Human || resource is Realm::Balance
        || resource is Realm::PaymentAccount || resource is Realm::Service
        || resource is Realm::Invoice || resource is Realm::Notification
        || resource is Realm::Proposal || resource is Realm::Vote
        || resource is Realm::Land || resource is Realm::Zone)
};

// REPL owner-write permits: mutations only on rows the caller owns.

permit (
    principal,
    action in [Realm::Action::"write", Realm::Action::"entity.update", Realm::Action::"entity.delete"],
    resource is Realm::User
)
when { context has repl && principal has id && resource has id && resource.id == principal.id };

permit (
    principal,
    action in [Realm::Action::"write", Realm::Action::"entity.create", Realm::Action::"entity.update", Realm::Action::"entity.delete"],
    resource is Realm::Human
)
when { context has repl && principal has id && resource has user && resource.user has id && resource.user.id == principal.id };

permit (
    principal,
    action in [Realm::Action::"write", Realm::Action::"entity.create", Realm::Action::"entity.update", Realm::Action::"entity.delete"],
    resource is Realm::Balance
)
when { context has repl && principal has id && resource has user && resource.user has id && resource.user.id == principal.id };

permit (
    principal,
    action in [Realm::Action::"write", Realm::Action::"entity.create", Realm::Action::"entity.update", Realm::Action::"entity.delete"],
    resource is Realm::PaymentAccount
)
when { context has repl && principal has id && resource has user && resource.user has id && resource.user.id == principal.id };

permit (
    principal,
    action in [Realm::Action::"write", Realm::Action::"entity.create", Realm::Action::"entity.update", Realm::Action::"entity.delete"],
    resource is Realm::Service
)
when { context has repl && principal has id && resource has user && resource.user has id && resource.user.id == principal.id };

permit (
    principal,
    action in [Realm::Action::"write", Realm::Action::"entity.create", Realm::Action::"entity.update", Realm::Action::"entity.delete"],
    resource is Realm::Invoice
)
when { context has repl && principal has id && resource has user && resource.user has id && resource.user.id == principal.id };

permit (
    principal,
    action in [Realm::Action::"write", Realm::Action::"entity.create", Realm::Action::"entity.update", Realm::Action::"entity.delete"],
    resource is Realm::Notification
)
when { context has repl && principal has id && resource has recipient && resource.recipient == principal.id };

permit (
    principal,
    action in [Realm::Action::"write", Realm::Action::"entity.create", Realm::Action::"entity.update", Realm::Action::"entity.delete"],
    resource is Realm::Proposal
)
when { context has repl && principal has id && resource has proposer && resource.proposer has id && resource.proposer.id == principal.id };

permit (
    principal,
    action in [Realm::Action::"write", Realm::Action::"entity.create", Realm::Action::"entity.update", Realm::Action::"entity.delete"],
    resource is Realm::Vote
)
when { context has repl && principal has id && resource has voter && resource.voter has id && resource.voter.id == principal.id };

permit (
    principal,
    action in [Realm::Action::"write", Realm::Action::"entity.create", Realm::Action::"entity.update", Realm::Action::"entity.delete"],
    resource is Realm::Land
)
when { context has repl && principal has id && resource has owner_user && resource.owner_user has id && resource.owner_user.id == principal.id };

permit (
    principal,
    action in [Realm::Action::"write", Realm::Action::"entity.create", Realm::Action::"entity.update", Realm::Action::"entity.delete"],
    resource is Realm::Zone
)
when { context has repl && principal has id && resource has user && resource.user has id && resource.user.id == principal.id };
