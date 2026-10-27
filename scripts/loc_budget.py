#!/usr/bin/env python3
"""Fail when src/realm_backend grows past its line budget (docs/project/refactor-plan.md)."""

import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "realm_backend"
TOTAL = 10_000


def main():
    sizes = {p.relative_to(SRC).as_posix(): len(p.read_text().splitlines())
             for p in SRC.rglob("*.py") if "tests" not in p.parts and "__pycache__" not in p.parts}
    total = sum(sizes.values())
    for name, n in sorted(sizes.items(), key=lambda kv: -kv[1]):
        print(f"{n:6}  {name}")
    print(f"{total:6}  total (budget {TOTAL})")
    if total > TOTAL:
        sys.exit(f"over budget by {total - TOTAL} lines")


if __name__ == "__main__":
    main()
