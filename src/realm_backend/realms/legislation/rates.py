"""Rates and money, on GGG's one scale and one rounding rule (GGG docs/layered-codex.md §3.3).

Every ratio is an integer in hundred-millionths: 100 % is 100 000 000, 1 % is 1 000 000 and 0.123456 %
is 123 456. Integers inside, decimals for people: a chapter writes a rate as the legislation does,
``pct("1.10")``, and the library stores the integer. Money computed from a rate is rounded to the
nearest unit of the currency (the cent), an exact half away from zero, with no other rule.
"""

from realms.legislation.errors import LegislationError
from realms.legislation.sealed import Sealed

SCALE = 100_000_000  # one, in hundred-millionths: 100 %
PERCENT = SCALE // 100  # 1 %
DECIMALS = 6  # digits after the point a percentage may carry: 0.123456 % is 123 456


def pct(text):
    """A rate as the legislation writes it: ``pct("1.10")`` is 1.10 %, the integer 1 100 000.

    Takes the decimal as a string (or an integer), never a float. More than six decimals do not fit
    the scale and are refused rather than rounded."""
    if isinstance(text, bool) or not isinstance(text, (str, int)):
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
    if len(fraction) > DECIMALS:
        raise LegislationError(
            f"{text!r} has more than {DECIMALS} decimals: it does not fit the scale of hundred-millionths"
        )
    return sign * (
        int(whole or "0") * PERCENT + int(fraction.ljust(DECIMALS, "0") or "0")
    )


def times(units, rate):
    """``units`` (an integer amount in the currency's smallest unit) times ``rate`` (hundred-millionths),
    rounded to the nearest unit, an exact half away from zero: 2.345 becomes 2.35 and -2.345 becomes -2.35.
    """
    for value in (units, rate):
        if isinstance(value, bool) or not isinstance(value, int):
            raise LegislationError(f"money times a rate takes integers, not {value!r}")
    product = units * rate
    quotient, remainder = divmod(abs(product), SCALE)
    if remainder * 2 >= SCALE:
        quotient += 1
    return quotient if product >= 0 else -quotient


class Money(metaclass=Sealed):
    """An amount in the smallest unit of its currency (cents for the euro), as GGG's money is stored.

    ``times(rate)`` applies GGG's one rounding rule and takes no rounding argument. Money adds to and
    compares with Money only, and never changes once written."""

    __slots__ = ("units",)

    def __init__(self, units):
        if isinstance(units, bool) or not isinstance(units, int):
            raise LegislationError(
                f"money is an integer amount in the smallest unit, not {units!r}"
            )
        object.__setattr__(self, "units", units)

    def __setattr__(self, name, value):
        raise LegislationError("money never changes: compute a new amount")

    def times(self, rate):
        return Money(times(self.units, rate))

    def to_json(self):
        return self.units

    def _units(self, other):
        return other.units if isinstance(other, Money) else NotImplemented

    def __add__(self, other):
        units = self._units(other)
        return units if units is NotImplemented else Money(self.units + units)

    def __sub__(self, other):
        units = self._units(other)
        return units if units is NotImplemented else Money(self.units - units)

    def __neg__(self):
        return Money(-self.units)

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
        return f"Money({self.units})"
