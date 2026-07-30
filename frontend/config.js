/**
 * Frontend runtime configuration.
 *
 * This is the single source of truth for the backend address and client-side
 * safety limits. Keep environment-specific values here; HTTP calls themselves
 * belong in utils/api.js.
 */
import { DEFAULT_OCR_PROFILE } from './ocrProfiles.js';

const ocrDataRootUrl = new URL('./assets/tessdata', import.meta.url).href.replace(/\/$/, '');

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
  ocr: Object.freeze({
    defaultProfile: DEFAULT_OCR_PROFILE,
    dataRootUrl: ocrDataRootUrl,
    manifestUrl: `${ocrDataRootUrl}/manifest.json`,
    cachePrefix: 'visual-scan-v5',
    workerPath: 'https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/worker.min.js',
    corePath: 'https://cdn.jsdelivr.net/npm/tesseract.js-core@5.1.1',
  }),
});
