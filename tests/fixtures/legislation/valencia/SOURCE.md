A copy of `legislation/` of smart-social-contracts/realms-codices (without its README): the sources of the design's
examples (docs/codices/layered-codex.md §5) with their releases, cases and chapters. Public and illustrative.

`cli/tests/test_legislation_tree.py` loads València from it, and `tests/ggg/test_legislation_valencia.py` runs its
chapters in the realm's runtime. The same file also compares it with `codices/legislation`, the `codices` submodule
at the commit this repository pins, and fails if they differ: refresh it by copying the folder again (without the
README) whenever the pin moves.
