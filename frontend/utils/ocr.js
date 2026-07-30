/**
 * ocr.js — client-side OCR through the pinned Tesseract.js build loaded by
 * index.html as the global `Tesseract`.
 *
 * One worker is kept alive per language so repeated scans do not download the
 * same trained data again during a session.
 */

const workers = new Map();
let currentProgress = null;

/** Language options offered in the baseline frontend. */
export const LANGUAGES = [
  { code: 'eng', label: 'English' },
  { code: 'rus', label: 'Russian' },
  { code: 'eng+rus', label: 'English + Russian' },
  { code: 'deu', label: 'German' },
  { code: 'fra', label: 'French' },
  { code: 'spa', label: 'Spanish' },
];

function ensureTesseract() {
  if (typeof window.Tesseract === 'undefined') {
    throw new Error('Tesseract.js did not load. Check the network connection and reload the page.');
  }
  return window.Tesseract;
}

async function getWorker(lang) {
  if (workers.has(lang)) return workers.get(lang);
  const Tesseract = ensureTesseract();
  const promise = Tesseract.createWorker(lang, 1, {
    logger: (message) => {
      if (!currentProgress) return;
      currentProgress({
        status: message.status,
        progress: typeof message.progress === 'number' ? message.progress : 0,
      });
    },
  });
  workers.set(lang, promise);
  try {
    return await promise;
  } catch (error) {
    workers.delete(lang);
    throw error;
  }
}

/**
 * Recognise text in a canvas, image, or blob.
 *
 * @param {CanvasImageSource|Blob} source
 * @param {{lang?: string, onProgress?: (p: {status: string, progress: number}) => void}} options
 * @returns {Promise<{text: string, confidence: number, words: number, lang: string, engine: string}>}
 */
export async function recognize(source, { lang = 'eng', onProgress } = {}) {
  currentProgress = onProgress || null;
  try {
    const worker = await getWorker(lang);
    const { data } = await worker.recognize(source);
    return {
      text: (data.text || '').replace(/\n{3,}/g, '\n\n').trim(),
      confidence: Math.round(data.confidence || 0),
      words: data.words
        ? data.words.length
        : (data.text || '').split(/\s+/).filter(Boolean).length,
      lang,
      engine: 'tesseract.js (browser)',
    };
  } finally {
    currentProgress = null;
  }
}

/** Free cached workers when the page is closed. */
export async function shutdown() {
  for (const promise of workers.values()) {
    try {
      (await promise).terminate();
    } catch {
      // The worker may already be gone.
    }
  }
  workers.clear();
}
