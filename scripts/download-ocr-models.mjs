import { createWriteStream } from 'node:fs';
import { access, mkdir, rename, rm, stat } from 'node:fs/promises';
import path from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { fileURLToPath } from 'node:url';

import {
  OCR_BASE_LANGUAGE_IDS,
  OCR_PROFILES,
  resolveOcrLanguageCodes,
} from '../frontend/ocrProfiles.js';
import { verifyOcrModels } from './verify-ocr-models.mjs';

const SCRIPT_DIRECTORY = path.dirname(fileURLToPath(import.meta.url));
const REPOSITORY_ROOT = path.resolve(SCRIPT_DIRECTORY, '..');
const TESSDATA_ROOT = path.join(REPOSITORY_ROOT, 'frontend', 'assets', 'tessdata');
const OFFICIAL_REPOSITORY_ROOT = 'https://raw.githubusercontent.com/tesseract-ocr';

function usage() {
  return [
    'Usage:',
    '  node scripts/download-ocr-models.mjs <profile> <language...> [--force]',
    '',
    `Profiles: ${Object.keys(OCR_PROFILES).join(', ')}`,
    `Languages: ${OCR_BASE_LANGUAGE_IDS.join(', ')} (eng+rus is also accepted)`,
  ].join('\n');
}

function parseArguments(args) {
  if (args.includes('--help') || args.includes('-h')) {
    console.log(usage());
    process.exit(0);
  }

  const unknownFlags = args.filter((value) => value.startsWith('-') && value !== '--force');
  if (unknownFlags.length) {
    throw new Error(`Unknown option: ${unknownFlags[0]}\n\n${usage()}`);
  }

  const force = args.includes('--force');
  const positional = args.filter((value) => value !== '--force');
  const [profileId, ...languageArguments] = positional;
  const profile = OCR_PROFILES[profileId];

  if (!profile || languageArguments.length === 0) {
    throw new Error(usage());
  }

  const languages = [...new Set(
    languageArguments.flatMap((value) => (
      value.split(',').flatMap(resolveOcrLanguageCodes)
    )),
  )];

  const invalidLanguage = languages.find(
    (language) => !OCR_BASE_LANGUAGE_IDS.includes(language),
  );
  if (invalidLanguage) {
    throw new Error(
      `Unsupported OCR language: ${invalidLanguage}\nConfigured languages: ${OCR_BASE_LANGUAGE_IDS.join(', ')}`,
    );
  }

  return { profile, languages, force };
}

async function pathExists(target) {
  try {
    await access(target);
    return true;
  } catch {
    return false;
  }
}

async function downloadModel(profile, language, force) {
  const directory = path.join(TESSDATA_ROOT, profile.directory);
  const target = path.join(directory, `${language}.traineddata`);
  await mkdir(directory, { recursive: true });

  if (await pathExists(target)) {
    const targetInfo = await stat(target);
    if (!targetInfo.isFile()) {
      throw new Error(`Cannot install over a non-file path: ${target}`);
    }
    if (!force) {
      console.log(`Keeping existing ${profile.id}/${language}.traineddata (use --force to replace it).`);
      return;
    }
  }

  const source = `${OFFICIAL_REPOSITORY_ROOT}/${profile.repository}/main/${language}.traineddata`;
  const temporary = path.join(
    directory,
    `.${language}.${process.pid}.${Date.now()}.traineddata.tmp`,
  );

  console.log(`Downloading ${profile.id}/${language} from ${profile.repository}…`);
  let response;
  try {
    response = await fetch(source, { redirect: 'follow' });
    if (!response.ok || !response.body) {
      throw new Error(`Download failed with HTTP ${response.status} ${response.statusText}`.trim());
    }

    await pipeline(
      Readable.fromWeb(response.body),
      createWriteStream(temporary, { flags: 'wx' }),
    );

    const temporaryInfo = await stat(temporary);
    if (temporaryInfo.size === 0) throw new Error('Downloaded file is empty.');

    if (force) await rm(target, { force: true });
    await rename(temporary, target);
    console.log(`Installed ${profile.id}/${language}.traineddata (${temporaryInfo.size} bytes).`);
  } catch (error) {
    await rm(temporary, { force: true });
    throw new Error(`Could not install ${profile.id}/${language}: ${error.message}`, {
      cause: error,
    });
  }
}

try {
  const { profile, languages, force } = parseArguments(process.argv.slice(2));
  for (const language of languages) {
    await downloadModel(profile, language, force);
  }

  const { manifestPath } = await verifyOcrModels();
  console.log(`Updated ${path.relative(REPOSITORY_ROOT, manifestPath)}.`);
  if (force) {
    console.warn(
      'Browser cache warning: --force does not invalidate Tesseract data already stored in IndexedDB.\n'
      + 'Before the next OCR run, clear this site’s data or change CONFIG.ocr.cachePrefix.',
    );
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
