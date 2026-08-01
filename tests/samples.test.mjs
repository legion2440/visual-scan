import assert from 'node:assert/strict';
import { mkdtemp, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { INTAKE_LIMITS } from '../frontend/intakeContract.js';
import {
  SampleDocsError,
  loadSampleFile,
  loadSampleManifestState,
  normalizeSampleManifest,
} from '../frontend/utils/samples.js';
import {
  sha256Hex,
  validateCorpusDirectoryEntries,
  verifySampleCorpus,
} from '../scripts/verify-sample-docs.mjs';

function directoryEntry(name, { file = true, symbolicLink = false } = {}) {
  return {
    name,
    isFile: () => file,
    isSymbolicLink: () => symbolicLink,
  };
}

function pngBytes(width = 1, height = 1) {
  const bytes = new Uint8Array(32);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  bytes.set([0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52], 8);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width, false);
  view.setUint32(20, height, false);
  return bytes;
}

function imageEntry(overrides = {}) {
  const { binary = pngBytes(), ...fields } = overrides;
  const asset = fields.asset || 'sample.png';
  const referenceText = fields.reference_text || 'sample.txt';
  return {
    id: 'sample-one',
    label: 'Sample one',
    description: 'Synthetic sample document',
    asset,
    kind: 'image',
    mime: 'image/png',
    language: 'eng',
    reference_text: referenceText,
    suggested_classification: 'invoice',
    suggested_settings: { engine: 'browser', profile: 'fast' },
    bytes: binary.byteLength,
    sha256: sha256Hex(binary),
    width: 1,
    height: 1,
    ...fields,
  };
}

function pdfEntry(bytes) {
  return {
    id: 'sample-pdf',
    label: 'Sample PDF',
    description: 'Synthetic PDF sample document',
    asset: 'sample.pdf',
    kind: 'pdf',
    mime: 'application/pdf',
    language: 'eng',
    reference_text: 'sample-pdf.txt',
    suggested_classification: 'statement',
    suggested_settings: { engine: 'server', preprocessing: 'none' },
    bytes: bytes.byteLength,
    sha256: sha256Hex(bytes),
    page_count: 1,
  };
}

function manifest(...samples) {
  return { version: 1, samples };
}

async function fixtureDirectory(t) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'visual-scan-samples-'));
  t.after(async () => {
    const { rm } = await import('node:fs/promises');
    await rm(directory, { recursive: true, force: true });
  });
  await writeFile(path.join(directory, 'SOURCES.md'), '# Synthetic sources\n');
  return directory;
}

async function writeFixture(t, entry, bytes, { reference = true } = {}) {
  const directory = await fixtureDirectory(t);
  await writeFile(path.join(directory, 'manifest.json'), `${JSON.stringify(manifest(entry), null, 2)}\n`);
  await writeFile(path.join(directory, entry.asset), bytes);
  if (reference) await writeFile(path.join(directory, entry.reference_text), 'Authoritative text\n');
  return directory;
}

test('manifest normalization produces a strict immutable UI contract', () => {
  const normalized = normalizeSampleManifest(manifest(imageEntry()));
  assert.equal(normalized.version, 1);
  assert.equal(normalized.samples[0].referenceText, 'sample.txt');
  assert.equal(normalized.samples[0].suggestedSettings.preprocessing, 'none');
  assert.equal(Object.isFrozen(normalized.samples[0]), true);
});

test('manifest rejects duplicate IDs, unsafe paths, and unsupported kind or MIME', () => {
  const first = imageEntry();
  const duplicate = imageEntry({ asset: 'second.png', reference_text: 'second.txt' });
  assert.throws(() => normalizeSampleManifest(manifest(first, duplicate)), /Duplicate sample ID/);
  assert.throws(
    () => normalizeSampleManifest(manifest(imageEntry({ asset: '../escape.png' }))),
    /relative filename/,
  );
  assert.throws(
    () => normalizeSampleManifest(manifest(imageEntry({ mime: 'image/webp' }))),
    /do not match/,
  );
  assert.throws(
    () => normalizeSampleManifest(manifest(imageEntry({ kind: 'archive' }))),
    /image or pdf/,
  );
});

test('corpus verifier rejects a missing reference text', async (t) => {
  const bytes = pngBytes();
  const entry = imageEntry({ binary: bytes });
  const directory = await writeFixture(t, entry, bytes, { reference: false });
  await assert.rejects(verifySampleCorpus({ directory }), /Required sample file is missing: sample.txt/);
});

test('corpus verifier rejects checksum, byte-size, and dimension mismatches', async (t) => {
  const bytes = pngBytes();
  const cases = [
    ['checksum', { sha256: '0'.repeat(64) }, /SHA-256/],
    ['size', { bytes: bytes.byteLength + 1 }, /byte size/],
    ['dimensions', { width: 2 }, /dimensions/],
  ];
  for (const [name, overrides, expected] of cases) {
    await t.test(name, async (subtest) => {
      const entry = imageEntry({ binary: bytes, ...overrides });
      const directory = await writeFixture(subtest, entry, bytes);
      await assert.rejects(verifySampleCorpus({ directory }), expected);
    });
  }
});

test('corpus verifier rejects case-insensitive filename collisions', () => {
  const entries = [
    directoryEntry('sample.png'),
    directoryEntry('SAMPLE.PNG'),
  ];
  assert.throws(
    () => validateCorpusDirectoryEntries(entries, new Set(['sample.png'])),
    /unique ignoring case/,
  );
});

test('corpus verifier rejects an undeclared symlink with a non-binary extension', () => {
  const entries = [directoryEntry('undocumented-link.txt', { symbolicLink: true })];
  assert.throws(
    () => validateCorpusDirectoryEntries(entries, new Set()),
    /must not be symbolic links/,
  );
});

test('committed demo corpus passes complete verification', async () => {
  const result = await verifySampleCorpus();
  assert.equal(result.sampleCount, 6);
  assert.ok(result.totalAssetBytes > 0);
  assert.ok(result.totalAssetBytes <= 15 * 1024 * 1024);
});

test('image and PDF samples become ordinary File instances with exact MIME', async () => {
  const imageBytes = pngBytes();
  const image = normalizeSampleManifest(manifest(imageEntry({ binary: imageBytes }))).samples[0];
  const imageFile = await loadSampleFile(image, {
    manifestUrl: 'http://localhost:5500/public/sample-docs/manifest.json',
    fetchImpl: async () => new Response(imageBytes, {
      headers: { 'content-type': 'image/png' },
    }),
  });
  assert.ok(imageFile instanceof File);
  assert.equal(imageFile.name, 'sample.png');
  assert.equal(imageFile.type, 'image/png');

  const pdfBytes = new TextEncoder().encode('%PDF-1.4\n%%EOF\n');
  const pdf = normalizeSampleManifest(manifest(pdfEntry(pdfBytes))).samples[0];
  const pdfFile = await loadSampleFile(pdf, {
    manifestUrl: 'http://localhost:5500/public/sample-docs/manifest.json',
    fetchImpl: async () => new Response(pdfBytes, {
      headers: { 'content-type': 'application/pdf' },
    }),
  });
  assert.equal(pdfFile.name, 'sample.pdf');
  assert.equal(pdfFile.type, 'application/pdf');
});

test('sample fetch rejects HTTP, MIME, and oversized responses', async () => {
  const bytes = pngBytes();
  const sample = normalizeSampleManifest(manifest(imageEntry({ binary: bytes }))).samples[0];
  const options = { manifestUrl: 'http://localhost:5500/public/sample-docs/manifest.json' };

  await assert.rejects(loadSampleFile(sample, {
    ...options,
    fetchImpl: async () => new Response('missing', { status: 404 }),
  }), (error) => error instanceof SampleDocsError && error.code === 'http');
  await assert.rejects(loadSampleFile(sample, {
    ...options,
    fetchImpl: async () => new Response(bytes, {
      headers: { 'content-type': 'image/jpeg' },
    }),
  }), (error) => error instanceof SampleDocsError && error.code === 'mime');
  await assert.rejects(loadSampleFile(sample, {
    ...options,
    fetchImpl: async () => new Response(bytes, {
      headers: {
        'content-type': 'image/png',
        'content-length': String(INTAKE_LIMITS.maxImageBytes + 1),
      },
    }),
  }), (error) => error instanceof SampleDocsError && error.code === 'size');
});

test('stale sample completion cannot create a File', async () => {
  const bytes = pngBytes();
  const sample = normalizeSampleManifest(manifest(imageEntry({ binary: bytes }))).samples[0];
  let resolveFetch;
  let current = true;
  const pending = loadSampleFile(sample, {
    manifestUrl: 'http://localhost:5500/public/sample-docs/manifest.json',
    fetchImpl: () => new Promise((resolve) => { resolveFetch = resolve; }),
    isCurrent: () => current,
  });
  current = false;
  resolveFetch(new Response(bytes, { headers: { 'content-type': 'image/png' } }));
  await assert.rejects(
    pending,
    (error) => error instanceof SampleDocsError && error.code === 'stale',
  );
});

test('sample fetch normalizes caller abort', async () => {
  const bytes = pngBytes();
  const sample = normalizeSampleManifest(manifest(imageEntry({ binary: bytes }))).samples[0];
  const controller = new AbortController();
  const pending = loadSampleFile(sample, {
    manifestUrl: 'http://localhost:5500/public/sample-docs/manifest.json',
    signal: controller.signal,
    fetchImpl: (_url, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }),
  });
  controller.abort();
  await assert.rejects(
    pending,
    (error) => error instanceof SampleDocsError && error.code === 'aborted',
  );
});

test('manifest unavailability degrades to an isolated empty sample state', async () => {
  const result = await loadSampleManifestState({
    url: 'http://localhost:5500/public/sample-docs/manifest.json',
    fetchImpl: async () => { throw new TypeError('offline'); },
  });
  assert.equal(result.status, 'unavailable');
  assert.equal(result.manifest, null);
  assert.equal(result.error.code, 'network');
});
