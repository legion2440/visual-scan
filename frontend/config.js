/**
 * Frontend runtime configuration.
 *
 * This is the single source of truth for the backend address and client-side
 * safety limits. Keep environment-specific values here; HTTP calls themselves
 * belong in utils/api.js.
 */
export const CONFIG = Object.freeze({
  backendUrl: 'http://localhost:8000',
  requestTimeoutMs: 45_000,
  healthTimeoutMs: 4_000,
  maxImageBytes: 20 * 1024 * 1024,
  maxImagePixels: 25_000_000,
  supportedImageTypes: Object.freeze([
    'image/jpeg',
    'image/png',
    'image/webp',
  ]),
});
