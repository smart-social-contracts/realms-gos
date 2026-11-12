"""The documents of the realm's official journal and their signatures: the first kind of document of core.signing
(docs/codices/journal-signature.md).

An issue's subject is its day and its body is its number, the realm's zone and its entries as stored. The kind is
chained: an issue links to the one before it by digest. A sweep every hour makes the days that have closed into
documents, oldest first, as many as it can in a batch, whether or not the day before is signed: that costs SHA-256 and a
call that costs no cycles, and the chain of digests it keeps is what lets one signature vouch for every day before it.

A day is signed when someone asks (``request``), at most once, ever: what the realm spends on signatures is never more
than signing every day would cost. A realm that says so (``Realm.journal_signing`` is ``daily``) signs each day as the
sweep makes it. A call that fails, a realm short of cycles or an upgrade only delays a document or a signature. Nothing
else in the realm waits for it.
"""

from core import chain, journal, signing
from core.db import Conflict, NotFound, Unavailable

KIND = "journal"
SWEEP = 3600  # seconds between two sweeps
BATCH_DAYS = 31  # the most days one batch covers: a batch is made in one message


def next_day():
    """The day to make next: the one after the last made, or the journal's first day."""
    head = journal.sealed_head()
    return journal.day_text(journal.day_number(head["subject"]) + 1) if head else journal.first_day()


def closed(day):
    """Whether ``day`` is over on the realm's clock. The open day is never made into a document."""
    return journal.day_number(day) < journal.day_number(journal.local_day(chain.now()))


def sealed(day):
    """Whether the document of ``day`` is made: the days are made in order, so it is when the last made is not before it."""
    head = journal.sealed_head()
    return bool(head) and journal.day_number(day) <= journal.day_number(head["subject"])


def closed_days():
    """The days that have closed and are not made, oldest first, at most ``BATCH_DAYS``."""
    days, day = [], next_day()
    while closed(day) and len(days) < BATCH_DAYS:
        days.append(day)
        day = journal.day_text(journal.day_number(day) + 1)
    return days


def body(issue):
    """What an issue says, as the realm signs it (core.journal.issue): the facts, never as rendered. The realm's name is
    left out: its canister id is its identity."""
    return {"number": issue["number"], "timezone": issue["timezone"], "entries": issue["entries"]}


def make_closed():
    """Make the days that have closed into documents, a batch at a time, until none is left or a batch fails: the failure
    is kept (core.signing.failures) and the next sweep tries again. The days made. A generator."""
    made = []
    while closed_days():
        batch = yield from signing.make(KIND, True, closed_days, lambda day: body(journal.issue(day)))
        if not batch:
            break
        made += batch
    return made


def sign_made(days):
    """Sign ``days`` (those the sweep has made now and any whose signature failed before) in batches, until all are or a
    batch fails. A generator."""
    failed = (signing.failures().get(KIND) or {}).get("subjects", [])
    waiting = list(dict.fromkeys([*failed, *days]))
    for first in range(0, len(waiting), BATCH_DAYS):
        if (yield from signing.sign_batch(KIND, waiting[first:first + BATCH_DAYS])) is None:
            return


def sweep():
    """Make what has closed, if the realm issues a journal, and sign it if the realm signs daily, and look again in an
    hour. The next sweep is set first, so that one that fails leaves it set. A generator."""
    chain.set_timer(SWEEP, sweep)
    if journal.publishes():
        made = yield from make_closed()
        if journal.signs_daily():
            yield from sign_made(made)


def resume():
    """Timers do not survive an upgrade, and a new realm has none: the sweeps begin at every start of the canister
    (setup.start), the first at once, so that a realm upgraded onto this release makes its closed days now."""
    chain.set_timer(0, sweep)


def request(day):
    """Sign the issue of ``day`` now if nobody has (core.signing.request), for anyone who asks. The day must have closed
    on the realm's clock and not be before the journal's first day; one the sweep has not made yet (it closed within the
    hour) is made first. ``(reply, signed)``: the reply is the ``day`` with its ``status``, ``signed`` and the ``proof``,
    or ``pending`` while another call is signing it; ``signed`` is whether this call made the signature. A generator."""
    if not journal.publishes():
        raise Conflict("this realm issues no journal: it is a quarter, and its capital's is signed where it is issued")
    first = journal.first_day()
    if journal.day_number(day) < journal.day_number(first):
        raise NotFound(f"there is no issue for {day}: the journal begins on {first}")
    if not sealed(day):
        if not closed(day):
            raise Conflict(f"the issue of {day} is not closed: it is signed once its day is over on the realm's clock")
        yield from make_closed()
        if not sealed(day):
            raise Unavailable(f"the issue of {day} could not be made: "
                              f"{(signing.failures().get(KIND) or {}).get('error', 'nothing was made')}")
    proof, signed = yield from signing.request(KIND, day)
    reply = {"day": day, "status": "signed", "proof": proof} if proof else {"day": day, "status": "pending"}
    return reply, signed


def day_of_code(code):
    """The day of the issue with the verification ``code`` (core.signing.find)."""
    kind, day = signing.find(code)
    if kind != KIND:
        raise NotFound("that verification code is not an issue of the journal")
    return day


def shown(issue):
    """``issue`` (core.journal.issue) with its ``status``: ``signed``; ``provisional``, the open day; or ``unsigned``, a
    closed day with no signature, which anyone may ask for (``request``). A signed one has its ``proof``
    (core.signing.proof)."""
    proof = signing.proof(KIND, issue["day"])
    if proof is not None and proof["signature"]:
        return {**issue, "status": "signed", "proof": proof}
    return {**issue, "status": "provisional" if issue["day"] == issue["today"] else "unsigned"}
