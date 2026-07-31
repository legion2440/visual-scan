/**
 * Read-only compatibility access to the pre-Step-7 local archive.
 *
 * New scans are stored by the backend. Existing localStorage records are
 * exposed only for explicit export or deletion and are never migrated or
 * removed silently.
 */

export const LEGACY_SCAN_KEY = 'visual-scan.scans';

export class StorageError extends Error {
  constructor(message, { cause } = {}) {
    super(message);
    this.name = 'StorageError';
    this.cause = cause;
  }
}

function readLegacy() {
  try {
    const value = JSON.parse(localStorage.getItem(LEGACY_SCAN_KEY) || '[]');
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

export const legacyStore = Object.freeze({
  all: readLegacy,
  count: () => readLegacy().length,
  clear() {
    try {
      localStorage.removeItem(LEGACY_SCAN_KEY);
    } catch (error) {
      throw new StorageError('Browser storage is unavailable.', { cause: error });
    }
  },
});
