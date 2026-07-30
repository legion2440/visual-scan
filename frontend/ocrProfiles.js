/**
 * Shared, environment-neutral OCR registry.
 *
 * Browser runtime code and Node.js setup scripts both import this module.
 * Keep it free of DOM, filesystem, and environment-specific configuration.
 */

export const OCR_BASE_LANGUAGE_IDS = Object.freeze([
  'eng',
  'rus',
  'deu',
  'fra',
  'spa',
]);

export const OCR_LANGUAGE_ORDER = Object.freeze([
  'eng',
  'rus',
  'eng+rus',
  'deu',
  'fra',
  'spa',
]);

export const OCR_LANGUAGES = Object.freeze({
  eng: 'English',
  rus: 'Russian',
  'eng+rus': 'English + Russian',
  deu: 'German',
  fra: 'French',
  spa: 'Spanish',
});

const SUPPORTED_BASE_LANGUAGES = OCR_BASE_LANGUAGE_IDS;

export const OCR_PROFILES = Object.freeze({
  fast: Object.freeze({
    id: 'fast',
    label: 'Fast',
    directory: 'fast',
    repository: 'tessdata_fast',
    supportedLanguages: SUPPORTED_BASE_LANGUAGES,
  }),
  standard: Object.freeze({
    id: 'standard',
    label: 'Standard',
    directory: 'standard',
    repository: 'tessdata',
    supportedLanguages: SUPPORTED_BASE_LANGUAGES,
  }),
  best: Object.freeze({
    id: 'best',
    label: 'Best',
    directory: 'best',
    repository: 'tessdata_best',
    supportedLanguages: SUPPORTED_BASE_LANGUAGES,
  }),
});

export const DEFAULT_OCR_PROFILE = 'fast';

export function resolveOcrLanguageCodes(language) {
  return String(language).split('+').filter(Boolean);
}
