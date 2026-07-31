/** Shared, environment-neutral document intake limits and media types. */

export const SUPPORTED_IMAGE_TYPES = Object.freeze([
  'image/jpeg',
  'image/png',
  'image/webp',
]);

export const SUPPORTED_PDF_TYPE = 'application/pdf';

export const INTAKE_LIMITS = Object.freeze({
  maxImageBytes: 20 * 1024 * 1024,
  maxPdfBytes: 50 * 1024 * 1024,
  maxImagePixels: 25_000_000,
});

export function maximumBytesForKind(kind) {
  return kind === 'pdf'
    ? INTAKE_LIMITS.maxPdfBytes
    : INTAKE_LIMITS.maxImageBytes;
}
