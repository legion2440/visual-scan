import assert from 'node:assert/strict';
import test from 'node:test';

import {
  LEGACY_SCAN_KEY,
  legacyStore,
  StorageError,
} from '../frontend/utils/store.js';

function installStorage(t, initial = null) {
  const original = globalThis.localStorage;
  let value = initial;
  globalThis.localStorage = {
    getItem(key) {
      assert.equal(key, LEGACY_SCAN_KEY);
      return value;
    },
    removeItem(key) {
      assert.equal(key, LEGACY_SCAN_KEY);
      value = null;
    },
  };
  t.after(() => {
    if (original === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = original;
  });
  return () => value;
}

test('legacy archive is readable but exposes no write or migration operation', (t) => {
  installStorage(t, JSON.stringify([{ id: 'old' }]));
  assert.deepEqual(legacyStore.all(), [{ id: 'old' }]);
  assert.equal(legacyStore.count(), 1);
  assert.equal('add' in legacyStore, false);
  assert.equal('replaceAll' in legacyStore, false);
});

test('malformed and non-array legacy values are treated as empty', (t) => {
  const value = installStorage(t, '{');
  assert.deepEqual(legacyStore.all(), []);
  globalThis.localStorage.getItem = () => JSON.stringify({ id: 'not-an-array' });
  assert.deepEqual(legacyStore.all(), []);
  assert.equal(typeof value(), 'string');
});

test('legacy clear is explicit and normalizes storage failures', (t) => {
  const value = installStorage(t, JSON.stringify([{ id: 'old' }]));
  legacyStore.clear();
  assert.equal(value(), null);
  globalThis.localStorage.removeItem = () => { throw new Error('blocked'); };
  assert.throws(() => legacyStore.clear(), StorageError);
});
