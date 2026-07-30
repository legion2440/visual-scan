import { readdir, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  OCR_BASE_LANGUAGE_IDS,
  OCR_PROFILES,
} from '../frontend/ocrProfiles.js';

const SCRIPT_DIRECTORY = path.dirname(fileURLToPath(import.meta.url));
const REPOSITORY_ROOT = path.resolve(SCRIPT_DIRECTORY, '..');
const DEFAULT_TESSDATA_ROOT = path.join(
  REPOSITORY_ROOT,
  'frontend',
  'assets',
  'tessdata',
);

export class OcrModelStructureError extends Error {
  constructor(problems) {
    super(`Invalid OCR model structure:\n${problems.map((item) => `- ${item}`).join('\n')}`);
    this.name = 'OcrModelStructureError';
    this.problems = problems;
  }
}

async function inspectProfile(root, profile) {
  const directory = path.join(root, profile.directory);
  const installed = [];
  const problems = [];

  let entries;
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch (error) {
    problems.push(
      error.code === 'ENOENT'
        ? `Missing profile directory: ${profile.directory}/`
        : `Cannot read ${profile.directory}/: ${error.message}`,
    );
    return { installed, problems };
  }

  for (const entry of entries) {
    if (entry.name === '.gitkeep' && entry.isFile()) continue;

    if (!entry.isFile()) {
      problems.push(`${profile.directory}/${entry.name} must be a regular file.`);
      continue;
    }

    if (entry.name.endsWith('.traineddata.gz')) {
      problems.push(
        `${profile.directory}/${entry.name} is compressed; install an uncompressed .traineddata file.`,
      );
      continue;
    }

    const match = /^([a-z]{3})\.traineddata$/.exec(entry.name);
    if (!match) {
      problems.push(`Unexpected file in ${profile.directory}/: ${entry.name}`);
      continue;
    }

    const language = match[1];
    if (!OCR_BASE_LANGUAGE_IDS.includes(language)) {
      problems.push(
        `${profile.directory}/${entry.name} is not one of the configured OCR languages.`,
      );
      continue;
    }

    const info = await stat(path.join(directory, entry.name));
    if (info.size === 0) {
      problems.push(`${profile.directory}/${entry.name} is empty.`);
      continue;
    }

    installed.push(language);
  }

  installed.sort(
    (left, right) => OCR_BASE_LANGUAGE_IDS.indexOf(left) - OCR_BASE_LANGUAGE_IDS.indexOf(right),
  );
  return { installed, problems };
}

export async function verifyOcrModels({
  tessdataRoot = DEFAULT_TESSDATA_ROOT,
  writeManifest = true,
} = {}) {
  const root = path.resolve(tessdataRoot);
  const problems = [];

  let rootEntries;
  try {
    rootEntries = await readdir(root, { withFileTypes: true });
  } catch (error) {
    throw new OcrModelStructureError([
      error.code === 'ENOENT'
        ? `Missing tessdata root: ${root}`
        : `Cannot read tessdata root ${root}: ${error.message}`,
    ]);
  }

  const expectedDirectories = new Set(
    Object.values(OCR_PROFILES).map((profile) => profile.directory),
  );
  for (const entry of rootEntries) {
    if (expectedDirectories.has(entry.name) && entry.isDirectory()) continue;
    if (entry.name === 'manifest.json' && entry.isFile()) continue;
    problems.push(`Unexpected item in tessdata root: ${entry.name}`);
  }

  const manifest = {};
  for (const profile of Object.values(OCR_PROFILES)) {
    const result = await inspectProfile(root, profile);
    manifest[profile.id] = result.installed;
    problems.push(...result.problems);
  }

  if (problems.length) throw new OcrModelStructureError(problems);

  const manifestPath = path.join(root, 'manifest.json');
  if (writeManifest) {
    await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  }

  return { manifest, manifestPath };
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : '';
if (invokedPath === fileURLToPath(import.meta.url)) {
  try {
    const { manifest, manifestPath } = await verifyOcrModels();
    console.log(`OCR model manifest written to ${path.relative(REPOSITORY_ROOT, manifestPath)}`);
    console.log(JSON.stringify(manifest, null, 2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
