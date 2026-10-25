"""Host-side justice domain logic (issue #272).

Courts, judges, cases, verdicts, penalties and appeals, plus private
end-to-end-encrypted litigations. The GGG entities and lifecycle functions
(``case_file``, ``case_issue_verdict``, ``penalty_execute`` and friends) already
live in :mod:`ggg.justice`; what moved here from the extension is everything that
decides *who may invoke them and on what*.

That was the bulk of it, and it is not a mechanical port — the in-process version
took identity from its own call arguments in several places. A caller with
``dispute.create`` could file a case naming somebody else as plaintiff, and a
caller with ``resolution.issue`` could issue a verdict attributed to any judge in
the realm, on a case that judge was never assigned to. Those arguments do not
exist here; see :mod:`core.justice.verbs`.
"""

from core.justice.verbs import READ_VERBS, VERBS  # noqa: F401
