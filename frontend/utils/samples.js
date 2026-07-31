/** Pure demo-corpus contracts plus isolated static-file fetch helpers. */

import {
  INTAKE_LIMITS,
  maximumBytesForKind,
} from '../intakeContract.js';
import {
  OCR_LANGUAGES,
  OCR_PROFILES,
} from '../ocrProfiles.js';
import { CLASSIFICATIONS } from './archive.js';

export const SAMPLE_MANIFEST_VERSION = 1;
export const SAMPLE_MANIFEST_MAX_BYTES = 256 * 1024;
export const SAMPLE_CORPUS_MAX_BYTES = 15 * 1024 * 1024;
export const SAMPLE_MAX_PDF_PAGES = 20;

const COMMON_SAMPLE_KEYS = Object.freeze([
  'id',
  'label',
  'description',
  'asset',
  'kind',
  'mime',
  'language',
  'reference_text',
  'suggested_classification',
  'suggested_settings',
  'bytes',
  'sha256',
]);
const IMAGE_SAMPLE_KEYS = new Set([...COMMON_SAMPLE_KEYS, 'width', 'height']);
const PDF_SAMPLE_KEYS = new Set([...COMMON_SAMPLE_KEYS, 'page_count']);
const SETTINGS_KEYS = new Set(['engine', 'profile', 'preprocessing', 'threshold']);
const KINDS = new Set(['image', 'pdf']);
const ENGINES = new Set(['browser', 'server']);
const PREPROCESSING = new Set(['none', 'grayscale', 'threshold']);
const MIME_CONTRACT = Object.freeze({
  'image/png': Object.freeze({ kind: 'image', extension: '.png' }),
  'image/jpeg': Object.freeze({ kind: 'image', extension: '.jpg' }),
  'application/pdf': Object.freeze({ kind: 'pdf', extension: '.pdf' }),
});
const SAFE_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const SAFE_FILENAME = /^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$/;
const SHA256 = /^[a-f0-9]{64}$/;

export class SampleDocsError extends Error {
  constructor(message, { code = 'invalid', cause } = {}) {
    super(message);
    this.name = 'SampleDocsError';
    this.code = code;
    this.cause = cause;
  }
}

function assertObject(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new SampleDocsError(`${label} must be an object.`);
  }
}

function assertExactKeys(value, allowed, label) {
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) {
      throw new SampleDocsError(`${label} contains unsupported field “${key}”.`);
    }
  }
  for (const key of allowed) {
    if (!(key in value)) throw new SampleDocsError(`${label}.${key} is required.`);
  }
}

function boundedText(value, label, maximum) {
  if (typeof value !== 'string') throw new SampleDocsError(`${label} must be a string.`);
  const normalized = value.trim();
  if (!normalized || Array.from(normalized).length > maximum) {
    throw new SampleDocsError(`${label} must contain 1–${maximum} characters.`);
  }
  return normalized;
}

export function normalizeSampleFilename(value, label = 'filename') {
  if (typeof value !== 'string' || !SAFE_FILENAME.test(value)) {
    throw new SampleDocsError(
      `${label} must be one lowercase relative filename without path, query, or fragment.`,
    );
  }
  return value;
}

function positiveInteger(value, label, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value <= 0 || value > maximum) {
    throw new SampleDocsError(`${label} must be a positive integer no greater than ${maximum}.`);
  }
  return value;
}

function normalizeSettings(value, kind, label) {
  assertObject(value, label);
  const keys = Object.keys(value);
  for (const key of keys) {
    if (!SETTINGS_KEYS.has(key)) {
      throw new SampleDocsError(`${label} contains unsupported field “${key}”.`);
    }
  }
  if (!keys.includes('engine')) throw new SampleDocsError(`${label}.engine is required.`);
  if (!ENGINES.has(value.engine)) {
    throw new SampleDocsError(`${label}.engine must be browser or server.`);
  }
  if (kind === 'pdf' && value.engine !== 'server') {
    throw new SampleDocsError(`${label}.engine must be server for PDF samples.`);
  }
  const preprocessing = value.preprocessing ?? 'none';
  if (!PREPROCESSING.has(preprocessing)) {
    throw new SampleDocsError(`${label}.preprocessing is unsupported.`);
  }
  const profile = value.profile ?? null;
  if (value.engine === 'browser') {
    if (typeof profile !== 'string' || !(profile in OCR_PROFILES)) {
      throw new SampleDocsError(`${label}.profile is required for browser OCR.`);
    }
  } else if (profile !== null) {
    throw new SampleDocsError(`${label}.profile is only valid for browser OCR.`);
  }
  const threshold = value.threshold ?? null;
  if (preprocessing === 'threshold') {
    if (!Number.isInteger(threshold) || threshold < 0 || threshold > 255) {
      throw new SampleDocsError(`${label}.threshold must be an integer from 0 to 255.`);
    }
  } else if (threshold !== null) {
    throw new SampleDocsError(`${label}.threshold requires threshold preprocessing.`);
  }
  return Object.freeze({
    engine: value.engine,
    profile,
    preprocessing,
    threshold,
  });
}

function normalizeSample(value, index) {
  const label = `samples[${index}]`;
  assertObject(value, label);
  if (!KINDS.has(value.kind)) throw new SampleDocsError(`${label}.kind must be image or pdf.`);
  const allowed = value.kind === 'image' ? IMAGE_SAMPLE_KEYS : PDF_SAMPLE_KEYS;
  assertExactKeys(value, allowed, label);

  const id = boundedText(value.id, `${label}.id`, 80);
  if (!SAFE_ID.test(id)) throw new SampleDocsError(`${label}.id is not a safe sample ID.`);
  const asset = normalizeSampleFilename(value.asset, `${label}.asset`);
  const referenceText = normalizeSampleFilename(
    value.reference_text,
    `${label}.reference_text`,
  );
  if (!referenceText.endsWith('.txt')) {
    throw new SampleDocsError(`${label}.reference_text must use the .txt extension.`);
  }
  const media = MIME_CONTRACT[value.mime];
  if (!media || media.kind !== value.kind || !asset.endsWith(media.extension)) {
    throw new SampleDocsError(`${label}.kind, MIME, and asset extension do not match.`);
  }
  if (!(value.language in OCR_LANGUAGES)) {
    throw new SampleDocsError(`${label}.language is unsupported.`);
  }
  if (!CLASSIFICATIONS.includes(value.suggested_classification)) {
    throw new SampleDocsError(`${label}.suggested_classification is unsupported.`);
  }
  const maximumBytes = maximumBytesForKind(value.kind);
  const bytes = positiveInteger(value.bytes, `${label}.bytes`, maximumBytes);
  if (typeof value.sha256 !== 'string' || !SHA256.test(value.sha256)) {
    throw new SampleDocsError(`${label}.sha256 must be a lowercase SHA-256 digest.`);
  }

  const normalized = {
    id,
    label: boundedText(value.label, `${label}.label`, 80),
    description: boundedText(value.description, `${label}.description`, 240),
    asset,
    kind: value.kind,
    mime: value.mime,
    language: value.language,
    referenceText,
    suggestedClassification: value.suggested_classification,
    suggestedSettings: normalizeSettings(value.suggested_settings, value.kind, `${label}.suggested_settings`),
    bytes,
    sha256: value.sha256,
  };
  if (value.kind === 'image') {
    normalized.width = positiveInteger(value.width, `${label}.width`);
    normalized.height = positiveInteger(value.height, `${label}.height`);
    if (normalized.width * normalized.height > INTAKE_LIMITS.maxImagePixels) {
      throw new SampleDocsError(`${label} exceeds the frontend image pixel limit.`);
    }
  } else {
    normalized.pageCount = positiveInteger(
      value.page_count,
      `${label}.page_count`,
      SAMPLE_MAX_PDF_PAGES,
    );
  }
  return Object.freeze(normalized);
}

export function normalizeSampleManifest(value) {
  assertObject(value, 'manifest');
  assertExactKeys(value, new Set(['version', 'samples']), 'manifest');
  if (value.version !== SAMPLE_MANIFEST_VERSION) {
    throw new SampleDocsError(`Unsupported sample manifest version: ${value.version}.`);
  }
  if (!Array.isArray(value.samples) || value.samples.length === 0) {
    throw new SampleDocsError('manifest.samples must be a non-empty array.');
  }
  const samples = value.samples.map(normalizeSample);
  const ids = new Set();
  const assets = new Set();
  const references = new Set();
  for (const sample of samples) {
    const id = sample.id.toLowerCase();
    const asset = sample.asset.toLowerCase();
    const reference = sample.referenceText.toLowerCase();
    if (ids.has(id)) throw new SampleDocsError(`Duplicate sample ID: ${sample.id}.`);
    if (assets.has(asset)) throw new SampleDocsError(`Duplicate sample asset: ${sample.asset}.`);
    if (references.has(reference)) {
      throw new SampleDocsError(`Duplicate reference text: ${sample.referenceText}.`);
    }
    ids.add(id);
    assets.add(asset);
    references.add(reference);
  }
  return Object.freeze({
    version: SAMPLE_MANIFEST_VERSION,
    samples: Object.freeze(samples),
  });
}

export function resolveSampleUrl(manifestUrl, filename) {
  const safeFilename = normalizeSampleFilename(filename);
  let manifest;
  try {
    manifest = new URL(manifestUrl);
  } catch (error) {
    throw new SampleDocsError('The sample manifest URL is invalid.', { cause: error });
  }
  const directory = new URL('./', manifest);
  const resolved = new URL(safeFilename, directory);
  if (resolved.origin !== directory.origin || !resolved.pathname.startsWith(directory.pathname)) {
    throw new SampleDocsError('The sample URL escapes the corpus directory.');
  }
  return resolved.href;
}

function mediaType(headers) {
  return (headers.get('content-type') || '').split(';', 1)[0].trim().toLowerCase();
}

function normalizeFetchError(error, fallback) {
  if (error instanceof SampleDocsError) return error;
  if (error?.name === 'AbortError') {
    return new SampleDocsError('Sample loading was cancelled.', { code: 'aborted', cause: error });
  }
  return new SampleDocsError(fallback, { code: 'network', cause: error });
}

export async function loadSampleManifest({ url, fetchImpl = globalThis.fetch, signal } = {}) {
  try {
    const response = await fetchImpl(url, {
      signal,
      credentials: 'same-origin',
      redirect: 'error',
      cache: 'no-cache',
    });
    if (!response.ok) {
      throw new SampleDocsError(`Sample manifest request failed with status ${response.status}.`, {
        code: 'http',
      });
    }
    if (mediaType(response.headers) !== 'application/json') {
      throw new SampleDocsError('Sample manifest has an invalid MIME type.', { code: 'mime' });
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength > SAMPLE_MANIFEST_MAX_BYTES) {
      throw new SampleDocsError('Sample manifest is too large.', { code: 'size' });
    }
    let payload;
    try {
      const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
      payload = JSON.parse(text);
    } catch (error) {
      throw new SampleDocsError('Sample manifest is not valid UTF-8 JSON.', {
        code: 'invalid',
        cause: error,
      });
    }
    return normalizeSampleManifest(payload);
  } catch (error) {
    throw normalizeFetchError(error, 'Sample manifest is unavailable.');
  }
}

export async function loadSampleManifestState(options) {
  try {
    return Object.freeze({ status: 'ready', manifest: await loadSampleManifest(options), error: null });
  } catch (error) {
    return Object.freeze({
      status: 'unavailable',
      manifest: null,
      error: normalizeFetchError(error, 'Sample manifest is unavailable.'),
    });
  }
}

export function hasExpectedMagic(bytes, mime) {
  if (!(bytes instanceof Uint8Array)) bytes = new Uint8Array(bytes);
  if (mime === 'image/png') {
    const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
    return signature.every((value, index) => bytes[index] === value);
  }
  if (mime === 'image/jpeg') {
    return bytes.length >= 4
      && bytes[0] === 0xff
      && bytes[1] === 0xd8
      && bytes[2] === 0xff
      && bytes.at(-2) === 0xff
      && bytes.at(-1) === 0xd9;
  }
  if (mime === 'application/pdf') {
    return bytes.length >= 5
      && String.fromCharCode(...bytes.subarray(0, 5)) === '%PDF-';
  }
  return false;
}

export async function loadSampleFile(sample, {
  manifestUrl,
  fetchImpl = globalThis.fetch,
  FileImpl = globalThis.File,
  signal,
  isCurrent = () => true,
} = {}) {
  const url = resolveSampleUrl(manifestUrl, sample.asset);
  try {
    const response = await fetchImpl(url, {
      signal,
      credentials: 'same-origin',
      redirect: 'error',
      cache: 'no-cache',
    });
    if (!response.ok) {
      throw new SampleDocsError(`Sample request failed with status ${response.status}.`, {
        code: 'http',
      });
    }
    if (mediaType(response.headers) !== sample.mime) {
      throw new SampleDocsError('Sample response MIME does not match its manifest.', {
        code: 'mime',
      });
    }
    const contentLength = Number(response.headers.get('content-length'));
    if (Number.isFinite(contentLength) && contentLength > maximumBytesForKind(sample.kind)) {
      throw new SampleDocsError('Sample exceeds the frontend file-size limit.', { code: 'size' });
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (!isCurrent()) throw new SampleDocsError('A newer source replaced this sample.', { code: 'stale' });
    if (bytes.byteLength !== sample.bytes) {
      throw new SampleDocsError('Sample byte size does not match its manifest.', { code: 'size' });
    }
    if (bytes.byteLength > maximumBytesForKind(sample.kind)) {
      throw new SampleDocsError('Sample exceeds the frontend file-size limit.', { code: 'size' });
    }
    if (!hasExpectedMagic(bytes, sample.mime)) {
      throw new SampleDocsError('Sample signature does not match its manifest.', { code: 'format' });
    }
    if (typeof FileImpl !== 'function') {
      throw new SampleDocsError('This browser cannot create a sample file.', { code: 'unsupported' });
    }
    return new FileImpl([bytes], sample.asset, {
      type: sample.mime,
      lastModified: 0,
    });
  } catch (error) {
    throw normalizeFetchError(error, 'Sample asset is unavailable.');
  }
}

export function sampleUiMetadata(sample) {
  const settings = sample.suggestedSettings;
  const parts = [settings.engine === 'browser' ? 'Browser OCR' : 'Server OCR'];
  if (settings.profile) parts.push(OCR_PROFILES[settings.profile].label);
  if (settings.preprocessing === 'threshold') parts.push(`threshold ${settings.threshold}`);
  else if (settings.preprocessing !== 'none') parts.push(settings.preprocessing);
  return Object.freeze({
    typeLabel: sample.kind === 'pdf' ? `PDF · ${sample.pageCount} pages` : sample.mime.replace('image/', '').toUpperCase(),
    languageLabel: OCR_LANGUAGES[sample.language],
    settingsLabel: parts.join(' · '),
    requiresSignIn: sample.kind === 'pdf',
  });
}
