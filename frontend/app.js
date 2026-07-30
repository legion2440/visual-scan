/**
 * app.js — Visual Scan frontend.
 *
 * Wiring only: image handling lives in utils/imageUtils.js, OCR in
 * utils/ocr.js, all HTTP in utils/api.js, persistence and table logic in
 * utils/store.js.
 */

import * as IU from './utils/imageUtils.js';
import { CONFIG } from './config.js';
import { recognize, shutdown, LANGUAGES } from './utils/ocr.js';
import { api, ApiError } from './utils/api.js';
import {
  store, StorageError, newId, snippet, formatDate, view, classifications,
} from './utils/store.js';

const el = (id) => document.getElementById(id);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  original: null,          // canvas — the image as loaded
  base: null,              // canvas — crops baked in
  rotation: 0,             // 90° steps
  skew: 0,                 // fine rotation, degrees
  filters: { grayscale: false, threshold: null, invert: false },
  geom: null,              // canvas — base + rotation + skew (crop source)
  processed: null,         // canvas — geom + filters (OCR source)
  display: { w: 0, h: 0, scale: 1 },
  cropMode: false,
  cropRect: null,          // display coords
  file: { name: '', size: 0 },
  ocr: null,
  ai: null,
  scans: [],
  sort: { key: 'scanned_at', dir: -1 },
  backend: 'unknown',      // 'up' | 'down' | 'unknown'
};

/* ── tabs ─────────────────────────────────────────────────────────────── */

function showPanel(name) {
  $$('.tab').forEach((t) => {
    const on = t.dataset.panel === name;
    t.setAttribute('aria-selected', String(on));
  });
  el('panel-scan').hidden = name !== 'scan';
  el('panel-results').hidden = name !== 'results';
  window.scrollTo({ top: 0 });
}
$$('.tab').forEach((t) => t.addEventListener('click', () => showPanel(t.dataset.panel)));
document.addEventListener('click', (e) => {
  const goto = e.target.closest('[data-goto]');
  if (goto) showPanel(goto.dataset.goto);
});

/* ── backend connection ───────────────────────────────────────────────── */

function paintConnection() {
  const dot = el('conn-dot');
  const label = el('conn-label');
  if (state.backend === 'up') {
    dot.dataset.state = 'up';
    label.textContent = 'Backend: reachable';
  } else if (state.backend === 'down') {
    dot.dataset.state = 'down';
    label.textContent = 'Backend: unavailable';
  } else {
    dot.dataset.state = 'unknown';
    label.textContent = 'Backend: checking…';
  }
}

function setBackendFromApiError(error) {
  state.backend = error instanceof ApiError && error.status > 0 ? 'up' : 'down';
}

async function checkBackend() {
  state.backend = 'unknown';
  paintConnection();
  try {
    await api.health();
    state.backend = 'up';
  } catch (error) {
    setBackendFromApiError(error);
  }
  paintConnection();
}

el('conn-test').addEventListener('click', checkBackend);

/* ── image intake ─────────────────────────────────────────────────────── */

const dropzone = el('dropzone');
dropzone.addEventListener('click', () => el('file-input').click());
dropzone.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); el('file-input').click(); }
});
['dragenter', 'dragover'].forEach((ev) => dropzone.addEventListener(ev, (e) => {
  e.preventDefault(); dropzone.classList.add('is-over');
}));
['dragleave', 'drop'].forEach((ev) => dropzone.addEventListener(ev, () => dropzone.classList.remove('is-over')));
dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  const file = e.dataTransfer.files && e.dataTransfer.files[0];
  if (file) loadFile(file);
  else notice('No file was selected.', 'error');
});
el('file-input').addEventListener('change', (e) => {
  const file = e.target.files && e.target.files[0];
  if (file) loadFile(file);
  else notice('No file was selected.', 'error');
  e.target.value = '';
});
$$('.sample').forEach((b) => b.addEventListener('click', () => loadSample(b.dataset.src, b.textContent.trim())));

async function loadFile(file) {
  if (!file) return notice('No file was selected.', 'error');
  if (!CONFIG.supportedImageTypes.includes(file.type)) {
    return notice('Unsupported format. Choose a JPEG, PNG, or WebP image.', 'error');
  }
  if (file.size > CONFIG.maxImageBytes) {
    return notice(`That image is too large. The maximum file size is ${formatMegabytes(CONFIG.maxImageBytes)}.`, 'error');
  }
  try {
    const img = await IU.fileToImage(file);
    adoptImage(img, { name: file.name, size: file.size });
  } catch (err) {
    notice(err.message, 'error');
  }
}

async function loadSample(src, label) {
  try {
    const img = await IU.loadImage(src);
    adoptImage(img, { name: src.split('/').pop(), size: 0 });
  } catch {
    notice(`Sample “${label}” is missing from /public/sample-docs/.`, 'error');
  }
}

function adoptImage(img, file) {
  const width = img.naturalWidth || img.width;
  const height = img.naturalHeight || img.height;
  if (!width || !height) {
    notice('The selected image has invalid dimensions.', 'error');
    return false;
  }
  if (width * height > CONFIG.maxImagePixels) {
    notice(
      `That image is too large to process safely (${width}×${height}px). `
      + `The limit is ${formatMegapixels(CONFIG.maxImagePixels)}.`,
      'error',
    );
    return false;
  }

  stopCamera();
  state.original = IU.toCanvas(img);
  state.base = IU.toCanvas(img);
  state.rotation = 0;
  state.skew = 0;
  state.cropRect = null;
  setCropMode(false);
  state.file = file;
  state.ocr = null;
  state.ai = null;
  el('ai').hidden = true;
  el('ocr-text').value = '';
  syncTextState();

  const tone = IU.analyseTone(state.base);
  el('rng-thresh').value = tone.suggestedThreshold;
  el('out-thresh').textContent = tone.suggestedThreshold;

  el('stage').hidden = false;
  el('tools').hidden = false;
  el('run').hidden = false;
  render();
  notice(`Loaded ${file.name}. Straighten and clean up, then extract the text.`, 'info');
  return true;
}

/* ── camera ───────────────────────────────────────────────────────────── */

let stream = null;

el('btn-camera').addEventListener('click', async () => {
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error('Camera capture is not supported by this browser.');
    }
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 } }, audio: false,
    });
    const video = el('video');
    video.srcObject = stream;
    video.hidden = false;
    await video.play();
    el('stage').hidden = false;
    el('preview').hidden = true;
    el('overlay').hidden = true;
    el('btn-shoot').hidden = false;
    el('btn-camera-stop').hidden = false;
    el('btn-camera').hidden = true;
    el('stage-caption').textContent = 'Live camera — frame the page and take the photo.';
  } catch {
    stopCamera();
    notice('The camera is unavailable, permission was refused, or the page is not running in a secure context.', 'error');
  }
});

el('btn-shoot').addEventListener('click', () => {
  const video = el('video');
  if (!video.videoWidth || !video.videoHeight) {
    notice('The camera is not ready yet. Wait a moment and try again.', 'error');
    return;
  }
  const c = IU.makeCanvas(video.videoWidth, video.videoHeight);
  c.getContext('2d').drawImage(video, 0, 0);
  const shot = new Image();
  shot.onload = () => adoptImage(shot, { name: `camera-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.jpg`, size: 0 });
  shot.src = c.toDataURL('image/jpeg', 0.95);
});

el('btn-camera-stop').addEventListener('click', stopCamera);

function stopCamera() {
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
  const video = el('video');
  video.srcObject = null;
  video.hidden = true;
  el('preview').hidden = false;
  el('overlay').hidden = false;
  el('btn-shoot').hidden = true;
  el('btn-camera-stop').hidden = true;
  el('btn-camera').hidden = false;
}

/* ── preview pipeline ─────────────────────────────────────────────────── */

function render() {
  if (!state.base) return;
  state.geom = state.skew
    ? IU.rotateFree(IU.rotate(state.base, state.rotation), state.skew)
    : IU.rotate(state.base, state.rotation);

  const f = state.filters;
  state.processed = IU.preprocess(state.geom, {
    grayscale: f.grayscale || f.threshold !== null,
    threshold: f.threshold,
    invert: f.invert,
    contrast: f.threshold !== null ? 1 : (f.grayscale ? 1.12 : 1),
  });

  drawPreview();
  const p = state.processed;
  el('stage-caption').textContent =
    `${state.file.name} · ${p.width}×${p.height}px${f.threshold !== null ? ` · threshold ${f.threshold}` : f.grayscale ? ' · grayscale' : ''}`;
}

function drawPreview() {
  const canvas = el('preview');
  const plate = canvas.parentElement;
  const maxW = plate.clientWidth || 520;
  const box = IU.fitContain(state.processed.width, state.processed.height, maxW, 480);
  const dpr = Math.min(window.devicePixelRatio || 1, 2);

  for (const c of [canvas, el('overlay')]) {
    c.style.width = `${box.w}px`;
    c.style.height = `${box.h}px`;
    c.width = Math.round(box.w * dpr);
    c.height = Math.round(box.h * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, box.w, box.h);
  ctx.drawImage(state.processed, 0, 0, box.w, box.h);

  state.display = { w: box.w, h: box.h, scale: state.processed.width / box.w, dpr };
  drawOverlay();
}

function drawOverlay() {
  const c = el('overlay');
  const { w, h, dpr } = state.display;
  const ctx = c.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (!state.cropMode || !state.cropRect) return;
  const r = state.cropRect;
  ctx.fillStyle = 'rgba(32,30,29,0.55)';
  ctx.fillRect(0, 0, w, h);
  ctx.clearRect(r.x, r.y, r.w, r.h);
  ctx.strokeStyle = '#0088b0';
  ctx.lineWidth = 1.5;
  ctx.strokeRect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1);
}

window.addEventListener('resize', () => { if (state.processed) drawPreview(); });

/* ── tools ────────────────────────────────────────────────────────────── */

el('btn-rot-l').addEventListener('click', () => { state.rotation = (state.rotation + 270) % 360; state.cropRect = null; render(); });
el('btn-rot-r').addEventListener('click', () => { state.rotation = (state.rotation + 90) % 360; state.cropRect = null; render(); });

el('rng-skew').addEventListener('input', (e) => {
  state.skew = Number(e.target.value);
  el('out-skew').textContent = `${state.skew}°`;
  state.cropRect = null;
  render();
});

el('chk-gray').addEventListener('change', (e) => { state.filters.grayscale = e.target.checked; render(); });
el('chk-invert').addEventListener('change', (e) => { state.filters.invert = e.target.checked; render(); });
el('chk-thresh').addEventListener('change', (e) => {
  el('rng-thresh').disabled = !e.target.checked;
  state.filters.threshold = e.target.checked ? Number(el('rng-thresh').value) : null;
  render();
});
el('rng-thresh').addEventListener('input', (e) => {
  el('out-thresh').textContent = e.target.value;
  if (el('chk-thresh').checked) { state.filters.threshold = Number(e.target.value); render(); }
});

el('btn-reset').addEventListener('click', () => {
  if (!state.original) return;
  state.base = IU.toCanvas(state.original);
  state.rotation = 0;
  state.skew = 0;
  state.cropRect = null;
  el('rng-skew').value = 0;
  el('out-skew').textContent = '0°';
  setCropMode(false);
  render();
});

/* crop selection on the overlay */

function setCropMode(on) {
  state.cropMode = on;
  el('btn-crop').setAttribute('aria-pressed', String(on));
  el('btn-crop').classList.toggle('is-on', on);
  el('overlay').classList.toggle('is-cropping', on);
  el('btn-crop-apply').disabled = !(on && state.cropRect);
  if (!on) state.cropRect = null;
  drawOverlay();
}

el('btn-crop').addEventListener('click', () => setCropMode(!state.cropMode));

el('btn-crop-apply').addEventListener('click', () => {
  if (!state.cropRect) return;
  const s = state.display.scale;
  const r = state.cropRect;
  state.base = IU.crop(state.geom, { x: r.x * s, y: r.y * s, w: r.w * s, h: r.h * s });
  state.rotation = 0;
  state.skew = 0;
  el('rng-skew').value = 0;
  el('out-skew').textContent = '0°';
  setCropMode(false);
  render();
  notice('Cropped. “Reset image” brings the original back.', 'info');
});

(function bindCropDrag() {
  const overlay = el('overlay');
  let start = null;
  const pos = (e) => {
    const b = overlay.getBoundingClientRect();
    return {
      x: IU.clamp(e.clientX - b.left, 0, b.width),
      y: IU.clamp(e.clientY - b.top, 0, b.height),
    };
  };
  overlay.addEventListener('pointerdown', (e) => {
    if (!state.cropMode) return;
    overlay.setPointerCapture(e.pointerId);
    start = pos(e);
    state.cropRect = { x: start.x, y: start.y, w: 0, h: 0 };
  });
  overlay.addEventListener('pointermove', (e) => {
    if (!start) return;
    const p = pos(e);
    state.cropRect = {
      x: Math.min(start.x, p.x), y: Math.min(start.y, p.y),
      w: Math.abs(p.x - start.x), h: Math.abs(p.y - start.y),
    };
    drawOverlay();
  });
  overlay.addEventListener('pointerup', () => {
    start = null;
    const r = state.cropRect;
    const ok = r && r.w > 12 && r.h > 12;
    if (!ok) state.cropRect = null;
    el('btn-crop-apply').disabled = !ok;
    drawOverlay();
  });
})();

/* ── OCR ──────────────────────────────────────────────────────────────── */

el('sel-lang').innerHTML = LANGUAGES.map((l) => `<option value="${l.code}">${l.label}</option>`).join('');

el('btn-ocr').addEventListener('click', async () => {
  if (!state.processed) {
    notice('Choose an image before starting OCR.', 'error');
    return;
  }
  const lang = el('sel-lang').value;
  const btn = el('btn-ocr');
  btn.disabled = true;
  showProgress(0, 'Loading the OCR engine…');
  try {
    const result = await recognize(state.processed, {
      lang,
      onProgress: ({ status, progress }) => showProgress(progress, status),
    });
    state.ocr = result;
    el('ocr-text').value = result.text;
    syncTextState();
    if (!result.text) notice('No text was recognised. Try cropping tighter, or turn on grayscale + threshold.', 'error');
    else notice(`Recognised ${result.text.split(/\s+/).filter(Boolean).length} words${result.confidence ? ` at ${result.confidence}% confidence` : ''}.`, 'ok');
  } catch (error) {
    notice(`OCR failed: ${error.message || 'Unknown OCR error.'}`, 'error');
  } finally {
    btn.disabled = false;
    hideProgress();
  }
});

function showProgress(p, label) {
  el('progress').hidden = false;
  el('progress-bar').style.width = `${Math.round(IU.clamp(p, 0, 1) * 100)}%`;
  el('progress-label').textContent = `${label}${p ? ` — ${Math.round(p * 100)}%` : ''}`;
}
function hideProgress() {
  el('progress').hidden = true;
  el('progress-bar').style.width = '0%';
}

/* ── text area ────────────────────────────────────────────────────────── */

el('ocr-text').addEventListener('input', syncTextState);

function syncTextState() {
  const text = el('ocr-text').value.trim();
  const words = text ? text.split(/\s+/).length : 0;
  el('text-meta').textContent = text
    ? `${words} words · ${text.length} characters${state.ocr && state.ocr.confidence ? ` · OCR ${state.ocr.confidence}%` : ''}`
    : 'no text yet';
  el('btn-analyze').disabled = !text;
  el('btn-copy').disabled = !text;
  el('btn-save').disabled = !text;
}

el('btn-copy').addEventListener('click', async () => {
  await navigator.clipboard.writeText(el('ocr-text').value);
  notice('Text copied to the clipboard.', 'ok');
});

/* ── AI analysis ──────────────────────────────────────────────────────── */

el('btn-analyze').addEventListener('click', async () => {
  const text = el('ocr-text').value.trim();
  if (!text) {
    notice('Extract or enter text before requesting AI analysis.', 'error');
    return;
  }
  const payload = { filename: state.file.name || 'untitled', text, language: el('sel-lang').value };
  const btn = el('btn-analyze');
  btn.disabled = true;
  btn.textContent = 'Working…';
  state.ai = null;
  el('ai').hidden = true;
  try {
    const result = await api.analyze(payload);
    state.backend = 'up';
    paintConnection();
    state.ai = result;
    paintAI(result);
  } catch (error) {
    setBackendFromApiError(error);
    paintConnection();
    notice(`AI analysis is unavailable: ${error.message} Your image and OCR text are unchanged.`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Classify & summarise';
    syncTextState();
  }
});

function paintAI(r) {
  el('ai').hidden = false;
  const provider = typeof r.provider === 'string' ? r.provider.trim() : '';
  el('ai-source').textContent = provider ? `AI provider: ${provider}` : 'AI provider: not reported';
  el('ai-source').className = 'tag tag-accent-2';
  el('ai-class').textContent = r.classification || 'unclassified';
  el('ai-conf').textContent = typeof r.confidence === 'number'
    ? `confidence ${Math.round(r.confidence * 100)}%` : '';
  el('ai-summary').textContent = r.summary || '—';

  const fields = r.fields || [];
  el('ai-fields-wrap').hidden = fields.length === 0;
  el('ai-fields').innerHTML = fields
    .map((f) => `<div class="field-pair"><dt>${escapeHtml(f.label)}</dt><dd>${escapeHtml(f.value)}</dd></div>`)
    .join('');

  const tags = r.tags || [];
  el('ai-tags-wrap').hidden = tags.length === 0;
  el('ai-tags').innerHTML = tags.map((t) => `<span class="tag tag-accent">${escapeHtml(t)}</span>`).join('');
}

/* ── saving ───────────────────────────────────────────────────────────── */

function persistScan(scan) {
  try {
    return { scans: store.add(scan), savedWithoutThumbnail: false };
  } catch (error) {
    if (!(error instanceof StorageError && error.quotaExceeded && scan.thumbnail)) throw error;
    return {
      scans: store.add({ ...scan, thumbnail: null }),
      savedWithoutThumbnail: true,
    };
  }
}

function reportStorageError(error, action) {
  if (error instanceof StorageError && error.quotaExceeded) {
    notice('Browser storage is full. Export or clear the archive, then try again.', 'error');
    return;
  }
  notice(`Could not ${action} because browser storage is unavailable. Check its permissions and try again.`, 'error');
}

el('btn-save').addEventListener('click', () => {
  const text = el('ocr-text').value.trim();
  if (!text) return;
  const ai = state.ai || {};
  const scan = {
    id: newId(),
    filename: state.file.name || 'untitled',
    scanned_at: new Date().toISOString(),
    text,
    snippet: snippet(text, 160),
    classification: ai.classification || 'unclassified',
    confidence: typeof ai.confidence === 'number' ? ai.confidence : null,
    summary: ai.summary || '',
    tags: ai.tags || [],
    fields: ai.fields || [],
    ocr: state.ocr ? {
      engine: state.ocr.engine, lang: state.ocr.lang, confidence: state.ocr.confidence,
    } : null,
    thumbnail: state.geom ? IU.makeThumbnail(state.geom, 320) : null,
  };

  try {
    const result = persistScan(scan);
    state.scans = result.scans;
    renderResults();
    notice(
      result.savedWithoutThumbnail
        ? 'Saved without image preview.'
        : 'Saved to the local results archive.',
      'ok',
    );
  } catch (error) {
    reportStorageError(error, 'save this result');
  }
});

/* ── results table ────────────────────────────────────────────────────── */

el('q').addEventListener('input', renderResults);
el('filter-class').addEventListener('change', renderResults);
$$('.sortable').forEach((th) => th.addEventListener('click', () => {
  const key = th.dataset.key;
  state.sort = state.sort.key === key ? { key, dir: -state.sort.dir } : { key, dir: key === 'scanned_at' ? -1 : 1 };
  renderResults();
}));

el('btn-export').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify(state.scans, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'scans.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
});

el('btn-clear').addEventListener('click', () => {
  if (!state.scans.length) return;
  if (!confirm('Remove every saved scan from this browser?')) return;
  try {
    const scans = store.clear();
    state.scans = scans;
    renderResults();
  } catch (error) {
    reportStorageError(error, 'clear the archive');
  }
});

function renderResults() {
  const query = el('q').value;
  const classification = el('filter-class').value;

  const options = ['all', ...classifications(state.scans)];
  if (el('filter-class').options.length !== options.length) {
    el('filter-class').innerHTML = options
      .map((c) => `<option value="${escapeHtml(c)}">${c === 'all' ? 'All' : escapeHtml(c)}</option>`)
      .join('');
    el('filter-class').value = options.includes(classification) ? classification : 'all';
  }

  const rows = view(state.scans, { query, classification: el('filter-class').value, sortKey: state.sort.key, sortDir: state.sort.dir });

  el('tab-count').textContent = state.scans.length;
  el('results-count').textContent = `${rows.length} of ${state.scans.length} document${state.scans.length === 1 ? '' : 's'}`;
  el('results-empty').hidden = state.scans.length > 0;

  $$('.sortable').forEach((th) => {
    th.dataset.dir = th.dataset.key === state.sort.key ? (state.sort.dir === 1 ? 'asc' : 'desc') : '';
  });

  el('results-body').innerHTML = rows.map((s) => `
    <tr data-id="${s.id}">
      <td class="cell-file">
        <button class="link-btn" data-act="open" data-id="${s.id}">${escapeHtml(s.filename)}</button>
        ${s.ocr ? `<span class="cell-sub text-muted">${escapeHtml(s.ocr.engine)} · ${escapeHtml(s.ocr.lang)}</span>` : ''}
      </td>
      <td class="cell-date">${formatDate(s.scanned_at)}</td>
      <td class="cell-snippet">${escapeHtml(snippet(s.text, 110))}</td>
      <td><span class="tag ${s.classification && s.classification !== 'unclassified' ? 'tag-accent' : 'tag-neutral'}">${escapeHtml(s.classification || 'unclassified')}</span></td>
      <td class="cell-summary">${escapeHtml(snippet(s.summary, 130)) || '<span class="text-muted">—</span>'}</td>
      <td class="cell-actions">
        <button class="btn btn-ghost" data-act="open" data-id="${s.id}">View</button>
        <button class="btn btn-ghost btn-danger" data-act="del" data-id="${s.id}">Delete</button>
      </td>
    </tr>`).join('');
}

el('results-body').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const scan = state.scans.find((s) => s.id === btn.dataset.id);
  if (!scan) return;
  if (btn.dataset.act === 'open') return openDetail(scan);
  try {
    const scans = store.remove(scan.id);
    state.scans = scans;
    renderResults();
  } catch (error) {
    reportStorageError(error, 'delete this result');
  }
});

/* ── detail dialog ────────────────────────────────────────────────────── */

function openDetail(s) {
  el('detail-kicker').textContent = `${s.classification || 'unclassified'} · ${formatDate(s.scanned_at)}`;
  el('detail-title').textContent = s.filename;
  el('detail-summary').textContent = s.summary || 'No summary was produced for this scan.';
  el('detail-text').textContent = s.text || '';
  el('detail-fields').innerHTML = (s.fields || [])
    .map((f) => `<div class="field-pair"><dt>${escapeHtml(f.label)}</dt><dd>${escapeHtml(f.value)}</dd></div>`)
    .join('');
  const fig = el('detail-figure');
  if (s.thumbnail) { el('detail-img').src = s.thumbnail; fig.hidden = false; } else { fig.hidden = true; }
  el('detail').hidden = false;
}
el('detail-close').addEventListener('click', () => { el('detail').hidden = true; });
el('detail').addEventListener('click', (e) => { if (e.target === el('detail')) el('detail').hidden = true; });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') el('detail').hidden = true; });

/* ── misc ─────────────────────────────────────────────────────────────── */

let noticeTimer = null;
function notice(message, tone = 'info') {
  const n = el('notice');
  n.hidden = false;
  n.textContent = message;
  n.dataset.tone = tone;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { n.hidden = true; }, 9000);
}

function escapeHtml(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function formatMegabytes(bytes) {
  return `${Math.round((bytes / (1024 * 1024)) * 10) / 10} MB`;
}

function formatMegapixels(pixels) {
  return `${Math.round((pixels / 1_000_000) * 10) / 10} megapixels`;
}

el('dateline-date').textContent = new Date().toLocaleDateString(undefined, {
  weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
});

window.addEventListener('beforeunload', () => { shutdown(); stopCamera(); });

state.scans = store.all();
renderResults();
syncTextState();
paintConnection();
checkBackend();
