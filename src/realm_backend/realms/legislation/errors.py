"""What the library refuses, and how it says where."""


class LegislationError(Exception):
    """A chapter, a value or a call the library refuses. The message says why."""


class UnitMismatch(LegislationError):
    """Two Periods of different units were compared. It fails closed: no conversion through the calendar."""


class Refused(LegislationError):
    """A chapter the loader refuses, reported as ``<source>/<domain>.py:<line>: <reason>``, the form the
    checker prints in CI and the realm shows again at install (layered-codex.md §5.5).
    """

    def __init__(self, reason, line=None, file=None):
        super().__init__(reason)
        self.reason, self.line, self.file = reason, line, file

    def __str__(self):
        where = self.file or ""
        if where and self.line:
            where += ":%d" % self.line
        return "%s: %s" % (where, self.reason) if where else self.reason
