"""Instruction counts that tests report, printed as a table at the end of the session (see conftest.py)."""


class Costs:
    def __init__(self):
        self.samples = {}

    def report(self, what, instructions):
        """Record that ``what`` (say "install step: fetch chapters, by canister call") cost ``instructions`` once."""
        self.samples.setdefault(what, []).append(instructions)

    def measure(self, canister, what, code):
        """Run ``code`` (Python source) in ``canister``'s heap through its controller shell, report the instructions it
        spent as ``what`` and return them."""
        out = canister.shell(f"_start = ic.performance_counter(0)\n{code}\nprint(ic.performance_counter(0) - _start)")
        assert "Traceback" not in out, out
        instructions = int(out.strip().splitlines()[-1])
        self.report(what, instructions)
        return instructions

    def table(self):
        """The report as lines of text: what, how many times, the cheapest and the dearest."""
        width = max(len(what) for what in self.samples)
        lines = [f"{'what':<{width}}  {'times':>5}  {'least':>15}  {'most':>15}"]
        for what, counts in sorted(self.samples.items()):
            lines.append(f"{what:<{width}}  {len(counts):>5}  {min(counts):>15,}  {max(counts):>15,}")
        return lines
