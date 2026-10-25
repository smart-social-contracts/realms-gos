"""Structured error envelopes for extension call results.

Handlers should set ``error_code`` at the source. ``error`` is display-only
and is never parsed to choose a code.

Known codes:
  permission_denied  — caller lacks an operation (see denied_operation)
  unauthenticated    — caller is not a registered realm member
  not_found          — a referenced entity is missing
  validation_error   — bad or incomplete arguments

Do not use the ``re`` module here: Basilisk ships only a stub without
``re.compile``.
"""

import json

ERROR_CODE_PERMISSION_DENIED = "permission_denied"
ERROR_CODE_UNAUTHENTICATED = "unauthenticated"
ERROR_CODE_NOT_FOUND = "not_found"
ERROR_CODE_VALIDATION = "validation_error"


class Unauthenticated(PermissionError):
    """Caller is not a registered realm member."""

    def __init__(self, message: str = "Not authenticated"):
        super().__init__(message)


class PermissionDenied(PermissionError):
    """Caller lacks a named operation."""

    def __init__(self, message: str, operation: str = ""):
        super().__init__(message)
        self.operation = operation


class CodedError(ValueError):
    """Validation/user error with a stable ``error_code`` for the UI."""

    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


def error_payload(error_code: str, message: str, **extra) -> dict:
    payload = {
        "success": False,
        "error_code": error_code,
        "error": message,
    }
    payload.update({k: v for k, v in extra.items() if v is not None and v != ""})
    return payload


def permission_denied_payload(message: str, operation: str = "") -> dict:
    return error_payload(
        ERROR_CODE_PERMISSION_DENIED,
        message or "Access denied",
        denied_operation=operation or None,
    )


def not_found_payload(message: str, entity: str = "") -> dict:
    return error_payload(ERROR_CODE_NOT_FOUND, message, entity=entity or None)


def validation_payload(message: str) -> dict:
    return error_payload(ERROR_CODE_VALIDATION, message)


def payload_from_permission_error(exc: BaseException) -> dict:
    if isinstance(exc, Unauthenticated):
        return error_payload(ERROR_CODE_UNAUTHENTICATED, str(exc) or "Not authenticated")
    operation = getattr(exc, "operation", "") or ""
    return permission_denied_payload(str(exc), operation)


def normalize_extension_result_json(result) -> str:
    """Serialize a handler result. Does not infer error_code from English text."""
    if result is None:
        return "null"
    if isinstance(result, dict):
        return json.dumps(result)
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result)
    except TypeError:
        return json.dumps({"success": False, "error": str(result)})
