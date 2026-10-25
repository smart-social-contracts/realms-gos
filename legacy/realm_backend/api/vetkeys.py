"""VetKeys API — on-chain encryption key derivation for user private data.

Provides async helper functions that call the IC management canister's
vetKD (Verifiably Encrypted Threshold Key Derivation) API.

The flow:
  1. Frontend calls ``get_vetkey_public_key`` to obtain the IBE public key.
  2. Frontend generates an ephemeral ``TransportSecretKey`` (BLS12-381 G1).
  3. Frontend calls ``derive_vetkey`` with the transport public key.
  4. Canister proxies the call to the management canister; the derived key
     is encrypted under the transport public key so no single node sees it.
  5. Frontend decrypts → symmetric AES-GCM key → encrypts/decrypts private data.

Context construction:
  ``len(domain_sep) || domain_sep || caller_principal_str``
  This binds every derived key to *this application* and *this user*.
"""

from _cdk import Async, ic
from ic_python_logging import get_logger

logger = get_logger("api.vetkeys")

# Master key hosted on the IC vetKD subnet.
# "test_key_1" — 13-node test subnet (fuqsr).  Switch to "key_1" for
# production (34-node fiduciary subnet pzp6e).
VETKD_KEY_NAME = "test_key_1"

DOMAIN_SEPARATOR = b"realms"


def _build_context_hex(caller_principal_str: str) -> str:
    """Return the per-user vetKD context as a hex string.

    Format: ``<1-byte len><domain_separator><scope>``
    where *scope* is the UTF-8 encoding of the caller's principal string.

    Used by the legacy per-user encryption scheme (one vetkd_public_key call
    per principal).
    """
    ds = DOMAIN_SEPARATOR
    scope = caller_principal_str.encode()
    ctx = bytes([len(ds)]) + ds + scope
    return "".join(f"{b:02x}" for b in ctx)


def _build_root_context_hex() -> str:
    """Return the *shared root* vetKD context as a hex string.

    Format: ``<1-byte len><domain_separator>`` (no per-principal scope).

    This single context is shared by all members.  Recipients are
    distinguished by the vetKD ``input`` (their principal text) instead of by
    the context, which lets the frontend derive every recipient's IBE public
    key locally from one fetched root key — no per-recipient management call.
    """
    ds = DOMAIN_SEPARATOR
    ctx = bytes([len(ds)]) + ds
    return "".join(f"{b:02x}" for b in ctx)


def get_root_public_key() -> Async[dict]:
    """Retrieve the vetKD public key for the shared *root* context.

    The same key for every member; the frontend fetches it once and IBE-wraps
    DEKs to each recipient using the recipient's principal as the IBE identity.

    Returns ``{"success": True, "public_key_hex": "..."}`` on success.
    """
    ctx_hex = _build_root_context_hex()
    logger.info(f"vetkd_public_key (root) ctx_len={len(ctx_hex)//2}")

    ctx_blob = _hex_to_blob_escaped(ctx_hex)
    args = ic.candid_encode(
        f'(record {{ canister_id = null; '
        f'context = blob "{ctx_blob}"; '
        f'key_id = record {{ curve = variant {{ bls12_381_g2 = null }}; '
        f'name = "{VETKD_KEY_NAME}" }} }})'
    )

    result = yield ic.call_raw("aaaaa-aa", "vetkd_public_key", args, 26_000_000_000)

    if hasattr(result, "Ok") and result.Ok is not None:
        decoded = ic.candid_decode(result.Ok)
        pk_hex = _extract_blob_hex(decoded, "public_key")
        logger.info(f"vetkd_public_key (root) OK (len={len(pk_hex)//2 if pk_hex else 0})")
        return {"success": True, "public_key_hex": pk_hex}
    else:
        err = str(getattr(result, "Err", result))
        logger.error(f"vetkd_public_key (root) failed: {err}")
        return {"success": False, "error": err}


def derive_vetkey_for_sharing(
    caller_principal: str,
    transport_public_key_hex: str,
) -> Async[dict]:
    """Derive an encrypted vetKey for *caller_principal* under the root context.

    The vetKD ``input`` is the caller's principal text (UTF-8). Because the
    management canister binds ``input`` to ``ic.caller()`` here, a caller can
    only ever derive the key for their **own** identity — so they can only
    decrypt IBE ciphertexts addressed to their principal.

    Returns ``{"success": True, "encrypted_key_hex": "..."}`` on success.
    """
    ctx_hex = _build_root_context_hex()
    input_hex = caller_principal.encode().hex()
    return _derive_vetkey_raw(ctx_hex, transport_public_key_hex, input_hex)


def get_vetkey_public_key(caller_principal: str) -> Async[dict]:
    """Retrieve the vetKD public key for *caller_principal*'s context.

    Returns ``{"success": True, "public_key_hex": "..."}`` on success.
    """
    ctx_hex = _build_context_hex(caller_principal)
    logger.info(f"vetkd_public_key for {caller_principal} (ctx len={len(ctx_hex)//2})")

    ctx_blob = _hex_to_blob_escaped(ctx_hex)
    args = ic.candid_encode(
        f'(record {{ canister_id = null; '
        f'context = blob "{ctx_blob}"; '
        f'key_id = record {{ curve = variant {{ bls12_381_g2 = null }}; '
        f'name = "{VETKD_KEY_NAME}" }} }})'
    )

    result = yield ic.call_raw("aaaaa-aa", "vetkd_public_key", args, 26_000_000_000)

    if hasattr(result, "Ok") and result.Ok is not None:
        decoded = ic.candid_decode(result.Ok)
        # decoded is a Candid record; convert the inner blob to hex
        pk_hex = _extract_blob_hex(decoded, "public_key")
        logger.info(f"vetkd_public_key OK (len={len(pk_hex)//2 if pk_hex else 0})")
        return {"success": True, "public_key_hex": pk_hex}
    else:
        err = str(getattr(result, "Err", result))
        logger.error(f"vetkd_public_key failed: {err}")
        return {"success": False, "error": err}


def derive_vetkey(
    caller_principal: str,
    transport_public_key_hex: str,
    input_hex: str = "",
) -> Async[dict]:
    """Derive an encrypted vetKey for *caller_principal*.

    The management canister encrypts the derived key under the caller's
    *transport_public_key* so the plaintext key never leaves the subnet.

    Returns ``{"success": True, "encrypted_key_hex": "..."}`` on success.
    """
    ctx_hex = _build_context_hex(caller_principal)
    return _derive_vetkey_raw(ctx_hex, transport_public_key_hex, input_hex)


def _derive_vetkey_raw(
    ctx_hex: str,
    transport_public_key_hex: str,
    input_hex: str = "",
) -> Async[dict]:
    """Low-level vetkd_derive_key call for a pre-built context + input."""
    tpk_hex = transport_public_key_hex.strip()
    inp_hex = input_hex.strip() if input_hex else ""

    logger.info(
        f"vetkd_derive_key (ctx={len(ctx_hex)//2}B, tpk={len(tpk_hex)//2}B, "
        f"input={len(inp_hex)//2}B)"
    )

    ctx_blob = _hex_to_blob_escaped(ctx_hex)
    tpk_blob = _hex_to_blob_escaped(tpk_hex)
    inp_blob = _hex_to_blob_escaped(inp_hex) if inp_hex else ""
    args = ic.candid_encode(
        f'(record {{ '
        f'input = blob "{inp_blob}"; '
        f'context = blob "{ctx_blob}"; '
        f'key_id = record {{ curve = variant {{ bls12_381_g2 = null }}; '
        f'name = "{VETKD_KEY_NAME}" }}; '
        f'transport_public_key = blob "{tpk_blob}" }})'
    )

    result = yield ic.call_raw("aaaaa-aa", "vetkd_derive_key", args, 54_000_000_000)

    if hasattr(result, "Ok") and result.Ok is not None:
        decoded = ic.candid_decode(result.Ok)
        ek_hex = _extract_blob_hex(decoded, "encrypted_key")
        logger.info(f"vetkd_derive_key OK (len={len(ek_hex)//2 if ek_hex else 0})")
        return {"success": True, "encrypted_key_hex": ek_hex}
    else:
        err = str(getattr(result, "Err", result))
        logger.error(f"vetkd_derive_key failed: {err}")
        return {"success": False, "error": err}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hex_to_blob_escaped(hex_str: str) -> str:
    """Convert a hex string to Candid blob escape format.

    E.g. ``"aabb01"`` → ``"\\aa\\bb\\01"``.

    Without this conversion, ``blob "aabb01"`` would create **6** literal
    ASCII bytes instead of the intended **3** binary bytes.
    """
    h = hex_str.strip()
    parts = []
    for i in range(0, len(h), 2):
        parts.append("\\" + h[i:i+2])
    return "".join(parts)

def _extract_blob_hex(decoded, field_name: str) -> str:
    """Best-effort extraction of a blob field from a Candid-decoded value.

    ``ic.candid_decode`` may return a dict, a record object, or a string
    representation.  We try dict access first, then attribute access, then
    parse the Candid text representation to extract blob bytes.
    """
    # Dict-like (most common inside canister)
    if isinstance(decoded, dict) and field_name in decoded:
        raw = decoded[field_name]
        if isinstance(raw, (bytes, bytearray)):
            return raw.hex()
        return str(raw)

    # Attribute access
    raw = getattr(decoded, field_name, None)
    if raw is not None:
        if isinstance(raw, (bytes, bytearray)):
            return raw.hex()
        return str(raw)

    # Candid text representation — ic.candid_decode may return a string
    # like:  (record { 881_350_601 = blob "\84\24\4f..." })
    # The field names are Candid hashes, not the original names.
    # Extract the first blob value from the text.
    text = str(decoded)
    blob_hex = _parse_candid_blob(text)
    if blob_hex:
        return blob_hex

    # Last resort — return string repr (will likely fail downstream)
    logger.warning(f"_extract_blob_hex: could not extract '{field_name}' from: {text[:200]}")
    return text


def _parse_candid_blob(text: str) -> str:
    """Extract the first blob value from a Candid text representation.

    Handles the escaped format: blob "\\xx\\xx..." where \\xx are hex bytes.
    Also handles mixed printable/escaped: blob "hello\\00world".
    Returns a hex string, or empty string if no blob found.

    Uses only basic string ops (no ``re``) because the IC Python runtime
    ships a stripped-down ``re`` module.
    """
    marker = 'blob "'
    start = text.find(marker)
    if start == -1:
        return ""
    start += len(marker)

    # Find the closing quote (handle escaped quotes)
    end = start
    while end < len(text):
        ch = text[end]
        if ch == '"':
            break
        if ch == '\\' and end + 1 < len(text):
            end += 2  # skip escaped char
        else:
            end += 1

    raw = text[start:end]
    result = []
    i = 0
    while i < len(raw):
        if raw[i] == '\\' and i + 2 < len(raw):
            hex_byte = raw[i+1:i+3]
            try:
                result.append(f"{int(hex_byte, 16):02x}")
                i += 3
                continue
            except ValueError:
                pass
        # Literal character
        result.append(f"{ord(raw[i]):02x}")
        i += 1

    return "".join(result)
