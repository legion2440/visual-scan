/**
 * Client-side OCR through the pinned Tesseract.js build loaded by index.html.
 *
 * Trained data is always loaded from frontend/assets/tessdata. There is no
 * traineddata CDN fallback. A single active worker avoids keeping multiple
 * large language/model combinations in memory.
 */

import { CONFIG } from '../config.js';
import {
  DEFAULT_OCR_PROFILE,
  OCR_LANGUAGE_ORDER,
  OCR_LANGUAGES,
  OCR_PROFILES,
  resolveOcrLanguageCodes,
} from '../ocrProfiles.js';

export const OCR_MODEL_NOT_INSTALLED_MESSAGE = [
  'The selected OCR model is not installed.',
  'Run the model download script or choose an available profile.',
].join('\n');

export class OcrModelError extends Error {
  constructor(message = OCR_MODEL_NOT_INSTALLED_MESSAGE, options = {}) {
    super(message, options);
    this.name = 'OcrModelError';
    this.code = 'OCR_MODEL_NOT_INSTALLED';
  }
}

export const LANGUAGES = Object.freeze(
  OCR_LANGUAGE_ORDER.map((code) => Object.freeze({ code, label: OCR_LANGUAGES[code] })),
);

export const PROFILES = Object.freeze(
  Object.values(OCR_PROFILES).map((profile) => Object.freeze({
    id: profile.id,
    label: profile.label,
  })),
);

let activeWorker = null;
let activeWorkerKey = null;
let currentProgress = null;
let operationQueue = Promise.resolve();
let availabilityPromise = null;

function emptyManifest() {
  return Object.fromEntries(Object.keys(OCR_PROFILES).map((profile) => [profile, []]));
}

function normalizeManifest(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('OCR manifest must be a JSON object.');
  }

  const manifest = emptyManifest();
  for (const [profileId, profile] of Object.entries(OCR_PROFILES)) {
    const installed = value[profileId];
    if (!Array.isArray(installed)) {
      throw new TypeError(`OCR manifest entry "${profileId}" must be an array.`);
    }
    if (installed.some((language) => (
      typeof language !== 'string'
      || language.includes('+')
      || !profile.supportedLanguages.includes(language)
    ))) {
      throw new TypeError(`OCR manifest entry "${profileId}" contains an unsupported language.`);
    }
    manifest[profileId] = [...new Set(installed)];
  }
  return manifest;
}

/**
 * Load locally generated model availability. Missing or invalid manifests are
 * represented by an empty manifest so image tools and the rest of the app keep
 * working.
 */
export async function loadOcrAvailability({ force = false } = {}) {
  if (force) availabilityPromise = null;
  if (availabilityPromise) return availabilityPromise;

  availabilityPromise = (async () => {
    try {
      const response = await fetch(CONFIG.ocr.manifestUrl, { cache: 'no-store' });
      if (!response.ok) {
        throw new Error(`OCR manifest request failed with HTTP ${response.status}.`);
      }
      return {
        manifest: normalizeManifest(await response.json()),
        loaded: true,
        error: null,
      };
    } catch (error) {
      return {
        manifest: emptyManifest(),
        loaded: false,
        error,
      };
    }
  })();

  return availabilityPromise;
}

export function resolveWorkerLanguages(language) {
  const languages = resolveOcrLanguageCodes(language);
  return languages.length > 1 ? languages : languages[0];
}

export function isOcrCombinationAvailable(manifest, profileId, language) {
  const profile = OCR_PROFILES[profileId];
  const installed = manifest && manifest[profileId];
  if (!profile || !Array.isArray(installed)) return false;

  const requiredLanguages = resolveOcrLanguageCodes(language);
  return requiredLanguages.length > 0
    && requiredLanguages.every((item) => (
      profile.supportedLanguages.includes(item) && installed.includes(item)
    ));
}

export function availableLanguagesForProfile(manifest, profileId) {
  return OCR_LANGUAGE_ORDER.filter(
    (language) => isOcrCombinationAvailable(manifest, profileId, language),
  );
}

function ensureTesseract() {
  if (typeof window === 'undefined' || typeof window.Tesseract === 'undefined') {
    throw new Error(
      'Tesseract.js did not load. Check the network connection and reload the page.',
    );
  }
  return window.Tesseract;
}

function enqueue(operation) {
  const result = operationQueue.then(operation, operation);
  operationQueue = result.catch(() => {});
  return result;
}

async function terminateActiveWorker() {
  const worker = activeWorker;
  activeWorker = null;
  activeWorkerKey = null;
  if (!worker) return;
  try {
    await worker.terminate();
  } catch {
    // The worker may already have stopped after an initialization/runtime error.
  }
}

function workerKey(profileId, language) {
  return `${profileId}:${language}`;
}

async function getWorker(profileId, language) {
  const key = workerKey(profileId, language);
  if (activeWorker && activeWorkerKey === key) return activeWorker;

  await terminateActiveWorker();
  const profile = OCR_PROFILES[profileId];
  const Tesseract = ensureTesseract();
  const langPath = `${CONFIG.ocr.dataRootUrl}/${profile.directory}`;

  try {
    const worker = await Tesseract.createWorker(
      resolveWorkerLanguages(language),
      1,
      {
        langPath,
        gzip: false,
        cachePath: `${CONFIG.ocr.cachePrefix}-${profileId}`,
        workerPath: CONFIG.ocr.workerPath,
        corePath: CONFIG.ocr.corePath,
        logger: (message) => {
          if (!currentProgress) return;
          currentProgress({
            status: message.status,
            progress: typeof message.progress === 'number' ? message.progress : 0,
          });
        },
      },
    );
    activeWorker = worker;
    activeWorkerKey = key;
    return worker;
  } catch (error) {
    await terminateActiveWorker();
    throw new Error(
      `Could not initialise the ${profile.label} OCR model: ${error.message || error}`,
      { cause: error },
    );
  }
}

async function recognizeInternal(source, {
  lang = 'eng',
  profile = DEFAULT_OCR_PROFILE,
  onProgress,
} = {}) {
  const availability = await loadOcrAvailability();
  if (!isOcrCombinationAvailable(availability.manifest, profile, lang)) {
    throw new OcrModelError();
  }

  currentProgress = onProgress || null;
  try {
    const worker = await getWorker(profile, lang);
    const { data } = await worker.recognize(source);
    return {
      text: (data.text || '').replace(/\n{3,}/g, '\n\n').trim(),
      confidence: Math.round(data.confidence || 0),
      words: data.words
        ? data.words.length
        : (data.text || '').split(/\s+/).filter(Boolean).length,
      lang,
      languageLabel: OCR_LANGUAGES[lang],
      profile,
      profileLabel: OCR_PROFILES[profile].label,
      engine: 'Tesseract.js 5.1.1 (browser)',
    };
  } finally {
    currentProgress = null;
  }
}

/**
 * Recognise text using an explicitly selected local profile and language.
 * Operations are serialized so a profile switch cannot terminate a worker
 * while it is still processing a page.
 */
export function recognize(source, options = {}) {
  return enqueue(() => recognizeInternal(source, options));
}

/**
 * Release an active worker when the user changes profile or language. No new
 * worker is created until the next OCR run.
 */
export function releaseWorkerForSelection(
  profile = DEFAULT_OCR_PROFILE,
  language = 'eng',
) {
  return enqueue(async () => {
    if (activeWorkerKey && activeWorkerKey !== workerKey(profile, language)) {
      await terminateActiveWorker();
    }
  });
}

/** Free the active worker when the page is closed. */
export function shutdown() {
  return enqueue(terminateActiveWorker);
}
