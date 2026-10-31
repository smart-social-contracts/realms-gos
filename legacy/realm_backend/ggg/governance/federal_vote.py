"""Federation-level vote entities (issue #300).

A federal vote is a question put to the whole realm. Members vote on their
home quarter only — there is no global user registry (issue #156), so each
quarter runs an ordinary local ballot as one "leg" and reports its counts to
the capital. ``FederalVote`` holds the federation-level question; on the
capital it is authoritative, on each quarter it is a mirror of what the
capital opened.

``FederalVoteLeg`` is that per-quarter ballot row. On the capital there is
one leg per quarter; on a quarter there is exactly one leg (its own). The
composite ``leg_key`` gives O(1) idempotent upsert when federation messages
are replayed.

Each leg stores its own ``vote_hash`` — a digest of the frozen action, rule,
and deadline — so a quarter executes only what its members actually voted on.
Without that binding the capital could swap the action after ballots opened.
"""

from ic_python_db import Boolean, Entity, Integer, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.federal_vote")

VOTE_STATUS_OPEN = "open"
VOTE_STATUS_ADOPTED = "adopted"
VOTE_STATUS_REJECTED = "rejected"
VOTE_STATUS_NO_QUORUM = "no_quorum"
VOTE_STATUS_EXPIRED = "expired"

LEG_STATUS_OPEN = "open"
LEG_STATUS_REPORTED = "reported"
LEG_STATUS_ARMED = "armed"
LEG_STATUS_EXECUTED = "executed"
LEG_STATUS_FAILED = "failed"
LEG_STATUS_EXPIRED = "expired"


class FederalVote(Entity, TimestampedMixin):
    __alias__ = "vote_id"
    __version__ = 1

    vote_id = String(max_length=64)
    origin_quarter = String(max_length=64)
    action = String(max_length=2048)
    rule_json = String(max_length=512)
    vote_hash = String(max_length=80)
    deadline = Integer(default=0)
    status = String(max_length=32, indexed=True, default=VOTE_STATUS_OPEN)
    tally_json = String(max_length=2048, default="")
    known_quarters = Integer(default=0)


class FederalVoteLeg(Entity, TimestampedMixin):
    __alias__ = "leg_key"
    __version__ = 1

    leg_key = String(max_length=130)
    vote_id = String(max_length=64, indexed=True)
    quarter_canister_id = String(max_length=64)
    proposal_id = String(max_length=64, default="")
    outcome = String(max_length=32, default="")
    votes_yes = Integer(default=0)
    votes_no = Integer(default=0)
    votes_abstain = Integer(default=0)
    eligible = Integer(default=0)
    reported = Boolean(default=False)
    status = String(max_length=32, indexed=True, default=LEG_STATUS_OPEN)
    vote_hash = String(max_length=80, default="")
    error = String(max_length=256, default="")
