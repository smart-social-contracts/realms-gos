import { describe, expect, it } from 'vitest';
import {
  DEFAULT_PERMISSION_MESSAGE,
  parseAccessError,
} from './errors';

describe('parseAccessError', () => {
  it('recognizes error_code permission_denied without denied_operation', () => {
    const denied = parseAccessError({
      success: false,
      error_code: 'permission_denied',
      error: 'Access denied',
    });
    expect(denied).toEqual({
      isAccessDenied: true,
      operation: '',
      message: DEFAULT_PERMISSION_MESSAGE,
    });
  });

  it('recognizes error_code plus denied_operation on inner JSON', () => {
    const denied = parseAccessError({
      success: false,
      error_code: 'permission_denied',
      error: "Access denied: user abc lacks permission 'permission.view'",
      denied_operation: 'permission.view',
    });
    expect(denied?.operation).toBe('permission.view');
    expect(denied?.message).toBe(DEFAULT_PERMISSION_MESSAGE);
  });

  it('recognizes unauthenticated as an access error', () => {
    const denied = parseAccessError({
      success: false,
      error_code: 'unauthenticated',
      error: 'Not authenticated',
    });
    expect(denied?.isAccessDenied).toBe(true);
    expect(denied?.operation).toBe('');
  });

  it('unwraps outer envelope response JSON', () => {
    const denied = parseAccessError({
      success: true,
      response: JSON.stringify({
        success: false,
        error_code: 'permission_denied',
        error: "Access denied: user abc lacks permission 'permission.view'",
        denied_operation: 'permission.view',
      }),
    });
    expect(denied?.operation).toBe('permission.view');
  });

  it('ignores not_found even when the message mentions a user', () => {
    expect(
      parseAccessError({
        success: false,
        error_code: 'not_found',
        error: "No realm member with principal 'xyz'. They must join the realm first.",
        entity: 'user',
      }),
    ).toBeNull();
  });

  it('ignores ordinary validation failures', () => {
    expect(
      parseAccessError({ success: false, error: 'Department name is required' }),
    ).toBeNull();
  });

  it('ignores English-only access-denied text without error_code', () => {
    expect(
      parseAccessError({
        success: false,
        error: "Access denied: user abc lacks permission 'permission.view'",
      }),
    ).toBeNull();
  });

  it('recognizes a realm refusal and names the missing permission', () => {
    const denied = parseAccessError({ ok: false, error: 'user_list needs user.view' });
    expect(denied).toEqual({
      isAccessDenied: true,
      operation: 'user.view',
      message: DEFAULT_PERMISSION_MESSAGE,
    });
    expect(parseAccessError(new Error('user_list needs user.view'))?.operation).toBe('user.view');
  });

  it('recognizes an anonymous realm refusal', () => {
    expect(parseAccessError({ ok: false, error: 'sign in first' })?.operation).toBe('');
  });

  it('ignores other realm failures', () => {
    expect(parseAccessError({ ok: false, error: 'unknown operation x' })).toBeNull();
  });

  it('ignores denied_operation without error_code', () => {
    expect(
      parseAccessError({
        error: 'Access denied',
        denied_operation: 'extension.sync_call',
      }),
    ).toBeNull();
  });
});
