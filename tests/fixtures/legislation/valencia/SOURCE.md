A copy of `legislation/` of smart-social-contracts/realms-codices (without its README): the sources of the design's
examples (docs/codices/layered-codex.md §5) with their releases, cases and chapters, including the pending edits to
Spain's `procedure.py` (GGG's fact is `legislation`) and `tax.py` (`compute_tax_assess`). Public and illustrative.

The checksums in `eu/es/releases.json` are those of these files; its `commit` names the commit of the release
before them, which the copy does not carry. `cli/tests/test_legislation_tree.py` loads València from it, and
`tests/ggg/test_legislation_valencia.py` runs its chapters in the realm's runtime. Refresh it by copying the folder
again when the tree changes.
