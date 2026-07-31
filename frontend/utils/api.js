/**
 * The frontend's only HTTP transport module.
 *
 * It knows URLs, request serialization, deadlines, response decoding, and
 * transport error normalization. Application state (including backend
 * reachability) deliberately remains in app.js.
 */

import { CONFIG } from '../config.js';

let csrfToken = null;

function csrfExempt(path) {
  return path === '/api/auth/register' || path === '/api/auth/login';
}

function requestHeaders(path, method, body, requestCsrfToken) {
  const headers = {};
  if (body != null && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && !csrfExempt(path) && requestCsrfToken) {
    headers['X-CSRF-Token'] = requestCsrfToken;
  }
  return Object.keys(headers).length ? headers : undefined;
}

export class ApiError extends Error {
  constructor(message, { status = 0, kind = 'network', cause } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.kind = kind;
    this.cause = cause;
  }
}

function validationDetail(detail) {
  if (!Array.isArray(detail)) return null;
  const messages = detail
    .map((item) => {
      if (!item || typeof item !== 'object') return null;
      const location = Array.isArray(item.loc)
        ? item.loc.filter((part) => typeof part === 'string' || typeof part === 'number').join('.')
        : '';
      const message = typeof item.msg === 'string' ? item.msg : '';
      if (!message) return null;
      return location ? `${location}: ${message}` : message;
    })
    .filter(Boolean);
  return messages.length ? messages.join('\n') : null;
}

function responseErrorMessage(payload, method, path, status) {
  const detail = payload && typeof payload === 'object' ? payload.detail : null;
  if (typeof detail === 'string' && detail.trim()) return detail;
  const validation = validationDetail(detail);
  if (validation) return validation;
  if (payload && typeof payload.message === 'string' && payload.message.trim()) {
    return payload.message;
  }
  return `${method} ${path} failed with status ${status}.`;
}

function buildUrl(path, query) {
  const url = new URL(`${CONFIG.backendUrl.replace(/\/$/, '')}${path}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url;
}

async function readPayload(response) {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('json')) {
    try {
      return JSON.parse(text);
    } catch (error) {
      throw new ApiError('The backend returned invalid JSON.', {
        status: response.status,
        kind: 'http',
        cause: error,
      });
    }
  }
  return text;
}

export async function request(path, {
  method = 'GET',
  body,
  query,
  timeoutMs,
  signal,
} = {}) {
  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort(signal.reason);
  if (signal?.aborted) abortFromCaller();
  else signal?.addEventListener('abort', abortFromCaller, { once: true });

  const timer = timeoutMs == null ? null : setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  const isFormData = body instanceof FormData;
  const serializedBody = body == null || isFormData ? body : JSON.stringify(body);
  const requestCsrfToken = csrfToken;

  try {
    const response = await fetch(buildUrl(path, query), {
      method,
      credentials: 'include',
      signal: controller.signal,
      headers: requestHeaders(path, method, body, requestCsrfToken),
      body: serializedBody,
    });
    const payload = await readPayload(response);
    if (!response.ok) {
      throw new ApiError(responseErrorMessage(payload, method, path, response.status), {
        status: response.status,
        kind: 'http',
      });
    }
    return payload;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error?.name === 'AbortError' || controller.signal.aborted) {
      if (timedOut) {
        throw new ApiError('The backend did not answer in time.', {
          kind: 'timeout',
          cause: error,
        });
      }
      throw new ApiError('The request was cancelled.', {
        kind: 'cancelled',
        cause: error,
      });
    }
    throw new ApiError(`Backend is unavailable at ${CONFIG.backendUrl}.`, {
      kind: 'network',
      cause: error,
    });
  } finally {
    if (timer !== null) clearTimeout(timer);
    signal?.removeEventListener('abort', abortFromCaller);
  }
}

function imageForm(file, language) {
  const form = new FormData();
  form.append('file', file, file.name || 'processed.png');
  form.append('language', language);
  form.append('preprocessing', 'none');
  return form;
}

function pdfForm(file, { language, preprocessing, threshold, password }) {
  const form = new FormData();
  form.append('file', file, file.name || 'document.pdf');
  form.append('language', language);
  form.append('preprocessing', preprocessing);
  if (preprocessing === 'threshold') form.append('threshold', String(threshold));
  if (password) form.append('password', password);
  return form;
}

export const api = Object.freeze({
  setCsrfToken: (value) => {
    csrfToken = typeof value === 'string' && value ? value : null;
  },
  clearCsrfToken: () => {
    csrfToken = null;
  },
  authSession: ({ signal } = {}) => request('/api/auth/session', {
    timeoutMs: CONFIG.archiveTimeoutMs,
    signal,
  }),
  register: (payload, { signal } = {}) => request('/api/auth/register', {
    method: 'POST',
    body: payload,
    timeoutMs: CONFIG.archiveTimeoutMs,
    signal,
  }),
  login: (payload, { signal } = {}) => request('/api/auth/login', {
    method: 'POST',
    body: payload,
    timeoutMs: CONFIG.archiveTimeoutMs,
    signal,
  }),
  logout: ({ signal } = {}) => request('/api/auth/logout', {
    method: 'POST',
    timeoutMs: CONFIG.archiveTimeoutMs,
    signal,
  }),
  health: ({ signal } = {}) => request('/api/health', {
    timeoutMs: CONFIG.healthTimeoutMs,
    signal,
  }),
  recognizeImage: (file, { language, signal } = {}) => request('/api/ocr/recognize', {
    method: 'POST',
    body: imageForm(file, language),
    timeoutMs: CONFIG.imageOcrTimeoutMs,
    signal,
  }),
  recognizePdf: (file, options = {}) => request('/api/ocr/pdf/recognize', {
    method: 'POST',
    body: pdfForm(file, options),
    timeoutMs: CONFIG.pdfOcrTimeoutMs,
    signal: options.signal,
  }),
  analyze: (payload, { signal } = {}) => request('/api/ai/analyze', {
    method: 'POST',
    body: payload,
    timeoutMs: CONFIG.aiTimeoutMs,
    signal,
  }),
  listScans: (query, { signal } = {}) => request('/api/scans', {
    query,
    timeoutMs: CONFIG.archiveTimeoutMs,
    signal,
  }),
  createScan: (payload, { signal } = {}) => request('/api/scans', {
    method: 'POST',
    body: payload,
    timeoutMs: CONFIG.archiveTimeoutMs,
    signal,
  }),
  getScan: (id, { signal } = {}) => request(`/api/scans/${encodeURIComponent(id)}`, {
    timeoutMs: CONFIG.archiveTimeoutMs,
    signal,
  }),
  deleteScan: (id, { signal } = {}) => request(`/api/scans/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    timeoutMs: CONFIG.archiveTimeoutMs,
    signal,
  }),
  clearScans: ({ signal } = {}) => request('/api/scans', {
    method: 'DELETE',
    timeoutMs: CONFIG.archiveTimeoutMs,
    signal,
  }),
  legacyScans: ({ signal } = {}) => request('/api/scans/legacy', {
    timeoutMs: CONFIG.archiveTimeoutMs,
    signal,
  }),
  claimLegacyScans: ({ signal } = {}) => request('/api/scans/legacy/claim', {
    method: 'POST',
    timeoutMs: CONFIG.archiveTimeoutMs,
    signal,
  }),
});
