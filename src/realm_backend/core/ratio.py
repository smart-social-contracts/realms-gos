"""The one scale of every ratio: hundred-millionths, as GGG's model has it (Conventions, "Ratios").

100 % is ``ONE`` (100 000 000), 1 % is ``PERCENT`` and 0.123456 % is 123 456. Quorums, thresholds, allocation
shares, tender weights and tax rates are integers on this scale, so there is one form for every value.

The previous release held them in whole percents and basis points. ``upgrade_args`` and the helpers under it turn
what it stored into the current form, for the rows that migrate when they are read and for the operation calls it
left waiting (a proposal's action, a federal vote's, a task step), which replay with their old arguments.
"""
from core.db import Fail

ONE = 100_000_000
PERCENT = ONE // 100
BASIS_POINT = ONE // 10_000


def refuse_renamed(args, renamed):
    """Refuse an argument a caller still sends under its old name. Left unread it would count as no quorum or a rate of
    zero, silently: ``renamed`` maps each old name to its new one."""
    for old, new in renamed.items():
        if old in args:
            raise Fail(f"{old} is now {new}, in hundred-millionths: 50 % is {50 * PERCENT}")


def shares_from_percents(percents):
    """An allocation rule held as a map of fund to whole percents, as the list of ``{fund, share}`` it is now."""
    return [{"fund": fund, "share": percent * PERCENT} for fund, percent in percents.items()]


def upgrade_tax_document(document):
    """A tax policy document with its rates in hundred-millionths under ``rate``; one written in basis points under
    ``rate_bp`` is converted, a converted one is returned as it is."""
    taxes = []
    for tax in document.get("taxes") or []:
        tax = {**tax, "brackets": [_rated(b) for b in tax["brackets"]]} if tax.get("brackets") else tax
        taxes.append(_rated(tax))
    return {**document, "taxes": taxes} if "taxes" in document else document


def _rated(tax_or_bracket):
    if "rate_bp" not in tax_or_bracket:
        return tax_or_bracket
    rest = {k: v for k, v in tax_or_bracket.items() if k != "rate_bp"}
    return {**rest, "rate": tax_or_bracket["rate_bp"] * BASIS_POINT}


def upgrade_federal_rule(rule):
    """A federal vote's rule with its threshold in hundred-millionths. The threshold is below 100 % and the old one a
    whole percent below 100, so a value under 100 is an old one (no real threshold is under 0.0001 %)."""
    threshold = rule.get("threshold")
    return {**rule, "threshold": threshold * PERCENT} if isinstance(threshold, int) and threshold < 100 else rule


def upgrade_args(op, args):
    """The arguments of operation ``op`` as the previous release took them, in the current form; arguments already in it
    pass unchanged. A call inside a call (a federal vote's action, a task's steps) is converted too."""
    args = dict(args or {})
    if op == "governance_update":
        for old, new in (("threshold", "voting_threshold"), ("quorum_percent", "voting_quorum")):
            if old in args:
                args[new] = args.pop(old) * PERCENT
    elif op == "department_set_policy" and "quorum_percent" in args:
        args["quorum"] = args.pop("quorum_percent") * PERCENT
    elif op == "tax_policy_draft" and isinstance(args.get("document"), dict):
        args["document"] = upgrade_tax_document(args["document"])
    elif op == "allocation_rule_draft" and isinstance(args.get("rules"), dict):
        args["rules"] = shares_from_percents(args["rules"])
    elif op == "tender_create" and isinstance(args.get("rubric"), dict) and sum(args["rubric"].values()) == 100:
        args["rubric"] = {criterion: weight * PERCENT for criterion, weight in args["rubric"].items()}
    elif op == "quarter_configure" and isinstance(args.get("rule"), dict):
        args["rule"] = upgrade_federal_rule(args["rule"])
    if isinstance(args.get("action"), dict) and "op" in args["action"]:
        args["action"] = upgrade_call(args["action"])
    if isinstance(args.get("steps"), list):
        args["steps"] = [upgrade_call(step) for step in args["steps"]]
    return args


def upgrade_call(call):
    """A stored call, ``{op, args}``, with its arguments in the current form."""
    return {**call, "args": upgrade_args(call.get("op"), call["args"])} if call.get("args") else call
