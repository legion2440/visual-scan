import assert from 'node:assert/strict';
import test from 'node:test';

import {
  AUTH_REVALIDATION_MODE,
  AuthContractError,
  EDITOR_PROVENANCE,
  anonymousAfterUnauthorized,
  authRequestSnapshot,
  codePointLength,
  identityChanged,
  isAuthRequestCurrent,
  isAuthRevisionCurrent,
  isServerDerivedEditor,
  normalizeAuthSession,
  normalizeUsername,
  planAuthRevalidation,
  planAuthVerificationFailure,
  planProtectedUnauthorized,
  provenanceForOcrSource,
  serverFeaturesAvailable,
  validatePassword,
} from '../frontend/utils/auth.js';

test('session normalization keeps only safe public identity and CSRF transport value', () => {
  const session = normalizeAuthSession({
    authenticated: true,
    user: {
      id: 'one',
      username: 'nazar',
      created_at: '2026-07-31T10:30:00Z',
      is_initial_user: true,
      password_hash: 'must-not-survive',
    },
    csrf_token: 'csrf',
  });
  assert.deepEqual(session, {
    status: 'authenticated',
    user: {
      id: 'one',
      username: 'nazar',
      createdAt: '2026-07-31T10:30:00Z',
      isInitialUser: true,
    },
    csrfToken: 'csrf',
  });
  assert.equal(serverFeaturesAvailable(session), true);
  assert.deepEqual(normalizeAuthSession({
    authenticated: false,
    user: null,
    csrf_token: null,
  }), { status: 'anonymous', user: null, csrfToken: null });
});

test('inconsistent session responses are rejected', () => {
  assert.throws(() => normalizeAuthSession({
    authenticated: false,
    user: { id: 'leak' },
    csrf_token: null,
  }), AuthContractError);
  assert.throws(() => normalizeAuthSession({
    authenticated: true,
    user: null,
    csrf_token: null,
  }), AuthContractError);
});

test('username normalization is ASCII-only and case-insensitive', () => {
  assert.equal(normalizeUsername('Nazar.Test-1'), 'nazar.test-1');
  for (const invalid of ['ab', 'unsafe user', 'назap', 'x'.repeat(33)]) {
    assert.throws(() => normalizeUsername(invalid), AuthContractError);
  }
});

test('password length counts Unicode code points and preserves exact input', () => {
  const password = `  ${'😀'.repeat(10)}`;
  assert.equal(codePointLength(password), 12);
  assert.equal(validatePassword(password), password);
  assert.throws(() => validatePassword('😀'.repeat(11)), AuthContractError);
  assert.throws(() => validatePassword(`valid password\0`), AuthContractError);
  assert.throws(() => validatePassword(`valid password\ud800`), AuthContractError);
});

test('revision, identity, and 401 transitions are deterministic', () => {
  const auth = {
    status: 'authenticated',
    user: { id: 'one' },
    csrfToken: 'secret',
    busy: true,
    revision: 4,
  };
  assert.equal(isAuthRevisionCurrent(auth, 4), true);
  assert.equal(identityChanged(auth.user, { id: 'one' }), false);
  assert.equal(identityChanged(auth.user, { id: 'two' }), true);
  const snapshot = authRequestSnapshot(auth);
  assert.equal(isAuthRequestCurrent(auth, snapshot), true);
  assert.equal(isAuthRequestCurrent({ ...auth, revision: 5 }, snapshot), false);
  assert.equal(isAuthRequestCurrent({ ...auth, user: { id: 'two' } }, snapshot), false);
  const anonymous = anonymousAfterUnauthorized(auth);
  assert.deepEqual(anonymous, {
    status: 'anonymous',
    user: null,
    csrfToken: null,
    busy: false,
    verificationUnavailable: false,
    revision: 5,
  });
});

test('ordinary focus revalidation preserves in-flight save and list generations', () => {
  const auth = {
    status: 'authenticated',
    user: { id: 'one' },
    csrfToken: 'csrf-one',
    revision: 7,
  };
  const saveSnapshot = authRequestSnapshot(auth);
  const focusPlan = planAuthRevalidation(auth);
  const sameUserHintPlan = planAuthRevalidation(auth, {
    hasIdentityHint: true,
    identityHint: 'one',
  });

  assert.deepEqual(focusPlan, {
    mode: AUTH_REVALIDATION_MODE.SOFT,
    cancelProtected: false,
    clearServerDerived: false,
  });
  assert.deepEqual(sameUserHintPlan, focusPlan);
  assert.equal(isAuthRequestCurrent(auth, saveSnapshot), true);
});

test('identity mismatch is the only hint that immediately invalidates protected state', () => {
  const auth = {
    status: 'authenticated',
    user: { id: 'one' },
    csrfToken: 'csrf-one',
    revision: 7,
  };

  for (const identityHint of ['two', null]) {
    assert.deepEqual(planAuthRevalidation(auth, {
      hasIdentityHint: true,
      identityHint,
    }), {
      mode: AUTH_REVALIDATION_MODE.BOUNDARY,
      cancelProtected: true,
      clearServerDerived: true,
    });
  }
});

test('authenticated verification failure preserves identity, CSRF, and derived state', () => {
  const auth = {
    status: 'authenticated',
    user: { id: 'one' },
    csrfToken: 'csrf-one',
    revision: 7,
  };

  assert.deepEqual(planAuthVerificationFailure(auth), {
    preserveIdentity: true,
    clearServerDerived: false,
  });
  assert.deepEqual(planAuthVerificationFailure({
    status: 'checking', user: null, csrfToken: null,
  }, { initial: true }), {
    preserveIdentity: false,
    clearServerDerived: true,
  });
});

test('same-user rotation confirms an early old 401 before clearing derived state', () => {
  const auth = {
    status: 'authenticated',
    user: { id: 'one' },
    csrfToken: 'csrf-one',
    revision: 7,
  };
  const request = authRequestSnapshot(auth);
  const editor = { provenance: EDITOR_PROVENANCE.SERVER_OCR, text: 'server OCR' };
  const archive = [{ id: 'scan-one' }];
  const sameUserHint = planAuthRevalidation(auth, {
    hasIdentityHint: true,
    identityHint: 'one',
  });
  const unauthorized = planProtectedUnauthorized(auth, request);
  const verified = normalizeAuthSession({
    authenticated: true,
    user: {
      id: 'one',
      username: 'user-one',
      created_at: '2026-01-01T00:00:00Z',
      is_initial_user: false,
    },
    csrf_token: 'csrf-two',
  });

  assert.equal(sameUserHint.mode, AUTH_REVALIDATION_MODE.SOFT);
  assert.deepEqual(unauthorized, {
    revalidate: true,
    clearIdentity: false,
    clearServerDerived: false,
  });
  assert.equal(identityChanged(auth.user, verified.user), false);
  assert.equal(verified.csrfToken, 'csrf-two');
  assert.deepEqual(editor, {
    provenance: EDITOR_PROVENANCE.SERVER_OCR,
    text: 'server OCR',
  });
  assert.deepEqual(archive, [{ id: 'scan-one' }]);
});

test('editor provenance survives OCR metadata invalidation decisions', () => {
  const server = provenanceForOcrSource('server');
  const browser = provenanceForOcrSource('browser');

  assert.equal(server, EDITOR_PROVENANCE.SERVER_OCR);
  assert.equal(browser, EDITOR_PROVENANCE.BROWSER_OCR);
  assert.equal(isServerDerivedEditor(server), true);
  assert.equal(isServerDerivedEditor(browser), false);
  assert.equal(isServerDerivedEditor(EDITOR_PROVENANCE.MANUAL), false);
});
