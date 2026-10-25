"""Build variant: production. GENERATED FILE — do not edit in place.

Source of truth: ``scripts/build_variants/production.py``. ``pack_realm_backend.py``
copies one of the variants in that directory over
``src/realm_backend/core/build_variant.py`` before packing, so only the selected
implementation is compiled into the WASM. ``scripts/`` is not part of the packed
source tree, which is what makes the other variant *absent* rather than merely
disabled.

This is the production variant: the test-only code paths are not implemented
here, so no runtime setting can reach them. A realm flag left over in the
database, a compromised admin session, or an extension writing directly to the
Realm entity all have nothing to switch on.
"""

BUILD_VARIANT = "production"

# Present only in the test variant; scripts/pack_realm_backend.py greps the
# built WASM for this string and fails a production build that contains it.
TEST_BUILD_SENTINEL = ""


def is_test_build() -> bool:
    return False


def ii_bypass_available() -> bool:
    """Whether Internet Identity may be bypassed with a deterministic keypair."""
    return False


def test_flags_available() -> bool:
    """Whether runtime test flags may be enabled at all in this build."""
    return False
