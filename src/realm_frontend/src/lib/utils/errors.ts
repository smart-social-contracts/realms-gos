export const ERROR_CODE_PERMISSION_DENIED = 'permission_denied';
export const ERROR_CODE_UNAUTHENTICATED = 'unauthenticated';
export const DEFAULT_PERMISSION_MESSAGE = "You don't have permission to view this.";

export interface AccessError {
  isAccessDenied: boolean;
  operation: string;
  message: string;
}

export class AccessDeniedError extends Error {
  operation: string;
  constructor(info: AccessError) {
    super(info.message);
    this.name = 'AccessDeniedError';
    this.operation = info.operation;
  }
}

/**
 * Detects whether a realm reply, an extension result (or a caught error)
 * represents a permission-denied response and extracts structured info.
 *
 * Realm refusals:
 *   {"ok":false,"error":"<operation> needs <permission>"}   {"ok":false,"error":"sign in first"}
 * Extension results:
 *   {"success":false,"error_code":"permission_denied","error":"...","denied_operation":"..."}
 *
 * Returns null when the result is not a permission error.
 */
export function parseAccessError(
  resultOrError: unknown
): AccessError | null {
  if (!resultOrError) return null;

  if (typeof resultOrError === 'object' && resultOrError !== null) {
    const obj = resultOrError as Record<string, unknown>;
    const direct = accessErrorFromObject(obj);
    if (direct) return direct;

    if (typeof obj.response === 'string') {
      return parseAccessError(obj.response);
    }
  }

  if (resultOrError instanceof Error) {
    return (
      accessErrorFromObject({ ok: false, error: resultOrError.message }) ??
      parseAccessError({ response: resultOrError.message })
    );
  }

  if (typeof resultOrError === 'string') {
    try {
      return parseAccessError(JSON.parse(resultOrError));
    } catch {
      return null;
    }
  }

  return null;
}

const REALM_REFUSAL = /^\S+ needs (\S+)$/;

function accessErrorFromObject(obj: Record<string, unknown>): AccessError | null {
  if (obj.ok === false && typeof obj.error === 'string') {
    const needs = obj.error.match(REALM_REFUSAL);
    if (!needs && obj.error !== 'sign in first') return null;
    return { isAccessDenied: true, operation: needs?.[1] ?? '', message: DEFAULT_PERMISSION_MESSAGE };
  }
  const code = obj.error_code;
  if (code !== ERROR_CODE_PERMISSION_DENIED && code !== ERROR_CODE_UNAUTHENTICATED) {
    return null;
  }
  const deniedOp = obj.denied_operation;
  return {
    isAccessDenied: true,
    operation: deniedOp ? String(deniedOp) : '',
    message: DEFAULT_PERMISSION_MESSAGE,
  };
}

/**
 * Detects whether an error is an expired IC delegation (HTTP 400 from replica).
 * Returns a user-friendly message if so, null otherwise.
 */
export function formatDelegationExpiredError(error: unknown): string | null {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  if (
    msg.includes('Invalid delegation expiry') ||
    msg.includes('delegation has expired') ||
    msg.includes('Specified sender delegation has expired')
  ) {
    return 'Your session expired. Please refresh the page to continue.';
  }
  return null;
}
