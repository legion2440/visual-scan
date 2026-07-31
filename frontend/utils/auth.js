/** Pure authentication state and validation helpers. */

export class AuthContractError extends Error {
  constructor(message) {
    super(message);
    this.name = 'AuthContractError';
  }
}

export function codePointLength(value) {
  return Array.from(String(value)).length;
}

export function normalizeUsername(value) {
  if (typeof value !== 'string') throw new AuthContractError('Enter a username.');
  let ascii = true;
  for (const character of value) {
    if (character.codePointAt(0) > 0x7f) ascii = false;
  }
  const normalized = value.toLowerCase();
  if (!ascii || !/^[a-z0-9._-]{3,32}$/.test(normalized)) {
    throw new AuthContractError(
      'Username must be 3–32 ASCII characters: letters, numbers, dot, underscore, or hyphen.',
    );
  }
  return normalized;
}

export function validatePassword(value) {
  if (typeof value !== 'string') throw new AuthContractError('Enter a password.');
  const length = codePointLength(value);
  if (length < 12 || length > 256) {
    throw new AuthContractError('Password must contain 12–256 characters.');
  }
  if (value.includes('\0') || /[\uD800-\uDFFF]/u.test(value)) {
    // Valid non-BMP characters are represented by paired surrogates in JS.
    for (let index = 0; index < value.length; index += 1) {
      const unit = value.charCodeAt(index);
      if (unit >= 0xd800 && unit <= 0xdbff) {
        const next = value.charCodeAt(index + 1);
        if (next >= 0xdc00 && next <= 0xdfff) {
          index += 1;
          continue;
        }
        throw new AuthContractError('Password contains an invalid Unicode character.');
      }
      if (unit >= 0xdc00 && unit <= 0xdfff) {
        throw new AuthContractError('Password contains an invalid Unicode character.');
      }
    }
    if (value.includes('\0')) throw new AuthContractError('Password must not contain null characters.');
  }
  return value;
}

function normalizeUser(value) {
  if (
    !value
    || typeof value !== 'object'
    || typeof value.id !== 'string'
    || typeof value.username !== 'string'
    || typeof value.created_at !== 'string'
    || typeof value.is_initial_user !== 'boolean'
  ) {
    throw new AuthContractError('The backend returned an invalid user session.');
  }
  return Object.freeze({
    id: value.id,
    username: value.username,
    createdAt: value.created_at,
    isInitialUser: value.is_initial_user,
  });
}

export function normalizeAuthSession(value) {
  if (!value || typeof value !== 'object' || typeof value.authenticated !== 'boolean') {
    throw new AuthContractError('The backend returned an invalid session response.');
  }
  if (!value.authenticated) {
    if (value.user !== null || value.csrf_token !== null) {
      throw new AuthContractError('The anonymous session response is inconsistent.');
    }
    return Object.freeze({ status: 'anonymous', user: null, csrfToken: null });
  }
  if (typeof value.csrf_token !== 'string' || !value.csrf_token) {
    throw new AuthContractError('The authenticated session is missing its security token.');
  }
  return Object.freeze({
    status: 'authenticated',
    user: normalizeUser(value.user),
    csrfToken: value.csrf_token,
  });
}

export function isAuthRevisionCurrent(authState, revision) {
  return authState.revision === revision;
}

export function serverFeaturesAvailable(authState) {
  return authState.status === 'authenticated' && Boolean(authState.user);
}

export function identityChanged(previousUser, nextUser) {
  return (previousUser?.id || null) !== (nextUser?.id || null);
}

export function anonymousAfterUnauthorized(authState) {
  return {
    ...authState,
    status: 'anonymous',
    user: null,
    csrfToken: null,
    busy: false,
    revision: authState.revision + 1,
  };
}
