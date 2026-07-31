/**
 * Pure archive/state helpers. This module intentionally has no DOM, storage,
 * or HTTP access so its race and contract rules can be tested in Node.
 */

export const CLASSIFICATIONS = Object.freeze([
  'unclassified',
  'invoice',
  'receipt',
  'contract',
  'letter',
  'form',
  'report',
  'statement',
  'identity_document',
  'certificate',
  'business_card',
  'note',
  'other',
]);

export const MAX_ARCHIVE_QUERY_LENGTH = 200;

const ANALYSIS_LIMITS = Object.freeze({
  tag: 100,
  fieldLabel: 200,
  fieldValue: 5_000,
});

function codePointLength(value) {
  return Array.from(value).length;
}

export class ArchiveContractError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ArchiveContractError';
  }
}

export function formatDate(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(undefined, {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function snippet(text, length = 140) {
  const flat = String(text || '').replace(/\s+/g, ' ').trim();
  return flat.length > length ? `${flat.slice(0, length - 1)}…` : flat;
}

export function nextBackendState(current, error) {
  if (!error || error.kind === 'cancelled') return current;
  if (error.kind === 'http' || error.status > 0) return 'up';
  if (error.kind === 'network' || error.kind === 'timeout') return 'down';
  return current;
}

export function analysisFingerprint({ sourceRevision, filename, language, text }) {
  return JSON.stringify([sourceRevision, filename, language, text]);
}

export function isAnalysisCurrent(ai, context) {
  return Boolean(
    ai
    && ai.analyzedText === context.text
    && ai.fingerprint === analysisFingerprint(context),
  );
}

export function mapBrowserOcr(result) {
  return {
    source: 'browser',
    engine: result.engine || 'tesseract.js',
    language: result.lang,
    profile: result.profile,
    confidence: Number.isFinite(result.confidence) ? result.confidence : null,
    words: Number.isInteger(result.words) ? result.words : 0,
  };
}

export function mapServerImageOcr(result) {
  return {
    source: 'server',
    engine: result.engine || 'tesseract',
    language: result.language,
    profile: null,
    confidence: Number.isFinite(result.confidence) ? result.confidence : null,
    words: Number.isInteger(result.words) ? result.words : 0,
  };
}

export function mapServerPdfOcr(result) {
  const pages = Array.isArray(result.pages) ? result.pages : [];
  return {
    snapshot: {
      source: 'server',
      engine: result.engine || 'tesseract',
      language: result.language,
      profile: null,
      confidence: null,
      words: pages.reduce(
        (total, page) => total + (Number.isInteger(page?.words) ? page.words : 0),
        0,
      ),
    },
    pageCount: Number.isInteger(result.page_count) ? result.page_count : pages.length,
  };
}

export function validateAnalysisSnapshot(result) {
  if (!result || typeof result !== 'object') {
    throw new ArchiveContractError('AI analysis is not a valid archive snapshot.');
  }
  const tags = Array.isArray(result.tags) ? result.tags : [];
  if (tags.some((tag) => (
    typeof tag !== 'string'
    || codePointLength(tag) > ANALYSIS_LIMITS.tag
  ))) {
    throw new ArchiveContractError(
      `AI analysis contains a tag longer than ${ANALYSIS_LIMITS.tag} characters.`,
    );
  }
  const fields = Array.isArray(result.fields) ? result.fields : [];
  if (fields.some((field) => (
    !field
    || typeof field.label !== 'string'
    || typeof field.value !== 'string'
    || codePointLength(field.label) > ANALYSIS_LIMITS.fieldLabel
    || codePointLength(field.value) > ANALYSIS_LIMITS.fieldValue
  ))) {
    throw new ArchiveContractError(
      'AI analysis contains a structured field that exceeds the archive limits.',
    );
  }
  if (
    typeof result.provider !== 'string'
    || !result.provider.trim()
    || codePointLength(result.provider.trim()) > 100
  ) {
    throw new ArchiveContractError('AI analysis provider exceeds the archive limits.');
  }
  return {
    classification: result.classification,
    confidence: result.confidence,
    summary: result.summary,
    tags,
    fields,
    provider: result.provider,
  };
}

export function buildScanPayload({
  filename,
  text,
  ai,
  analysisContext,
  ocr,
  omitAnalysis = false,
}) {
  if (!String(text).trim()) throw new ArchiveContractError('Scan text cannot be empty.');
  let analysis = null;
  if (!omitAnalysis && isAnalysisCurrent(ai, analysisContext)) {
    analysis = validateAnalysisSnapshot(ai.result);
  }
  return {
    filename: filename || 'untitled',
    text,
    analysis,
    ocr: ocr || null,
  };
}

export function parsePdfThreshold(preprocessing, rawValue) {
  if (preprocessing !== 'threshold') return null;
  if (String(rawValue).trim() === '') {
    throw new ArchiveContractError(
      'PDF threshold must be a whole number from 0 to 255.',
    );
  }
  const threshold = Number(rawValue);
  if (!Number.isInteger(threshold) || threshold < 0 || threshold > 255) {
    throw new ArchiveContractError(
      'PDF threshold must be a whole number from 0 to 255.',
    );
  }
  return threshold;
}

export function listQuery({
  query = '',
  classification = 'all',
  sort = 'scanned_at',
  order = 'desc',
  limit = 50,
  offset = 0,
} = {}) {
  const q = query.trim().slice(0, MAX_ARCHIVE_QUERY_LENGTH);
  return {
    limit,
    offset,
    sort,
    order,
    ...(q ? { q } : {}),
    ...(classification !== 'all' ? { classification } : {}),
  };
}

export function previousPageOffset(offset, limit) {
  return Math.max(0, offset - limit);
}

export function nextPageOffset(offset, limit, total) {
  return Math.min(offset + limit, Math.max(0, Math.floor(Math.max(0, total - 1) / limit) * limit));
}

export function offsetAfterDelete(offset, limit, remainingTotal) {
  return validPageOffset(offset, limit, remainingTotal);
}

export function validPageOffset(offset, limit, total) {
  if (total <= 0) return 0;
  return Math.min(offset, Math.floor((total - 1) / limit) * limit);
}

async function mapConcurrent(values, concurrency, mapper) {
  const results = new Array(values.length);
  let cursor = 0;
  async function worker() {
    while (cursor < values.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await mapper(values[index], index);
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(concurrency, values.length) }, () => worker()),
  );
  return results;
}

export async function collectArchiveForExport({
  listScans,
  getScan,
  pageSize = 200,
  concurrency = 4,
}) {
  const ids = [];
  const seen = new Set();
  let initialTotal = null;
  let offset = 0;

  while (initialTotal === null || ids.length < initialTotal) {
    const page = await listScans({
      limit: pageSize,
      offset,
      sort: 'scanned_at',
      order: 'asc',
    });
    if (!page || !Array.isArray(page.items) || !Number.isInteger(page.total)) {
      throw new ArchiveContractError('The archive list response is invalid.');
    }
    if (initialTotal === null) initialTotal = page.total;
    if (page.total !== initialTotal) {
      throw new ArchiveContractError('The archive changed during export. Try again.');
    }
    if (!page.items.length && ids.length < initialTotal) {
      throw new ArchiveContractError('The archive changed during export. Try again.');
    }
    for (const item of page.items) {
      if (!item || typeof item.id !== 'string' || seen.has(item.id)) {
        throw new ArchiveContractError('The archive changed during export. Try again.');
      }
      seen.add(item.id);
      ids.push(item.id);
    }
    offset += page.items.length;
  }

  if (ids.length !== initialTotal) {
    throw new ArchiveContractError('The archive changed during export. Try again.');
  }

  const details = await mapConcurrent(ids, concurrency, (id) => getScan(id));
  const finalProbe = await listScans({
    limit: 1,
    offset: 0,
    sort: 'scanned_at',
    order: 'asc',
  });
  if (!finalProbe || finalProbe.total !== initialTotal) {
    throw new ArchiveContractError('The archive changed during export. Try again.');
  }
  return details;
}
