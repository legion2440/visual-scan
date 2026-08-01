#!/usr/bin/env node

import { createHash } from 'node:crypto';
import {
  lstat,
  readFile,
  readdir,
  realpath,
} from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import {
  SAMPLE_CORPUS_MAX_BYTES,
  hasExpectedMagic,
  normalizeSampleManifest,
} from '../frontend/utils/samples.js';

const REPOSITORY_ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
export const DEFAULT_SAMPLE_DIRECTORY = path.join(REPOSITORY_ROOT, 'public', 'sample-docs');
const BINARY_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.pdf']);

export class SampleCorpusVerificationError extends Error {
  constructor(message, { cause } = {}) {
    super(message);
    this.name = 'SampleCorpusVerificationError';
    this.cause = cause;
  }
}

function corpusError(message, cause) {
  return new SampleCorpusVerificationError(message, { cause });
}

export function validateCorpusDirectoryEntries(directoryEntries, declaredAssets) {
  const seenNames = new Set();
  for (const entry of directoryEntries) {
    const normalizedName = entry.name.toLowerCase();
    if (seenNames.has(normalizedName)) {
      throw corpusError(`Corpus filenames must be unique ignoring case: ${entry.name}.`);
    }
    seenNames.add(normalizedName);
    if (entry.isSymbolicLink()) {
      throw corpusError(`Corpus paths must not be symbolic links: ${entry.name}.`);
    }

    const extension = path.extname(entry.name).toLowerCase();
    if (BINARY_EXTENSIONS.has(extension)) {
      if (!entry.isFile()) {
        throw corpusError(`Binary sample path must be a regular file: ${entry.name}.`);
      }
      if (!declaredAssets.has(normalizedName)) {
        throw corpusError(`Undeclared binary sample asset: ${entry.name}.`);
      }
    }
  }
}

export function sha256Hex(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

export function pngDimensions(bytes) {
  if (!hasExpectedMagic(bytes, 'image/png') || bytes.length < 24) {
    throw corpusError('PNG header is invalid.');
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const width = view.getUint32(16, false);
  const height = view.getUint32(20, false);
  if (!width || !height) throw corpusError('PNG dimensions are invalid.');
  return Object.freeze({ width, height });
}

export function jpegDimensions(bytes) {
  if (!hasExpectedMagic(bytes, 'image/jpeg')) throw corpusError('JPEG header is invalid.');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const startOfFrameMarkers = new Set([
    0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
    0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf,
  ]);
  let offset = 2;
  while (offset + 4 <= bytes.length) {
    while (offset < bytes.length && bytes[offset] !== 0xff) offset += 1;
    while (offset < bytes.length && bytes[offset] === 0xff) offset += 1;
    if (offset >= bytes.length) break;
    const marker = bytes[offset];
    offset += 1;
    if (marker === 0xd8 || marker === 0xd9 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    if (offset + 2 > bytes.length) break;
    const length = view.getUint16(offset, false);
    if (length < 2 || offset + length > bytes.length) break;
    if (startOfFrameMarkers.has(marker)) {
      if (length < 7) break;
      const height = view.getUint16(offset + 3, false);
      const width = view.getUint16(offset + 5, false);
      if (!width || !height) break;
      return Object.freeze({ width, height });
    }
    offset += length;
  }
  throw corpusError('JPEG dimensions could not be read.');
}

export function pdfPageCount(bytes) {
  if (!hasExpectedMagic(bytes, 'application/pdf')) throw corpusError('PDF header is invalid.');
  const source = new TextDecoder('latin1').decode(bytes);
  if (!source.includes('%%EOF')) throw corpusError('PDF end marker is missing.');
  const count = source.match(/\/Type\s*\/Page\b/g)?.length || 0;
  if (!count) throw corpusError('PDF contains no classic page objects.');
  return count;
}

function dimensionsFor(sample, bytes) {
  if (sample.mime === 'image/png') return pngDimensions(bytes);
  if (sample.mime === 'image/jpeg') return jpegDimensions(bytes);
  throw corpusError(`Unsupported image MIME: ${sample.mime}.`);
}

function confinedPath(directory, filename) {
  const target = path.resolve(directory, filename);
  const relative = path.relative(directory, target);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw corpusError(`Path escapes the sample corpus: ${filename}.`);
  }
  return target;
}

async function readRegularFile(directory, filename) {
  const target = confinedPath(directory, filename);
  let metadata;
  try {
    metadata = await lstat(target);
  } catch (error) {
    throw corpusError(`Required sample file is missing: ${filename}.`, error);
  }
  if (metadata.isSymbolicLink() || !metadata.isFile()) {
    throw corpusError(`Sample path must be a regular file, not a symlink: ${filename}.`);
  }
  const canonicalDirectory = await realpath(directory);
  const canonicalTarget = await realpath(target);
  const relative = path.relative(canonicalDirectory, canonicalTarget);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw corpusError(`Resolved sample path escapes the corpus: ${filename}.`);
  }
  return new Uint8Array(await readFile(canonicalTarget));
}

function decodeUtf8(bytes, filename) {
  let text;
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch (error) {
    throw corpusError(`${filename} is not valid UTF-8.`, error);
  }
  if (text.startsWith('\uFEFF')) throw corpusError(`${filename} must not contain a UTF-8 BOM.`);
  if (text.includes('\r')) throw corpusError(`${filename} must use LF line endings.`);
  if (!text.trim()) throw corpusError(`${filename} must not be empty.`);
  return text;
}

export async function verifySampleCorpus({
  directory = DEFAULT_SAMPLE_DIRECTORY,
} = {}) {
  const corpusDirectory = path.resolve(directory);
  const manifestBytes = await readRegularFile(corpusDirectory, 'manifest.json');
  let manifestPayload;
  try {
    manifestPayload = JSON.parse(decodeUtf8(manifestBytes, 'manifest.json'));
  } catch (error) {
    if (error instanceof SampleCorpusVerificationError) throw error;
    throw corpusError('manifest.json is not valid JSON.', error);
  }
  let manifest;
  try {
    manifest = normalizeSampleManifest(manifestPayload);
  } catch (error) {
    throw corpusError(error.message, error);
  }

  decodeUtf8(await readRegularFile(corpusDirectory, 'SOURCES.md'), 'SOURCES.md');
  const declaredAssets = new Set();
  let totalAssetBytes = 0;
  for (const sample of manifest.samples) {
    declaredAssets.add(sample.asset.toLowerCase());
    const asset = await readRegularFile(corpusDirectory, sample.asset);
    if (asset.byteLength !== sample.bytes) {
      throw corpusError(`${sample.asset} byte size does not match manifest.json.`);
    }
    if (sha256Hex(asset) !== sample.sha256) {
      throw corpusError(`${sample.asset} SHA-256 does not match manifest.json.`);
    }
    if (!hasExpectedMagic(asset, sample.mime)) {
      throw corpusError(`${sample.asset} signature does not match ${sample.mime}.`);
    }
    totalAssetBytes += asset.byteLength;

    const reference = decodeUtf8(
      await readRegularFile(corpusDirectory, sample.referenceText),
      sample.referenceText,
    );
    if (sample.kind === 'image') {
      const actual = dimensionsFor(sample, asset);
      if (actual.width !== sample.width || actual.height !== sample.height) {
        throw corpusError(`${sample.asset} dimensions do not match manifest.json.`);
      }
    } else {
      const actualPageCount = pdfPageCount(asset);
      if (actualPageCount !== sample.pageCount) {
        throw corpusError(`${sample.asset} page count does not match manifest.json.`);
      }
      if (reference.split('\f').length !== sample.pageCount) {
        throw corpusError(`${sample.referenceText} must contain one form-feed-separated section per page.`);
      }
    }
  }
  if (totalAssetBytes > SAMPLE_CORPUS_MAX_BYTES) {
    throw corpusError(`Sample assets exceed ${SAMPLE_CORPUS_MAX_BYTES} bytes in total.`);
  }

  const directoryEntries = await readdir(corpusDirectory, { withFileTypes: true });
  validateCorpusDirectoryEntries(directoryEntries, declaredAssets);
  return Object.freeze({
    sampleCount: manifest.samples.length,
    totalAssetBytes,
    manifest,
  });
}

async function main() {
  const result = await verifySampleCorpus();
  process.stdout.write(
    `Verified ${result.sampleCount} sample documents (${result.totalAssetBytes} bytes).\n`,
  );
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : '';
if (invokedPath === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`Sample corpus verification failed: ${error.message}\n`);
    process.exitCode = 1;
  });
}
