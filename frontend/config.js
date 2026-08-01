/**
 * Frontend runtime configuration.
 *
 * This composes environment-neutral intake/OCR contracts with browser runtime
 * URLs and deadlines. Backend HTTP calls themselves belong in utils/api.js.
 */
import { DEFAULT_OCR_PROFILE } from './ocrProfiles.js';
import {
  INTAKE_LIMITS,
  SUPPORTED_IMAGE_TYPES,
  SUPPORTED_PDF_TYPE,
} from './intakeContract.js';

const ocrDataRootUrl = new URL('./assets/tessdata', import.meta.url).href.replace(/\/$/, '');
const sampleManifestUrl = new URL('../public/sample-docs/manifest.json', import.meta.url).href;

export const CONFIG = Object.freeze({
  backendUrl: 'http://localhost:8000',
  healthTimeoutMs: 4_000,
  archiveTimeoutMs: 15_000,
  imageOcrTimeoutMs: 60_000,
  aiTimeoutMs: 100_000,
  pdfOcrTimeoutMs: 210_000,
  maxImageBytes: INTAKE_LIMITS.maxImageBytes,
  maxPdfBytes: INTAKE_LIMITS.maxPdfBytes,
  maxImagePixels: INTAKE_LIMITS.maxImagePixels,
  supportedImageTypes: SUPPORTED_IMAGE_TYPES,
  supportedPdfType: SUPPORTED_PDF_TYPE,
  sampleManifestUrl,
  ocr: Object.freeze({
    defaultProfile: DEFAULT_OCR_PROFILE,
    dataRootUrl: ocrDataRootUrl,
    manifestUrl: `${ocrDataRootUrl}/manifest.json`,
    cachePrefix: 'visual-scan-v5',
    workerPath: 'https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/worker.min.js',
    corePath: 'https://cdn.jsdelivr.net/npm/tesseract.js-core@5.1.1',
  }),
});
