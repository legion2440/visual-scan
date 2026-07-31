import assert from 'node:assert/strict';
import test from 'node:test';

import {
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
    revision: 5,
  });
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
