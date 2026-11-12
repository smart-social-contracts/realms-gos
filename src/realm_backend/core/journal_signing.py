"""The signature on every closed issue of the realm's official journal: the first kind of document of core.signing
(docs/codices/journal-signature.md).

An issue's subject is its day and its body is its number, the realm's zone and its entries as stored. The kind is
chained: an issue links to the one before it by digest. A sweep every hour signs the days that have closed and are not
signed, oldest first, as one batch (one signature) for as many as it can, so that a call that fails, a realm short of
cycles or an upgrade only delays a signature. Nothing else in the realm waits for it.
"""

from core import chain, journal, signing
from core.db import NotFound

KIND = "journal"
SWEEP = 3600  # seconds between two sweeps
BATCH_DAYS = 31  # the most days one signature covers: a batch is made in one message


def next_day():
    """The day to sign next: the one after the last signed, or the journal's first day."""
    head = journal.signed_head()
    return journal.day_text(journal.day_number(head["subject"]) + 1) if head else journal.first_day()


def closed(day):
    """Whether ``day`` is over on the realm's clock. The open day is never signed."""
    return journal.day_number(day) < journal.day_number(journal.local_day(chain.now()))


def closed_days():
    """The days that have closed and are not signed, oldest first, at most ``BATCH_DAYS``."""
    days, day = [], next_day()
    while closed(day) and len(days) < BATCH_DAYS:
        days.append(day)
        day = journal.day_text(journal.day_number(day) + 1)
    return days


def body(issue):
    """What an issue says, as the realm signs it (core.journal.issue): the facts, never as rendered. The realm's name is
    left out: its canister id is its identity."""
    return {"number": issue["number"], "timezone": issue["timezone"], "entries": issue["entries"]}


def issues(days):
    """The documents of ``days`` as they are now. Signing takes a few calls, and a change of the realm's zone to the
    west in the meantime can put an entry into a day that has closed, or move the first day: a batch whose issues are
    not what they were is not kept (core.signing.sign_batch)."""
    return [(day, body(journal.issue(day))) for day in days]


def sign_closed():
    """Sign the days that have closed, a batch at a time, until none is left or a batch fails: the failure is kept
    (core.signing.failures) and the next sweep tries again. A generator."""
    while days := closed_days():
        if not (yield from signing.sign_batch(KIND, issues(days), chained=True, fresh=lambda: issues(days))):
            return


def sweep():
    """Sign what has closed, if the realm issues a journal, and look again in an hour. The next sweep is set first, so
    that one that fails leaves it set. A generator."""
    chain.set_timer(SWEEP, sweep)
    if journal.publishes():
        yield from sign_closed()


def resume():
    """Timers do not survive an upgrade, and a new realm has none: the sweeps begin at every start of the canister
    (setup.start), the first at once, so that a realm upgraded onto this release signs its closed days now."""
    chain.set_timer(0, sweep)


def day_of_code(code):
    """The day of the issue with the verification ``code`` (core.signing.find)."""
    kind, day = signing.find(code)
    if kind != KIND:
        raise NotFound("that verification code is not an issue of the journal")
    return day


def shown(issue):
    """``issue`` (core.journal.issue) with its ``status``: ``signed``; ``provisional``, the open day; or ``unsigned``, a
    closed day whose signature is not made yet. A signed one has its ``proof`` (core.signing.proof)."""
    proof = signing.proof(KIND, issue["day"])
    if proof is not None:
        return {**issue, "status": "signed", "proof": proof}
    return {**issue, "status": "provisional" if issue["day"] == issue["today"] else "unsigned"}
