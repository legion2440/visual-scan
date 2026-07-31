import assert from 'node:assert/strict';
import test from 'node:test';

import { api, ApiError, request } from '../frontend/utils/api.js';

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

test('health decodes JSON and scan list serializes query parameters', async (t) => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return jsonResponse(
      calls.length === 1
        ? { status: 'ok', ai_available: false, provider: null }
        : { items: [], total: 0, limit: 25, offset: 0 },
    );
  };

  assert.equal((await api.health()).status, 'ok');
  await api.listScans({
    limit: 25,
    offset: 0,
    q: 'invoice',
    classification: 'invoice',
    sort: 'filename',
    order: 'asc',
  });

  const url = new URL(calls[1].url);
  assert.equal(url.pathname, '/api/scans');
  assert.equal(url.searchParams.get('q'), 'invoice');
  assert.equal(url.searchParams.get('classification'), 'invoice');
  assert.equal(calls[1].options.headers, undefined);
});

test('image and PDF OCR send exact multipart controls', async (t) => {
  const originalFetch = globalThis.fetch;
  const bodies = [];
  t.after(() => { globalThis.fetch = originalFetch; });
  const requests = [];
  globalThis.fetch = async (_url, options) => {
    requests.push(options);
    bodies.push(options.body);
    return jsonResponse({ text: 'ok' });
  };

  const image = new File(['png'], 'processed.png', { type: 'image/png' });
  await api.recognizeImage(image, { language: 'eng+rus' });
  assert.equal(bodies[0].get('language'), 'eng+rus');
  assert.equal(bodies[0].get('preprocessing'), 'none');
  assert.equal(bodies[0].has('threshold'), false);
  assert.equal(requests[0].headers, undefined);

  const pdf = new File(['pdf'], 'document.pdf', { type: 'application/pdf' });
  await api.recognizePdf(pdf, {
    language: 'rus',
    preprocessing: 'threshold',
    threshold: 171,
    password: '',
  });
  assert.equal(bodies[1].get('preprocessing'), 'threshold');
  assert.equal(bodies[1].get('threshold'), '171');
  assert.equal(bodies[1].has('password'), false);
  assert.equal(requests[1].headers, undefined);
});

test('AI and scans CRUD use JSON bodies and exact endpoint methods', async (t) => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    if (options.method === 'DELETE' && String(url).endsWith('/one')) {
      return new Response(null, { status: 204 });
    }
    return jsonResponse(
      String(url).endsWith('/api/scans') && options.method === 'DELETE'
        ? { deleted: 1 }
        : { id: 'one' },
      options.method === 'POST' && String(url).endsWith('/api/scans') ? 201 : 200,
    );
  };

  await api.analyze({ filename: 'a.png', text: 'raw', language: 'eng' });
  await api.createScan({ filename: 'a.png', text: ' raw ', analysis: null, ocr: null });
  await api.getScan('one');
  await api.deleteScan('one');
  await api.clearScans();

  assert.deepEqual(calls.map((call) => [
    new URL(call.url).pathname,
    call.options.method,
  ]), [
    ['/api/ai/analyze', 'POST'],
    ['/api/scans', 'POST'],
    ['/api/scans/one', 'GET'],
    ['/api/scans/one', 'DELETE'],
    ['/api/scans', 'DELETE'],
  ]);
  assert.equal(calls[0].options.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    filename: 'a.png',
    text: ' raw ',
    analysis: null,
    ocr: null,
  });
});

test('FastAPI validation arrays become safe readable ApiError messages', async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => jsonResponse({
    detail: [
      { loc: ['body', 'text'], msg: 'Field required', input: '<secret>' },
      null,
    ],
  }, 422);

  await assert.rejects(
    api.createScan({}),
    (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, 422);
      assert.equal(error.kind, 'http');
      assert.equal(error.message, 'body.text: Field required');
      assert.equal(error.message.includes('secret'), false);
      return true;
    },
  );
});

test('HTTP detail strings and non-JSON success responses are supported', async (t) => {
  const originalFetch = globalThis.fetch;
  let call = 0;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => {
    call += 1;
    return call === 1
      ? jsonResponse({ detail: 'Scan not found.' }, 404)
      : new Response('plain response', {
        status: 200,
        headers: { 'content-type': 'text/plain' },
      });
  };
  await assert.rejects(api.getScan('missing'), {
    name: 'ApiError',
    message: 'Scan not found.',
    status: 404,
    kind: 'http',
  });
  assert.equal(await request('/plain', { timeoutMs: 100 }), 'plain response');
});

test('204 responses return null', async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(null, { status: 204 });
  assert.equal(await api.deleteScan('a scan/id'), null);
});

test('network, timeout, and caller cancellation have distinct kinds', async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });

  globalThis.fetch = async () => { throw new TypeError('network'); };
  await assert.rejects(request('/api/health', { timeoutMs: 20 }), {
    name: 'ApiError',
    kind: 'network',
    status: 0,
  });

  globalThis.fetch = (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  });
  await assert.rejects(request('/api/health', { timeoutMs: 5 }), {
    name: 'ApiError',
    kind: 'timeout',
    status: 0,
  });

  const controller = new AbortController();
  const pending = request('/api/health', { timeoutMs: 1_000, signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, {
    name: 'ApiError',
    kind: 'cancelled',
    status: 0,
  });
});

test('invalid JSON from an HTTP response remains a reachable HTTP error', async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response('{', {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
  await assert.rejects(api.health(), {
    name: 'ApiError',
    kind: 'http',
    status: 200,
  });
});

test('auth transport includes credentials and scopes in-memory CSRF to unsafe requests', async (t) => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  t.after(() => {
    globalThis.fetch = originalFetch;
    api.clearCsrfToken();
  });
  api.clearCsrfToken();
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return jsonResponse({ authenticated: false, user: null, csrf_token: null });
  };

  await api.authSession();
  api.setCsrfToken('memory-only-csrf');
  await api.register({ username: 'user', password: 'long enough password' });
  await api.listScans({});
  await api.createScan({ filename: 'a', text: 'text', analysis: null, ocr: null });
  await api.logout();

  assert.ok(calls.every((call) => call.options.credentials === 'include'));
  assert.equal(calls[0].options.headers, undefined);
  assert.equal(calls[1].options.headers['X-CSRF-Token'], undefined);
  assert.equal(calls[2].options.headers, undefined);
  assert.equal(calls[3].options.headers['X-CSRF-Token'], 'memory-only-csrf');
  assert.equal(calls[4].options.headers['X-CSRF-Token'], 'memory-only-csrf');
});

test('a 401 preserves CSRF until session verification confirms anonymous state', async (t) => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  t.after(() => {
    globalThis.fetch = originalFetch;
    api.clearCsrfToken();
  });
  api.setCsrfToken('expired-csrf');
  globalThis.fetch = async (_url, options) => {
    calls.push(options);
    return calls.length === 1
      ? jsonResponse({ detail: 'Authentication is required.' }, 401)
      : jsonResponse({ deleted: 0 });
  };

  await assert.rejects(api.clearScans(), { status: 401 });
  await api.clearScans();
  api.clearCsrfToken();
  await api.clearScans();
  assert.equal(calls[0].headers['X-CSRF-Token'], 'expired-csrf');
  assert.equal(calls[1].headers['X-CSRF-Token'], 'expired-csrf');
  assert.equal(calls[2].headers, undefined);
});

test('a stale 401 cannot disturb an explicitly rotated CSRF token', async (t) => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  let finishOldRequest;
  t.after(() => {
    globalThis.fetch = originalFetch;
    api.clearCsrfToken();
  });
  api.setCsrfToken('old-csrf');
  globalThis.fetch = async (_url, options) => {
    calls.push(options);
    if (calls.length === 1) {
      return new Promise((resolve) => {
        finishOldRequest = () => resolve(
          jsonResponse({ detail: 'Authentication is required.' }, 401),
        );
      });
    }
    return jsonResponse({ deleted: 0 });
  };

  const stale = api.clearScans();
  await new Promise((resolve) => setImmediate(resolve));
  api.setCsrfToken('new-csrf');
  finishOldRequest();
  await assert.rejects(stale, { status: 401 });
  await api.clearScans();

  assert.equal(calls[0].headers['X-CSRF-Token'], 'old-csrf');
  assert.equal(calls[1].headers['X-CSRF-Token'], 'new-csrf');
});
