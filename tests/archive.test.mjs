import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ArchiveContractError,
  CLASSIFICATIONS,
  analysisFingerprint,
  buildScanPayload,
  collectArchiveForExport,
  isAnalysisCurrent,
  listQuery,
  mapBrowserOcr,
  mapServerPdfOcr,
  nextBackendState,
  offsetAfterDelete,
  previousPageOffset,
  nextPageOffset,
} from '../frontend/utils/archive.js';

function currentAi(text = ' exact text ') {
  const context = {
    sourceRevision: 2,
    filename: 'scan.png',
    language: 'eng',
    text,
  };
  return {
    context,
    ai: {
      analyzedText: text,
      fingerprint: analysisFingerprint(context),
      result: {
        classification: 'invoice',
        confidence: 0.9,
        summary: 'Summary',
        tags: ['billing'],
        fields: [{ label: 'Total', value: '10' }],
        provider: 'example',
      },
    },
  };
}

test('analysis freshness requires exact raw text and full source fingerprint', () => {
  const { context, ai } = currentAi();
  assert.equal(isAnalysisCurrent(ai, context), true);
  assert.equal(isAnalysisCurrent(ai, { ...context, text: context.text.trim() }), false);
  assert.equal(isAnalysisCurrent(ai, { ...context, sourceRevision: 3 }), false);
});

test('scan payload preserves raw text and discards stale AI analysis', () => {
  const { context, ai } = currentAi();
  const fresh = buildScanPayload({
    filename: 'scan.png',
    text: context.text,
    ai,
    analysisContext: context,
    ocr: null,
  });
  assert.equal(fresh.text, ' exact text ');
  assert.equal(fresh.analysis.classification, 'invoice');

  const stale = buildScanPayload({
    filename: 'scan.png',
    text: 'changed',
    ai,
    analysisContext: { ...context, text: 'changed' },
    ocr: null,
  });
  assert.equal(stale.analysis, null);
});

test('manual text has null OCR metadata', () => {
  const context = {
    sourceRevision: 0,
    filename: 'untitled',
    language: 'eng',
    text: '  manually entered  ',
  };
  assert.deepEqual(buildScanPayload({
    filename: 'untitled',
    text: context.text,
    ai: null,
    analysisContext: context,
    ocr: null,
  }), {
    filename: 'untitled',
    text: '  manually entered  ',
    analysis: null,
    ocr: null,
  });
});

test('AI snapshot over archive limits is rejected rather than truncated', () => {
  const { context, ai } = currentAi();
  ai.result.tags = ['x'.repeat(101)];
  assert.throws(() => buildScanPayload({
    filename: 'scan.png',
    text: context.text,
    ai,
    analysisContext: context,
    ocr: null,
  }), ArchiveContractError);
});

test('OCR mappings preserve source and aggregate PDF word counts', () => {
  assert.deepEqual(mapBrowserOcr({
    engine: 'tesseract.js',
    lang: 'eng',
    profile: 'fast',
    confidence: 93,
    words: 4,
  }), {
    source: 'browser',
    engine: 'tesseract.js',
    language: 'eng',
    profile: 'fast',
    confidence: 93,
    words: 4,
  });
  assert.deepEqual(mapServerPdfOcr({
    engine: 'tesseract',
    language: 'rus',
    page_count: 2,
    pages: [{ words: 4 }, { words: 6 }],
  }), {
    snapshot: {
      source: 'server',
      engine: 'tesseract',
      language: 'rus',
      profile: null,
      confidence: null,
      words: 10,
    },
    pageCount: 2,
  });
});

test('reachability changes only for transport and HTTP ApiError kinds', () => {
  assert.equal(nextBackendState('unknown', { kind: 'http', status: 422 }), 'up');
  assert.equal(nextBackendState('up', { kind: 'timeout', status: 0 }), 'down');
  assert.equal(nextBackendState('up', { kind: 'network', status: 0 }), 'down');
  assert.equal(nextBackendState('up', { kind: 'cancelled', status: 0 }), 'up');
  assert.equal(nextBackendState('up', new TypeError('frontend')), 'up');
});

test('list query bounds text and deletion corrects the final-page offset', () => {
  const query = listQuery({
    query: `  ${'x'.repeat(250)}  `,
    classification: 'invoice',
    limit: 25,
    offset: 50,
  });
  assert.equal(query.q.length, 200);
  assert.equal(query.classification, 'invoice');
  assert.equal(offsetAfterDelete(50, 25, 50), 25);
  assert.equal(previousPageOffset(0, 25), 0);
  assert.equal(previousPageOffset(50, 25), 25);
  assert.equal(nextPageOffset(0, 25, 51), 25);
  assert.equal(nextPageOffset(50, 25, 51), 50);
  assert.deepEqual(listQuery({ query: '   ', classification: 'all' }), {
    limit: 50,
    offset: 0,
    sort: 'scanned_at',
    order: 'desc',
  });
});

test('classification filter is fixed and complete', () => {
  assert.deepEqual(CLASSIFICATIONS, [
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
});

test('best-effort export uses stable order, four-way details, and returns no partial result', async () => {
  const records = Array.from({ length: 5 }, (_, index) => ({
    id: `id-${index}`,
    scanned_at: `2026-01-0${index + 1}T00:00:00Z`,
  }));
  const listCalls = [];
  let active = 0;
  let maximumActive = 0;
  const result = await collectArchiveForExport({
    pageSize: 2,
    concurrency: 4,
    listScans: async (query) => {
      listCalls.push(query);
      return {
        items: records.slice(query.offset, query.offset + query.limit),
        total: records.length,
      };
    },
    getScan: async (id) => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      await Promise.resolve();
      active -= 1;
      return { id, text: id };
    },
  });
  assert.deepEqual(result.map((item) => item.id), records.map((item) => item.id));
  assert.equal(maximumActive, 4);
  assert.ok(listCalls.every((call) => call.sort === 'scanned_at' && call.order === 'asc'));
});

test('export detects total drift and duplicate IDs', async () => {
  let call = 0;
  await assert.rejects(collectArchiveForExport({
    pageSize: 1,
    listScans: async () => {
      call += 1;
      return call === 1
        ? { items: [{ id: 'one' }], total: 2 }
        : { items: [{ id: 'two' }], total: 3 };
    },
    getScan: async () => ({}),
  }), /changed during export/);

  await assert.rejects(collectArchiveForExport({
    pageSize: 2,
    listScans: async () => ({ items: [{ id: 'same' }, { id: 'same' }], total: 2 }),
    getScan: async () => ({}),
  }), /changed during export/);
});

test('a detail failure rejects the whole export result', async () => {
  let detailsStarted = 0;
  await assert.rejects(collectArchiveForExport({
    listScans: async (query) => ({
      items: query.offset === 0 ? [{ id: 'one' }, { id: 'two' }] : [],
      total: 2,
    }),
    getScan: async (id) => {
      detailsStarted += 1;
      if (id === 'two') throw new Error('detail failed');
      return { id };
    },
  }), /detail failed/);
  assert.equal(detailsStarted, 2);
});
