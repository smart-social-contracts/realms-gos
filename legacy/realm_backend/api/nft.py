"""NFT API for minting and managing LAND NFTs."""

import json
from typing import Optional

from _cdk import (
    Async,
    CallResult,
    Opt,
    Principal,
    Record,
    Service,
    Tuple,
    Variant,
    Vec,
    blob,
    ic,
    nat,
    null,
    service_update,
    text,
)
from ic_python_logging import get_logger

logger = get_logger("api.nft")


# Candid types for the shared NFT canister interface.  We keep the typed
# Record/Variant classes so the Basilisk CDK can use them, but we also
# provide explicit full Candid type strings on the Service so the Rust
# typed-encoding path is used instead of the text-encoding path.
class NftAccount(Record):
    owner: Principal
    subaccount: Opt[blob]


class MetadataValue(Variant, total=False):
    Int: int
    Nat: nat
    Blob: blob
    Text: text


class MintArg(Record):
    token_id: Opt[nat]
    owner: NftAccount
    metadata: Opt[Vec[Tuple[text, MetadataValue]]]


class GenericError(Record):
    message: text
    error_code: nat


class MintError(Variant, total=False):
    GenericError: GenericError
    SupplyCapReached: null
    Unauthorized: null
    TokenIdAlreadyExists: null


class MintResult(Variant, total=False):
    Ok: nat
    Err: MintError


class ForceTransferArg(Record):
    token_id: nat
    to: NftAccount
    memo: Opt[text]


class FreezeArg(Record):
    token_id: nat
    reason: Opt[text]


class AuthorityError(Variant, total=False):
    NonExistingTokenId: null
    Unauthorized: null
    InvalidRecipient: null
    GenericError: GenericError


class AuthorityResult(Variant, total=False):
    Ok: nat
    Err: AuthorityError


_MINT_ARG_TYPE = (
    "record { token_id : opt nat; "
    "owner : record { owner : principal; subaccount : opt blob }; "
    "metadata : opt vec record { 0 : text; 1 : variant { Text : text; Blob : blob; Nat : nat; Int : int } } }"
)
_MINT_RESULT_TYPE = (
    "variant { Ok : nat; "
    "Err : variant { Unauthorized : null; TokenIdAlreadyExists : null; SupplyCapReached : null; "
    "GenericError : record { error_code : nat; message : text } } }"
)
_FORCE_TRANSFER_ARG_TYPE = (
    "record { token_id : nat; "
    "to : record { owner : principal; subaccount : opt blob }; "
    "memo : opt text }"
)
_FREEZE_ARG_TYPE = "record { token_id : nat; reason : opt text }"
_AUTHORITY_RESULT_TYPE = (
    "variant { Ok : nat; "
    "Err : variant { NonExistingTokenId : null; Unauthorized : null; InvalidRecipient : null; "
    "GenericError : record { error_code : nat; message : text } } }"
)


class NFTService(Service):
    _arg_types = {
        "mint": _MINT_ARG_TYPE,
        "force_transfer": _FORCE_TRANSFER_ARG_TYPE,
        "freeze_token": _FREEZE_ARG_TYPE,
        "unfreeze_token": "nat",
    }
    _return_types = {
        "mint": _MINT_RESULT_TYPE,
        "force_transfer": _AUTHORITY_RESULT_TYPE,
        "freeze_token": _AUTHORITY_RESULT_TYPE,
        "unfreeze_token": _AUTHORITY_RESULT_TYPE,
    }

    @service_update
    def mint(self, arg: MintArg) -> MintResult:
        ...

    @service_update
    def force_transfer(self, arg: ForceTransferArg) -> AuthorityResult:
        ...

    @service_update
    def freeze_token(self, arg: FreezeArg) -> AuthorityResult:
        ...

    @service_update
    def unfreeze_token(self, token_id: nat) -> AuthorityResult:
        ...


def _mint_error_message(error) -> str:
    if error is None:
        return "NFT mint failed"
    if isinstance(error, dict):
        if "GenericError" in error:
            ge = error["GenericError"]
            if isinstance(ge, dict):
                return ge.get("message") or str(ge)
            return str(ge)
        if "SupplyCapReached" in error:
            return "NFT supply cap reached"
        if "Unauthorized" in error:
            return (
                "Unauthorized to mint NFT — the realm backend must be added as an "
                "authorized minter on the NFT canister"
            )
        if "TokenIdAlreadyExists" in error:
            return "Token ID already exists on the NFT canister"
    # Handle Basilisk-decoded Variant objects that expose attributes.
    if hasattr(error, "GenericError"):
        ge = getattr(error, "GenericError")
        if hasattr(ge, "message"):
            return ge.message or str(ge)
        return str(ge)
    if hasattr(error, "SupplyCapReached"):
        return "NFT supply cap reached"
    if hasattr(error, "Unauthorized"):
        return (
            "Unauthorized to mint NFT — the realm backend must be added as an "
            "authorized minter on the NFT canister"
        )
    if hasattr(error, "TokenIdAlreadyExists"):
        return "Token ID already exists on the NFT canister"
    return str(error)


def _validate_principal_text(principal_text: str) -> None:
    """Validate a principal text; raises ValueError with a clear message if invalid."""
    try:
        p = Principal.from_str(principal_text)
        # Decode to bytes; this will throw on invalid base32 characters.
        p._ensure_bytes()
        # Force re-canonicalization so the CRC is verified and compared.
        p._text = None
        canonical = p.to_str()
        if canonical != principal_text:
            raise ValueError(
                f"principal checksum mismatch — did you mean {canonical}? "
                "Make sure you copied the full principal text."
            )
    except Exception as e:
        raise ValueError(f"invalid principal '{principal_text}': {e}")


def _unwrap_mint_result(result) -> dict:
    """Unwrap a CallResult[MintResult] from the typed service call."""
    outer_err = None
    if isinstance(result, dict):
        outer_err = result.get("Err")
    elif hasattr(result, "Err"):
        outer_err = result.Err
    if outer_err is not None:
        logger.error(f"Inter-canister mint call failed: {outer_err}")
        return {"success": False, "error": f"Call failed: {outer_err}"}

    inner = None
    if isinstance(result, dict):
        inner = result.get("Ok")
    elif hasattr(result, "Ok"):
        inner = result.Ok
    if inner is None:
        logger.error(f"Unexpected mint response (Ok is None): {result}")
        return {"success": False, "error": "Unexpected empty response from NFT canister"}

    if isinstance(inner, dict):
        if "Ok" in inner:
            return {"success": True, "token_id": str(inner["Ok"])}
        if "Err" in inner:
            return {"success": False, "error": _mint_error_message(inner["Err"])}
    elif hasattr(inner, "Ok"):
        return {"success": True, "token_id": str(inner.Ok)}
    elif hasattr(inner, "Err"):
        return {"success": False, "error": _mint_error_message(inner.Err)}

    logger.error(f"Unexpected inner mint result: {inner}")
    return {"success": False, "error": f"Unexpected result from NFT canister: {inner}"}


def _authority_error_message(error) -> str:
    if error is None:
        return "NFT authority operation failed"
    def _has(key):
        if isinstance(error, dict):
            return key in error
        return hasattr(error, key)
    if _has("NonExistingTokenId"):
        return "Token does not exist on the NFT canister"
    if _has("Unauthorized"):
        return (
            "Realm is not the registry authority for this token — it must be an "
            "authorized minter and the token's minting authority on the NFT canister"
        )
    if _has("InvalidRecipient"):
        return "Recipient already owns this token"
    if _has("GenericError"):
        ge = error["GenericError"] if isinstance(error, dict) else error.GenericError
        if isinstance(ge, dict):
            return ge.get("message") or str(ge)
        return getattr(ge, "message", None) or str(ge)
    return str(error)


def _unwrap_authority_result(result) -> dict:
    """Unwrap a CallResult[AuthorityResult] from the typed service call."""
    outer_err = None
    if isinstance(result, dict):
        outer_err = result.get("Err")
    elif hasattr(result, "Err"):
        outer_err = result.Err
    if outer_err is not None:
        logger.error(f"Inter-canister authority call failed: {outer_err}")
        return {"success": False, "error": f"Call failed: {outer_err}"}

    inner = None
    if isinstance(result, dict):
        inner = result.get("Ok")
    elif hasattr(result, "Ok"):
        inner = result.Ok
    if inner is None:
        logger.error(f"Unexpected authority response (Ok is None): {result}")
        return {"success": False, "error": "Unexpected empty response from NFT canister"}

    if isinstance(inner, dict):
        if "Ok" in inner:
            return {"success": True, "tx_id": str(inner["Ok"])}
        if "Err" in inner:
            return {"success": False, "error": _authority_error_message(inner["Err"])}
    elif hasattr(inner, "Ok"):
        return {"success": True, "tx_id": str(inner.Ok)}
    elif hasattr(inner, "Err"):
        return {"success": False, "error": _authority_error_message(inner.Err)}

    logger.error(f"Unexpected inner authority result: {inner}")
    return {"success": False, "error": f"Unexpected result from NFT canister: {inner}"}


def force_transfer_nft(
    nft_canister_id: str,
    token_id: int,
    new_owner_principal: str,
    memo: str = "",
):
    """Force-transfer an NFT to a new owner via the realm's authority.

    The realm backend must be the token's registry authority on the NFT
    canister (i.e. it minted the token and is still an authorized minter).
    """
    logger.info(
        f"Force transfer NFT: token_id={token_id}, new_owner={new_owner_principal}, memo={memo!r}"
    )
    try:
        try:
            _validate_principal_text(new_owner_principal)
        except Exception as e:
            return {"success": False, "error": f"Invalid new owner principal: {e}"}

        arg = {
            "token_id": token_id,
            "to": {
                "owner": Principal.from_str(new_owner_principal),
                "subaccount": None,
            },
            "memo": memo or None,
        }
        nft_service = NFTService(Principal.from_str(nft_canister_id))
        result: CallResult[AuthorityResult] = yield nft_service.force_transfer(arg)
        logger.info(f"NFT force_transfer result: {result}")
        return _unwrap_authority_result(result)
    except Exception as e:
        logger.error(f"Error force-transferring NFT: {e}")
        return {"success": False, "error": f"Failed to force-transfer: {e}"}


def freeze_nft(nft_canister_id: str, token_id: int, reason: str = ""):
    """Freeze an NFT (block holder transfers) via the realm's authority."""
    logger.info(f"Freeze NFT: token_id={token_id}, reason={reason!r}")
    try:
        arg = {"token_id": token_id, "reason": reason or None}
        nft_service = NFTService(Principal.from_str(nft_canister_id))
        result: CallResult[AuthorityResult] = yield nft_service.freeze_token(arg)
        logger.info(f"NFT freeze result: {result}")
        return _unwrap_authority_result(result)
    except Exception as e:
        logger.error(f"Error freezing NFT: {e}")
        return {"success": False, "error": f"Failed to freeze: {e}"}


def unfreeze_nft(nft_canister_id: str, token_id: int):
    """Unfreeze an NFT via the realm's authority."""
    logger.info(f"Unfreeze NFT: token_id={token_id}")
    try:
        nft_service = NFTService(Principal.from_str(nft_canister_id))
        result: CallResult[AuthorityResult] = yield nft_service.unfreeze_token(token_id)
        logger.info(f"NFT unfreeze result: {result}")
        return _unwrap_authority_result(result)
    except Exception as e:
        logger.error(f"Error unfreezing NFT: {e}")
        return {"success": False, "error": f"Failed to unfreeze: {e}"}


def mint_land_nft(
    nft_canister_id: str,
    owner_principal: str,
    land_id: str,
    x_coordinate: int,
    y_coordinate: int,
    land_type: str = "",
    token_id: Optional[int] = None,
    h3_index: Optional[str] = None,
    h3_indexes: Optional[list] = None,
):
    """
    Mint a LAND NFT for a registered land parcel.

    Makes an inter-canister call to the realm's NFT canister to mint
    an NFT representing ownership of a land parcel. If token_id is not
    provided, the NFT canister auto-assigns the next sequential ID.

    Args:
        nft_canister_id: Canister ID of the realm's NFT backend
        owner_principal: Principal ID of the land owner
        land_id: ID of the land parcel in the realm
        x_coordinate: X coordinate of the land
        y_coordinate: Y coordinate of the land
        land_type: Type of land (residential, agricultural, etc.)
        token_id: Optional explicit token ID (omit for auto-assignment)

    Returns:
        Dictionary with success status and token_id or error
    """
    logger.info(
        f"Minting LAND NFT: owner={owner_principal}, land_id={land_id}, "
        f"coords=({x_coordinate},{y_coordinate}), explicit_token_id={token_id}"
    )

    try:
        # Validate principal early so the user gets a clear error.
        try:
            _validate_principal_text(owner_principal)
        except Exception as e:
            return {"success": False, "error": f"Invalid owner principal: {e}"}

        metadata = [
            ("land_id", {"Text": land_id}),
            ("x_coordinate", {"Nat": x_coordinate}),
            ("y_coordinate", {"Nat": y_coordinate}),
            ("realm_canister", {"Text": str(ic.id())}),
        ]
        if land_type:
            metadata.append(("land_type", {"Text": land_type}))
        if h3_index:
            metadata.append(("h3_index", {"Text": str(h3_index)}))
        if h3_indexes:
            metadata.append(("h3_indexes", {"Text": json.dumps([str(i) for i in h3_indexes if i])}))

        mint_arg = {
            "token_id": None if token_id is None else token_id,
            "owner": {
                "owner": Principal.from_str(owner_principal),
                "subaccount": None,
            },
            "metadata": metadata,
        }

        logger.info(f"Calling NFT canister {nft_canister_id} with typed mint arg")

        nft_service = NFTService(Principal.from_str(nft_canister_id))
        result: CallResult[MintResult] = yield nft_service.mint(mint_arg)

        logger.info(f"NFT mint result: {result}")

        parsed = _unwrap_mint_result(result)
        if parsed.get("success"):
            parsed["nft_canister_id"] = nft_canister_id
            parsed["land_id"] = land_id
        return parsed

    except Exception as e:
        logger.error(f"Error minting LAND NFT: {str(e)}")
        return {"success": False, "error": f"Failed to mint: {str(e)}"}


def get_nft_canister_id() -> Optional[str]:
    """Get the NFT canister ID for this realm (Realm entity, then static config)."""
    from api.tokens import get_nft_canister_id as _get_nft_canister_id

    return _get_nft_canister_id()
