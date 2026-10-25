"""CDK compatibility layer.

This module centralizes all imports from the Internet Computer CDK (currently Basilisk).
To switch CDKs, only this file needs to be modified.
"""

from basilisk import *  # noqa: F401, F403
from basilisk import ic  # noqa: F401 - explicit for IDE support

# Re-export management canister sub-package
# In CPython template mode the basilisk.canisters.management subpackage
# is not available, so fall back to inline definitions.
try:
    from basilisk.canisters.management import (  # noqa: F401
        HttpResponse,
        HttpTransformArgs,
        management_canister,
    )
except (ImportError, ModuleNotFoundError):
    from basilisk import Service, Principal  # noqa: F401

    HttpResponse = dict
    HttpTransformArgs = dict
    management_canister = Service(principal=Principal.management_canister())

# In CPython template mode, Variant and Record are plain dict which doesn't
# accept keyword arguments (e.g. total=False) in __init_subclass__.
# Override with subclasses that accept arbitrary kwargs.
try:
    class _TestVariant(Variant, total=False):  # noqa: F405
        pass
except TypeError:
    class Variant(dict):
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

    class Record(dict):
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()
