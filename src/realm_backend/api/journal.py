"""The operations of the realm's official journal (docs/codices/layered-codex.md §9.6). The rules are in core.journal."""

import json

from api import quarters
from core import chain, journal, journal_signing
from core.db import Conflict, Fail
from core.op import op


@op(None, read=True, public=True)
def journal_issue(c, args):
    """The realm's official journal for ``day`` (``YYYY-MM-DD`` on the capital's clock, default today), or for the issue
    with the verification ``code``: the ``issue`` with the ``realm``'s name, its ``number``, counted from the journal's
    first day, and the day's ``entries``. Every day from the first to today has an issue, entries or not; a day after
    today or before the first one is not found. The issue's ``status`` is ``provisional`` for today, ``signed`` for a
    day the realm's canister has signed, with its ``proof`` (docs/codices/journal-signature.md), and ``unsigned`` for a
    closed day whose signature is not made yet. A quarter issues nothing: ``issued`` is false, there is no ``issue`` and
    ``capital`` is the canister whose journal it is (``journal_capital_issue`` reads it from there). Returns ``issued``,
    ``capital`` and ``issue``."""
    if not journal.publishes():
        return {"issued": False, "capital": quarters.capital()}
    if args.get("day") and args.get("code"):
        raise Fail("give a day or a verification code, not both")
    day = journal_signing.day_of_code(args["code"]) if args.get("code") else args.get("day")
    return {"issued": True, "capital": chain.self_id(), "issue": journal_signing.shown(journal.issue(day))}


@op("realm.data_view", only_reads=True)
def journal_capital_issue(c, args):
    """On a quarter, the capital's ``issue`` for ``day`` (as ``journal_issue`` takes it), asked of the capital: a read
    cannot call another canister, so a quarter shows its capital's journal through this update, which changes nothing
    and leaves no audit event. Returns ``issued`` (false: the quarter issues nothing), ``capital`` and ``issue``. A realm
    that is not a quarter is in Conflict."""
    if journal.publishes():
        raise Conflict("this realm issues its own journal: read it with journal_issue")
    capital = quarters.capital()
    asked = {}
    if args.get("day"):
        journal.day_number(args["day"])  # a day the capital would refuse is refused here, without the call
        asked = {"day": args["day"]}
    reply = json.loads((yield from chain.call_op(capital, "journal_issue", asked)) or "{}")
    if not reply.get("ok"):
        raise Fail(f"the capital's journal: {reply.get('error')}")
    return {"issued": False, "capital": capital, "issue": reply["issue"]}
