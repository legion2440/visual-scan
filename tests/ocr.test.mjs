import assert from 'node:assert/strict';
import test from 'node:test';

function deferred() {
  let resolve;
  const promise = new Promise((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

test('shutdown waits for queued worker creation and recognition before termination', async (t) => {
  const originalFetch = globalThis.fetch;
  const originalWindow = globalThis.window;
  t.after(() => {
    globalThis.fetch = originalFetch;
    if (originalWindow === undefined) delete globalThis.window;
    else globalThis.window = originalWindow;
  });

  const creationGate = deferred();
  const creationStarted = deferred();
  const recognitionGate = deferred();
  const recognitionStarted = deferred();
  const events = [];

  const worker = {
    async recognize() {
      events.push('recognize:start');
      recognitionStarted.resolve();
      await recognitionGate.promise;
      events.push('recognize:end');
      return {
        data: {
          text: 'graceful shutdown',
          confidence: 99,
          words: [{ text: 'graceful' }, { text: 'shutdown' }],
        },
      };
    },
    async terminate() {
      events.push('terminate');
    },
  };

  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({ fast: ['eng'], standard: [], best: [] }),
  });
  globalThis.window = {
    Tesseract: {
      async createWorker() {
        events.push('create:start');
        creationStarted.resolve();
        await creationGate.promise;
        events.push('create:end');
        return worker;
      },
    },
  };

  const moduleUrl = new URL('../frontend/utils/ocr.js?shutdown-contract', import.meta.url);
  const ocr = await import(moduleUrl.href);
  const recognition = ocr.recognize({}, { profile: 'fast', lang: 'eng' });
  await creationStarted.promise;

  let shutdownSettled = false;
  const shutdown = ocr.shutdown().then(() => {
    shutdownSettled = true;
    events.push('shutdown:end');
  });

  await Promise.resolve();
  assert.equal(shutdownSettled, false);
  assert.deepEqual(events, ['create:start']);

  creationGate.resolve();
  await recognitionStarted.promise;
  assert.equal(shutdownSettled, false);
  assert.equal(events.includes('terminate'), false);

  recognitionGate.resolve();
  const result = await recognition;
  await shutdown;

  assert.equal(result.text, 'graceful shutdown');
  assert.deepEqual(events, [
    'create:start',
    'create:end',
    'recognize:start',
    'recognize:end',
    'terminate',
    'shutdown:end',
  ]);
});
