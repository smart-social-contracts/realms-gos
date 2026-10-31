"""Build variant: production. Source of truth for ``src/realm_backend/core/build_variant.py``.

``scripts/pack_realm_backend.py`` copies one variant of this directory over that module before packing, so
the variant that was not chosen is absent from the wasm, not switched off in it. Production is the checked-in
default: a realm flag left in the database, a compromised admin or an extension writing to the Realm entity
find nothing to switch on.
"""

BUILD_VARIANT = "production"


def test_flags_allowed(network, can_test_mode):
    """Whether test mode and test flags may be set: never, in this build."""
    return False
