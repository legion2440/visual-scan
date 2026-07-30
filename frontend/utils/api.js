/**
 * api.js — the only frontend module that performs HTTP requests.
 *
 * Baseline contract expected from the future backend:
 *
 *   GET  {backend}/api/health
 *        → { "status": "ok", "ai_available": true, "provider": "..." }
 *
 *   POST {backend}/api/ai/analyze
 *        { "filename": "contract.jpg", "text": "...", "language": "eng" }
 *        → { "filename": "contract.jpg",
 *            "classification": "contract",
 *            "confidence": 0.93,
 *            "summary": "...",
 *            "tags": ["legal"],
 *            "fields": [{ "label": "Date", "value": "2026-07-30" }],
 *            "provider": "..." }
 *
 * FastAPI-style errors may use { "detail": "message" }.
 */

import { CONFIG } from '../config.js';

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request(path, { method = 'GET', body, timeout = CONFIG.requestTimeoutMs } = {}) {
  const url = `${CONFIG.backendUrl.replace(/\/$/, '')}${path}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(url, {
      method,
      signal: controller.signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const payload = await response.json().catch(() => null);

    if (!response.ok) {
      const detail = payload && (payload.detail || payload.message);
      throw new ApiError(
        typeof detail === 'string'
          ? detail
          : `${method} ${path} failed with status ${response.status}.`,
        response.status,
      );
    }

    return payload;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error.name === 'AbortError') {
      throw new ApiError('The backend did not answer in time.');
    }
    throw new ApiError(`Backend is unavailable at ${CONFIG.backendUrl}.`);
  } finally {
    clearTimeout(timer);
  }
}

export const api = Object.freeze({
  health: () => request('/api/health', { timeout: CONFIG.healthTimeoutMs }),
  analyze: (payload) => request('/api/ai/analyze', { method: 'POST', body: payload }),
});
