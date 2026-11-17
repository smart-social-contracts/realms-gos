"""The ``realms`` package as the realm canister sees it: only ``realms.legislation``, the library
every chapter of a layered codex imports (docs/codices/layered-codex.md).

The product CLI's ``realms`` package (cli/realms) shares ``realms.legislation`` through a symlink, so
a laptop and the realm run one implementation. Nothing under this package imports the CLI's
dependencies, and nothing here imports the canister's other modules either.
"""
