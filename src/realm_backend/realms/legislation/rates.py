"""Rates and money, on GGG's one scale and one rounding rule (GGG docs/layered-codex.md §3.3).

Every ratio is an integer in hundred-millionths: 100 % is 100 000 000, 1 % is 1 000 000 and 0.123456 %
is 123 456. Integers inside, decimals for people: a chapter writes a rate as the legislation does,
``pct("1.10")``, and the library stores the integer.

Money computed from a rate is **rounded to the cent**, an exact half away from zero, because
legislation states amounts in cents. A Money amount is an integer in the token's smallest unit, so a
cent is ``10 ** (decimals - 2)`` of those units and the rounded result is a multiple of it. Rounding
therefore needs the token's ``decimals``: ``times`` takes them where they are known (a Money carries
them) and refuses to guess when they are not. GGG states the money kind so every implementation rounds
the same way; the token's decimals come from GGG too (ggg#2).
"""

from realms.legislation.errors import LegislationError
from realms.legislation.sealed import Sealed

SCALE = 100_000_000  # one, in hundred-millionths: 100 %
PERCENT = SCALE // 100  # 1 %
RATE_DECIMALS = (
    6  # digits after the point a percentage may carry: 0.123456 % is 123 456
)
MAX_DECIMALS = 255  # a token's decimals, as GGG bounds them (token_register); a chapter's Money takes no more


def pct(text):
    """A rate as the legislation writes it: ``pct("1.10")`` is 1.10 %, the integer 1 100 000.

    Takes the decimal as a string (or an integer), never a float. More than six decimals do not fit
    the scale and are refused rather than rounded."""
    if type(text) not in (str, int):
        raise LegislationError(f'write a rate as a string, pct("1.10"), not {text!r}')
    text = str(text).strip()
    sign, digits = 1, text
    if digits[:1] in ("-", "+"):
        sign, digits = (-1 if digits[0] == "-" else 1), digits[1:]
    whole, _, fraction = digits.partition(".")
    if (
        not (whole or fraction)
        or not (whole + fraction).isdigit()
        or not (whole + fraction).isascii()
    ):
        raise LegislationError(f"{text!r} is not a decimal rate")
    if len(fraction) > RATE_DECIMALS:
        raise LegislationError(
            f"{text!r} has more than {RATE_DECIMALS} decimals: it does not fit the scale of hundred-millionths"
        )
    return sign * (
        int(whole or "0") * PERCENT + int(fraction.ljust(RATE_DECIMALS, "0") or "0")
    )


def cent(decimals):
    """One cent, in the token's smallest units: ``10 ** (decimals - 2)``. A token with fewer than two
    decimals cannot express a cent finer than its own unit, so rounding falls back to that unit.
    """
    if type(decimals) is not int or not 0 <= decimals <= MAX_DECIMALS:
        raise LegislationError(
            f"a token's decimals is an integer from 0 to {MAX_DECIMALS}, not {decimals!r}"
        )
    return 10 ** (decimals - 2) if decimals >= 2 else 1


def times(units, rate, decimals):
    """``units`` (an integer amount in the token's smallest unit) times ``rate`` (hundred-millionths),
    rounded to the nearest cent, an exact half away from zero: 2.345 becomes 2.35 and -2.345 becomes -2.35.

    The result is a multiple of ``cent(decimals)`` smallest units."""
    for value in (units, rate):
        if type(value) is not int:
            raise LegislationError(f"money times a rate takes integers, not {value!r}")
    step = cent(decimals)
    product = units * rate
    whole_cents, remainder = divmod(abs(product), SCALE * step)
    if remainder * 2 >= SCALE * step:
        whole_cents += 1
    return (whole_cents if product >= 0 else -whole_cents) * step


class Money(metaclass=Sealed):
    """An amount in the smallest unit of its token, as GGG's money is stored, with the token's
    ``decimals`` so ``times`` can round to the cent. GGG supplies both; a Money made without decimals
    can be added and compared but cannot be multiplied by a rate until its decimals are known.

    ``times(rate)`` applies GGG's one rounding rule and takes no rounding argument. Money adds to and
    compares with Money only, and never changes once written."""

    __slots__ = ("units", "decimals")

    def __init__(self, units, decimals=None):
        if type(units) is not int:
            raise LegislationError(
                f"money is an integer amount in the smallest unit, not {units!r}"
            )
        if decimals is not None:
            cent(decimals)  # validates
        object.__setattr__(self, "units", units)
        object.__setattr__(self, "decimals", decimals)

    def __setattr__(self, name, value):
        raise LegislationError("money never changes: compute a new amount")

    def times(self, rate):
        if self.decimals is None:
            raise LegislationError(
                "money times a rate rounds to the cent, so it needs the token's decimals: Money(units, decimals)"
            )
        return Money(times(self.units, rate, self.decimals), self.decimals)

    def to_json(self):
        return self.units

    def _decimals(self, other):
        """The decimals the result carries: the known one, or an error if the two are known and differ."""
        if (
            self.decimals is not None
            and other.decimals is not None
            and self.decimals != other.decimals
        ):
            raise LegislationError(
                "two amounts in tokens of different decimals cannot be combined"
            )
        return self.decimals if self.decimals is not None else other.decimals

    def __add__(self, other):
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.units + other.units, self._decimals(other))

    def __sub__(self, other):
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.units - other.units, self._decimals(other))

    def __neg__(self):
        return Money(-self.units, self.decimals)

    def _units(self, other):
        return other.units if isinstance(other, Money) else NotImplemented

    def __eq__(self, other):
        units = self._units(other)
        return units if units is NotImplemented else self.units == units

    def __lt__(self, other):
        units = self._units(other)
        return units if units is NotImplemented else self.units < units

    def __le__(self, other):
        units = self._units(other)
        return units if units is NotImplemented else self.units <= units

    def __gt__(self, other):
        units = self._units(other)
        return units if units is NotImplemented else self.units > units

    def __ge__(self, other):
        units = self._units(other)
        return units if units is NotImplemented else self.units >= units

    def __hash__(self):
        return hash(("Money", self.units))

    def __int__(self):
        return self.units

    def __repr__(self):
        return (
            f"Money({self.units})"
            if self.decimals is None
            else f"Money({self.units}, {self.decimals})"
        )
