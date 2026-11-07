"""The one scale of every ratio: hundred-millionths, as GGG's model has it (Conventions, "Ratios").

100 % is ``ONE`` (100 000 000), 1 % is ``PERCENT`` and 0.123456 % is 123 456. Quorums, thresholds, allocation
shares, tender weights and tax rates are integers on this scale, so there is one form for every value.
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
