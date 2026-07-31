/**
 * Visual Scan frontend orchestration.
 *
 * Canvas work lives in imageUtils.js, browser OCR in ocr.js, every backend
 * request in api.js, pure archive rules in archive.js, and pre-Step-7 browser
 * records behind the explicit legacy adapter in store.js.
 */

import * as IU from './utils/imageUtils.js';
import { CONFIG } from './config.js';
import {
  availableLanguagesForProfile,
  isOcrCombinationAvailable,
  LANGUAGES,
  loadOcrAvailability,
  OCR_MODEL_NOT_INSTALLED_MESSAGE,
  OcrModelError,
  PROFILES,
  recognize,
  releaseWorkerForSelection,
  shutdown,
} from './utils/ocr.js';
import { api, ApiError } from './utils/api.js';
import {
  ArchiveContractError,
  CLASSIFICATIONS,
  analysisFingerprint,
  buildScanPayload,
  collectArchiveForExport,
  formatDate,
  listQuery,
  mapBrowserOcr,
  mapServerImageOcr,
  mapServerPdfOcr,
  nextBackendState,
  nextPageOffset,
  offsetAfterDelete,
  parsePdfThreshold,
  previousPageOffset,
  snippet,
  validPageOffset,
} from './utils/archive.js';
import { legacyStore, StorageError } from './utils/store.js';
import {
  AUTH_REVALIDATION_MODE,
  AuthContractError,
  EDITOR_PROVENANCE,
  anonymousAfterUnauthorized,
  authRequestSnapshot,
  identityChanged,
  isAuthRequestCurrent,
  isAuthRequestSessionCurrent,
  isAuthRevisionCurrent,
  isServerDerivedEditor,
  normalizeAuthSession,
  normalizeUsername,
  planAuthRevalidation,
  planAuthVerificationFailure,
  provenanceForOcrSource,
  serverFeaturesAvailable,
  validatePassword,
} from './utils/auth.js';
import { createAuthSync } from './utils/authSync.js';

const el = (id) => document.getElementById(id);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  original: null,
  base: null,
  rotation: 0,
  skew: 0,
  filters: { grayscale: false, threshold: null, invert: false },
  geom: null,
  processed: null,
  display: { w: 0, h: 0, scale: 1, dpr: 1 },
  cropMode: false,
  cropRect: null,
  source: { kind: null, file: null, name: '', size: 0, type: '' },
  intakeRevision: 0,
  sourceRevision: 0,
  ocrEngine: 'browser',
  ocr: null,
  ocrBusy: false,
  ocrRevision: 0,
  ocrController: null,
  ocrManifest: Object.fromEntries(PROFILES.map((profile) => [profile.id, []])),
  ai: null,
  aiBusy: false,
  aiRevision: 0,
  aiController: null,
  saveBusy: false,
  saveRevision: 0,
  saveController: null,
  editorProvenance: EDITOR_PROVENANCE.MANUAL,
  backend: 'unknown',
  health: { status: 'unknown', aiAvailable: null, provider: null },
  archive: {
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
    query: '',
    classification: 'all',
    sort: 'scanned_at',
    order: 'desc',
    busy: false,
    error: '',
    revision: 0,
    controller: null,
    mutationRevision: 0,
    mutationBusy: false,
    exportBusy: false,
    exportRevision: 0,
    exportController: null,
    detailCache: new Map(),
  },
  detail: { id: null, revision: 0, controller: null },
  auth: {
    status: 'checking',
    user: null,
    csrfToken: null,
    busy: false,
    revision: 0,
    controller: null,
    verificationRevision: 0,
    verificationController: null,
    verificationUnavailable: false,
    legacy: {
      count: 0,
      claimable: false,
      busy: false,
      revision: 0,
      controller: null,
    },
  },
  savedEditor: null,
};

const NO_IDENTITY_HINT = Symbol('no-identity-hint');
let authSync = null;
let authRevalidationTimer = null;
let pendingIdentityHint = NO_IDENTITY_HINT;

/* ── tabs and connection ─────────────────────────────────────────────── */

function showPanel(name) {
  $$('.tab').forEach((tab) => {
    tab.setAttribute('aria-selected', String(tab.dataset.panel === name));
  });
  el('panel-scan').hidden = name !== 'scan';
  el('panel-results').hidden = name !== 'results';
  window.scrollTo({ top: 0 });
  if (name === 'results') loadArchive({ clearCache: true });
}

$$('.tab').forEach((tab) => {
  tab.addEventListener('click', () => showPanel(tab.dataset.panel));
});
document.addEventListener('click', (event) => {
  const target = event.target.closest('[data-goto]');
  if (target) showPanel(target.dataset.goto);
});

function paintConnection() {
  el('conn-dot').dataset.state = state.backend;
  el('conn-label').textContent = state.backend === 'up'
    ? 'Backend: reachable'
    : state.backend === 'down'
      ? 'Backend: unavailable'
      : 'Backend: checking…';
}

function applyApiReachability(error) {
  state.backend = nextBackendState(state.backend, error);
  paintConnection();
}

function markBackendReachable() {
  state.backend = 'up';
  paintConnection();
}

function authenticated() {
  return serverFeaturesAvailable(state.auth);
}

function currentAuthUserId() {
  return state.auth.status === 'authenticated' ? state.auth.user?.id || null : null;
}

function publishAuthIdentity() {
  authSync?.publish(currentAuthUserId());
}

function scheduleAuthRevalidation(identityHint = NO_IDENTITY_HINT) {
  if (identityHint !== NO_IDENTITY_HINT) pendingIdentityHint = identityHint;
  if (authRevalidationTimer !== null) return;
  authRevalidationTimer = setTimeout(runScheduledAuthRevalidation, 0);
}

async function runScheduledAuthRevalidation() {
  authRevalidationTimer = null;
  if (state.auth.busy) {
    authRevalidationTimer = setTimeout(runScheduledAuthRevalidation, 100);
    return;
  }
  const identityHint = pendingIdentityHint;
  pendingIdentityHint = NO_IDENTITY_HINT;
  const hasIdentityHint = identityHint !== NO_IDENTITY_HINT;
  const plan = planAuthRevalidation(state.auth, {
    hasIdentityHint,
    identityHint: hasIdentityHint ? identityHint : null,
  });
  if (plan.mode === AUTH_REVALIDATION_MODE.SOFT) {
    await verifyAuthenticatedSession();
  } else {
    await restoreAuthSession({
      revalidation: true,
      identityHint,
    });
  }
}

function beginProtectedRequest() {
  return authRequestSnapshot(state.auth);
}

function protectedRequestIsCurrent(requestAuth) {
  return isAuthRequestCurrent(state.auth, requestAuth);
}

function cancelAuthVerification() {
  state.auth.verificationRevision += 1;
  state.auth.verificationController?.abort();
  state.auth.verificationController = null;
}

function cancelSaveRequest() {
  state.saveRevision += 1;
  state.saveController?.abort();
  state.saveController = null;
  state.saveBusy = false;
  el('btn-save').textContent = 'Save to server archive';
}

function cancelArchiveExport() {
  state.archive.exportRevision += 1;
  state.archive.exportController?.abort();
  state.archive.exportController = null;
  state.archive.exportBusy = false;
}

function resetServerLegacyState() {
  state.auth.legacy.controller?.abort();
  state.auth.legacy = {
    count: 0,
    claimable: false,
    busy: false,
    revision: 0,
    controller: null,
  };
}

function cancelAuthBoundRequests() {
  if (state.source.kind === 'pdf' || state.ocrEngine === 'server') {
    cancelOcrRequest();
    state.ocrBusy = false;
    hideProgress();
    syncOcrControls();
  }
  state.aiRevision += 1;
  state.aiController?.abort();
  state.aiController = null;
  state.aiBusy = false;
  el('btn-analyze').textContent = 'Classify & summarise';
  cancelSaveRequest();
  cancelArchiveList();
  cancelArchiveExport();
  state.archive.mutationRevision += 1;
  state.archive.mutationBusy = false;
  closeDetail();
  state.auth.legacy.revision += 1;
  state.auth.legacy.controller?.abort();
  state.auth.legacy.controller = null;
  state.auth.legacy.busy = false;
}

function resetArchiveState() {
  cancelArchiveList();
  cancelArchiveExport();
  state.archive.mutationRevision += 1;
  state.archive.items = [];
  state.archive.total = 0;
  state.archive.offset = 0;
  state.archive.error = '';
  state.archive.mutationBusy = false;
  state.archive.detailCache.clear();
  closeDetail();
}

function clearServerDerivedState() {
  cancelAuthBoundRequests();
  invalidateAnalysis({ clear: true });
  state.savedEditor = null;
  if (isServerDerivedEditor(state.editorProvenance)) {
    state.ocr = null;
    el('ocr-text').value = '';
    state.editorProvenance = EDITOR_PROVENANCE.MANUAL;
  }
  if (state.source.kind !== 'pdf' && state.ocrEngine === 'server') {
    state.ocrEngine = 'browser';
    el('sel-engine').value = 'browser';
  }
  resetArchiveState();
  resetServerLegacyState();
  paintServerLegacy();
}

function paintAuthState() {
  const checking = state.auth.status === 'checking';
  const signedIn = authenticated();
  el('auth-label').textContent = checking
    ? 'Session: checking…'
    : signedIn
      ? [
        `Signed in: ${state.auth.user.username}`,
        state.auth.verificationUnavailable ? 'verification unavailable' : '',
      ].filter(Boolean).join(' · ')
      : 'Session: anonymous';
  el('btn-sign-in').hidden = checking || signedIn;
  el('btn-register').hidden = checking || signedIn;
  el('btn-logout').hidden = !signedIn;
  el('btn-logout').disabled = state.auth.busy;
  el('archive-sign-in').hidden = signedIn || checking;
  syncSourceControls();
  syncTextState();
  renderArchive();
  paintAiAvailability();
}

function applyAuthenticatedSession(session) {
  const previousUser = state.auth.user;
  if (identityChanged(previousUser, session.user)) clearServerDerivedState();
  state.auth.status = session.status;
  state.auth.user = session.user;
  state.auth.csrfToken = session.csrfToken;
  state.auth.busy = false;
  state.auth.verificationUnavailable = false;
  api.setCsrfToken(session.csrfToken);
  paintAuthState();
}

function becomeAnonymous(message = '') {
  cancelAuthVerification();
  api.clearCsrfToken();
  state.auth = {
    ...anonymousAfterUnauthorized(state.auth),
    controller: null,
    legacy: state.auth.legacy,
  };
  clearServerDerivedState();
  paintAuthState();
  if (message) notice(message, 'error');
}

function handleProtectedApiError(error, requestAuth) {
  if (requestAuth && !protectedRequestIsCurrent(requestAuth)) return true;
  applyApiReachability(error);
  if (error instanceof ApiError && error.status === 401) {
    if (requestAuth && !isAuthRequestSessionCurrent(state.auth, requestAuth)) {
      notice(
        'A request from the previous session was rejected. Your current session is unchanged.',
        'info',
      );
      return true;
    }
    becomeAnonymous('Your session ended. Sign in again to use server features.');
    publishAuthIdentity();
    return true;
  }
  return false;
}

function paintAiAvailability() {
  const status = el('ai-availability');
  if (!authenticated()) {
    status.textContent = 'Sign in to use AI analysis. Browser OCR remains available.';
  } else if (state.health.aiAvailable === false) {
    status.textContent = 'AI analysis is not configured on the backend.';
  } else if (state.health.aiAvailable === true) {
    status.textContent = state.health.provider
      ? `AI analysis available · ${state.health.provider}`
      : 'AI analysis is available.';
  } else {
    status.textContent = 'AI availability is unknown; you can still try the request.';
  }
  syncTextState();
}

async function checkBackend() {
  state.backend = 'unknown';
  paintConnection();
  try {
    const health = await api.health();
    markBackendReachable();
    state.health.status = typeof health?.status === 'string' ? health.status : 'unknown';
    state.health.aiAvailable = typeof health?.ai_available === 'boolean'
      ? health.ai_available
      : null;
    state.health.provider = typeof health?.provider === 'string' ? health.provider : null;
  } catch (error) {
    applyApiReachability(error);
    state.health.status = 'unknown';
    state.health.aiAvailable = null;
    state.health.provider = null;
  }
  paintAiAvailability();
}

el('conn-test').addEventListener('click', checkBackend);

/* ── source lifecycle and intake ─────────────────────────────────────── */

function cancelOcrRequest() {
  state.ocrRevision += 1;
  state.ocrController?.abort();
  state.ocrController = null;
}

function invalidateAnalysis({ clear = true } = {}) {
  state.aiRevision += 1;
  state.aiController?.abort();
  state.aiController = null;
  state.aiBusy = false;
  el('btn-analyze').textContent = 'Classify & summarise';
  if (clear) {
    state.ai = null;
    el('ai').hidden = true;
  } else {
    paintAiFreshness();
  }
}

function beginNewSource() {
  state.sourceRevision += 1;
  cancelOcrRequest();
  state.ocrBusy = false;
  state.ocr = null;
  invalidateAnalysis({ clear: true });
  el('ocr-text').value = '';
  state.editorProvenance = EDITOR_PROVENANCE.MANUAL;
  el('pdf-password').value = '';
  hideProgress();
  syncTextState();
}

function invalidateProcessedSource() {
  state.sourceRevision += 1;
  cancelOcrRequest();
  state.ocrBusy = false;
  state.ocr = null;
  invalidateAnalysis();
  hideProgress();
  syncTextState();
}

const dropzone = el('dropzone');
dropzone.addEventListener('click', () => el('file-input').click());
dropzone.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    el('file-input').click();
  }
});
['dragenter', 'dragover'].forEach((name) => dropzone.addEventListener(name, (event) => {
  event.preventDefault();
  dropzone.classList.add('is-over');
}));
['dragleave', 'drop'].forEach((name) => dropzone.addEventListener(name, () => {
  dropzone.classList.remove('is-over');
}));
dropzone.addEventListener('drop', (event) => {
  event.preventDefault();
  const file = event.dataTransfer.files?.[0];
  if (file) loadFile(file);
  else notice('No file was selected.', 'error');
});
el('file-input').addEventListener('change', (event) => {
  const file = event.target.files?.[0];
  if (file) loadFile(file);
  else notice('No file was selected.', 'error');
  event.target.value = '';
});
$$('.sample').forEach((button) => {
  button.addEventListener('click', () => loadSample(button.dataset.src, button.textContent.trim()));
});

async function loadFile(file) {
  if (!file) {
    notice('No file was selected.', 'error');
    return;
  }
  const intakeRevision = ++state.intakeRevision;
  if (file.type === CONFIG.supportedPdfType) {
    if (file.size > CONFIG.maxPdfBytes) {
      notice(`That PDF is too large. The maximum file size is ${formatMegabytes(CONFIG.maxPdfBytes)}.`, 'error');
      return;
    }
    if (intakeRevision === state.intakeRevision) adoptPdf(file);
    return;
  }
  if (!CONFIG.supportedImageTypes.includes(file.type)) {
    notice('Unsupported format. Choose a JPEG, PNG, WebP, or PDF file.', 'error');
    return;
  }
  if (file.size > CONFIG.maxImageBytes) {
    notice(`That image is too large. The maximum file size is ${formatMegabytes(CONFIG.maxImageBytes)}.`, 'error');
    return;
  }
  try {
    const image = await IU.fileToImage(file);
    if (intakeRevision !== state.intakeRevision) return;
    adoptImage(image, {
      file,
      name: file.name,
      size: file.size,
      type: file.type,
    });
  } catch (error) {
    notice(error.message || 'Could not decode this image.', 'error');
  }
}

async function loadSample(src, label) {
  const intakeRevision = ++state.intakeRevision;
  try {
    const image = await IU.loadImage(src);
    if (intakeRevision !== state.intakeRevision) return;
    adoptImage(image, {
      file: null,
      name: src.split('/').pop(),
      size: 0,
      type: 'image/jpeg',
    });
  } catch {
    notice(`Sample “${label}” is missing from /public/sample-docs/.`, 'error');
  }
}

function adoptImage(image, source) {
  const width = image.naturalWidth || image.width;
  const height = image.naturalHeight || image.height;
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
  beginNewSource();
  state.source = { kind: 'image', ...source };
  state.original = IU.toCanvas(image);
  state.base = IU.toCanvas(image);
  state.rotation = 0;
  state.skew = 0;
  state.cropRect = null;
  setCropMode(false);
  resetFilterControls();

  const tone = IU.analyseTone(state.base);
  el('rng-thresh').value = tone.suggestedThreshold;
  el('out-thresh').textContent = tone.suggestedThreshold;

  el('stage').hidden = false;
  el('tools').hidden = false;
  el('pdf-card').hidden = true;
  el('run').hidden = false;
  render();
  syncSourceControls();
  notice(`Loaded ${source.name}. Straighten and clean up, then extract the text.`, 'info');
  return true;
}

function adoptPdf(file) {
  stopCamera();
  beginNewSource();
  state.source = {
    kind: 'pdf',
    file,
    name: file.name,
    size: file.size,
    type: file.type,
  };
  state.original = null;
  state.base = null;
  state.geom = null;
  state.processed = null;
  state.ocrEngine = 'server';
  el('sel-engine').value = 'server';
  el('stage').hidden = true;
  el('tools').hidden = true;
  el('pdf-card').hidden = false;
  el('pdf-name').textContent = file.name;
  el('pdf-meta').textContent = `${formatMegabytes(file.size)} · original PDF`;
  el('run').hidden = false;
  syncSourceControls();
  notice(`Loaded ${file.name}. PDF OCR runs on the backend and may take several minutes.`, 'info');
}

/* ── camera ──────────────────────────────────────────────────────────── */

let stream = null;

el('btn-camera').addEventListener('click', async () => {
  try {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('Camera capture is not supported by this browser.');
    }
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 } },
      audio: false,
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
  const canvas = IU.makeCanvas(video.videoWidth, video.videoHeight);
  canvas.getContext('2d').drawImage(video, 0, 0);
  const intakeRevision = ++state.intakeRevision;
  const shot = new Image();
  shot.onload = () => {
    if (intakeRevision !== state.intakeRevision) return;
    adoptImage(shot, {
      file: null,
      name: `camera-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.jpg`,
      size: 0,
      type: 'image/jpeg',
    });
  };
  shot.src = canvas.toDataURL('image/jpeg', 0.95);
});

el('btn-camera-stop').addEventListener('click', stopCamera);

function stopCamera() {
  if (stream) {
    stream.getTracks().forEach((track) => track.stop());
    stream = null;
  }
  const video = el('video');
  video.srcObject = null;
  video.hidden = true;
  el('preview').hidden = false;
  el('overlay').hidden = false;
  el('btn-shoot').hidden = true;
  el('btn-camera-stop').hidden = true;
  el('btn-camera').hidden = false;
}

/* ── Canvas preview and image tools ──────────────────────────────────── */

function render() {
  if (!state.base) return;
  state.geom = state.skew
    ? IU.rotateFree(IU.rotate(state.base, state.rotation), state.skew)
    : IU.rotate(state.base, state.rotation);
  state.processed = IU.preprocess(state.geom, {
    grayscale: state.filters.grayscale || state.filters.threshold !== null,
    threshold: state.filters.threshold,
    invert: state.filters.invert,
    contrast: state.filters.threshold !== null ? 1 : (state.filters.grayscale ? 1.12 : 1),
  });
  drawPreview();
  const suffix = state.filters.threshold !== null
    ? ` · threshold ${state.filters.threshold}`
    : state.filters.grayscale
      ? ' · grayscale'
      : '';
  el('stage-caption').textContent =
    `${state.source.name} · ${state.processed.width}×${state.processed.height}px${suffix}`;
}

function drawPreview() {
  const canvas = el('preview');
  const maxWidth = canvas.parentElement.clientWidth || 520;
  const box = IU.fitContain(state.processed.width, state.processed.height, maxWidth, 480);
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  for (const item of [canvas, el('overlay')]) {
    item.style.width = `${box.w}px`;
    item.style.height = `${box.h}px`;
    item.width = Math.round(box.w * dpr);
    item.height = Math.round(box.h * dpr);
  }
  const context = canvas.getContext('2d');
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, box.w, box.h);
  context.drawImage(state.processed, 0, 0, box.w, box.h);
  state.display = {
    w: box.w,
    h: box.h,
    scale: state.processed.width / box.w,
    dpr,
  };
  drawOverlay();
}

function drawOverlay() {
  const canvas = el('overlay');
  const { w, h, dpr } = state.display;
  const context = canvas.getContext('2d');
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, w, h);
  if (!state.cropMode || !state.cropRect) return;
  const rect = state.cropRect;
  context.fillStyle = 'rgba(32,30,29,0.55)';
  context.fillRect(0, 0, w, h);
  context.clearRect(rect.x, rect.y, rect.w, rect.h);
  context.strokeStyle = '#0088b0';
  context.lineWidth = 1.5;
  context.strokeRect(rect.x + 0.5, rect.y + 0.5, rect.w - 1, rect.h - 1);
}

window.addEventListener('resize', () => {
  if (state.processed) drawPreview();
});

function changeProcessedImage(change) {
  if (!state.base) return;
  invalidateProcessedSource();
  change();
  state.cropRect = null;
  render();
  syncOcrControls();
}

el('btn-rot-l').addEventListener('click', () => {
  changeProcessedImage(() => { state.rotation = (state.rotation + 270) % 360; });
});
el('btn-rot-r').addEventListener('click', () => {
  changeProcessedImage(() => { state.rotation = (state.rotation + 90) % 360; });
});
el('rng-skew').addEventListener('input', (event) => {
  changeProcessedImage(() => {
    state.skew = Number(event.target.value);
    el('out-skew').textContent = `${state.skew}°`;
  });
});
el('chk-gray').addEventListener('change', (event) => {
  changeProcessedImage(() => { state.filters.grayscale = event.target.checked; });
});
el('chk-invert').addEventListener('change', (event) => {
  changeProcessedImage(() => { state.filters.invert = event.target.checked; });
});
el('chk-thresh').addEventListener('change', (event) => {
  changeProcessedImage(() => {
    el('rng-thresh').disabled = !event.target.checked;
    state.filters.threshold = event.target.checked ? Number(el('rng-thresh').value) : null;
  });
});
el('rng-thresh').addEventListener('input', (event) => {
  el('out-thresh').textContent = event.target.value;
  if (el('chk-thresh').checked) {
    changeProcessedImage(() => { state.filters.threshold = Number(event.target.value); });
  }
});
el('btn-reset').addEventListener('click', () => {
  if (!state.original) return;
  changeProcessedImage(() => {
    state.base = IU.toCanvas(state.original);
    state.rotation = 0;
    state.skew = 0;
    el('rng-skew').value = 0;
    el('out-skew').textContent = '0°';
    setCropMode(false);
  });
});

function resetFilterControls() {
  state.filters = { grayscale: false, threshold: null, invert: false };
  el('chk-gray').checked = false;
  el('chk-thresh').checked = false;
  el('chk-invert').checked = false;
  el('rng-thresh').disabled = true;
  el('rng-skew').value = 0;
  el('out-skew').textContent = '0°';
}

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
  const scale = state.display.scale;
  const rect = state.cropRect;
  changeProcessedImage(() => {
    state.base = IU.crop(state.geom, {
      x: rect.x * scale,
      y: rect.y * scale,
      w: rect.w * scale,
      h: rect.h * scale,
    });
    state.rotation = 0;
    state.skew = 0;
    el('rng-skew').value = 0;
    el('out-skew').textContent = '0°';
    setCropMode(false);
  });
  notice('Cropped. “Reset image” brings the original back.', 'info');
});

(function bindCropDrag() {
  const overlay = el('overlay');
  let start = null;
  const pointerPosition = (event) => {
    const bounds = overlay.getBoundingClientRect();
    return {
      x: IU.clamp(event.clientX - bounds.left, 0, bounds.width),
      y: IU.clamp(event.clientY - bounds.top, 0, bounds.height),
    };
  };
  overlay.addEventListener('pointerdown', (event) => {
    if (!state.cropMode) return;
    overlay.setPointerCapture(event.pointerId);
    start = pointerPosition(event);
    state.cropRect = { x: start.x, y: start.y, w: 0, h: 0 };
  });
  overlay.addEventListener('pointermove', (event) => {
    if (!start) return;
    const point = pointerPosition(event);
    state.cropRect = {
      x: Math.min(start.x, point.x),
      y: Math.min(start.y, point.y),
      w: Math.abs(point.x - start.x),
      h: Math.abs(point.y - start.y),
    };
    drawOverlay();
  });
  overlay.addEventListener('pointerup', () => {
    start = null;
    const valid = state.cropRect && state.cropRect.w > 12 && state.cropRect.h > 12;
    if (!valid) state.cropRect = null;
    el('btn-crop-apply').disabled = !valid;
    drawOverlay();
  });
}());

/* ── OCR controls and execution ──────────────────────────────────────── */

el('sel-model').innerHTML = PROFILES
  .map((profile) => `<option value="${profile.id}">${profile.label}</option>`)
  .join('');
el('sel-model').value = CONFIG.ocr.defaultProfile;
el('sel-lang').innerHTML = LANGUAGES
  .map((language) => `<option value="${language.code}">${language.label}</option>`)
  .join('');
el('sel-lang').value = 'eng';

function selectedLocalCombinationAvailable() {
  return isOcrCombinationAvailable(
    state.ocrManifest,
    el('sel-model').value,
    el('sel-lang').value,
  );
}

function paintOcrAvailability({ selectFirstLanguage = false } = {}) {
  const browserMode = state.source.kind !== 'pdf' && state.ocrEngine === 'browser';
  for (const option of el('sel-model').options) {
    option.disabled = browserMode && availableLanguagesForProfile(
      state.ocrManifest,
      option.value,
    ).length === 0;
  }

  const profile = el('sel-model').value;
  const available = availableLanguagesForProfile(state.ocrManifest, profile);
  if (
    browserMode
    && selectFirstLanguage
    && available.length
    && !available.includes(el('sel-lang').value)
  ) {
    el('sel-lang').value = available[0];
  }
  for (const option of el('sel-lang').options) {
    option.disabled = browserMode
      ? !isOcrCombinationAvailable(state.ocrManifest, profile, option.value)
      : false;
  }

  const status = el('model-status');
  if (!browserMode && !authenticated()) {
    status.textContent = 'Sign in to use server OCR.';
    status.dataset.state = 'missing';
  } else if (!browserMode) {
    status.textContent = state.source.kind === 'pdf'
      ? 'PDF OCR uses the original file and system Tesseract on the backend.'
      : 'Server OCR uses the processed Canvas image and system Tesseract.';
    status.dataset.state = 'ready';
  } else if (selectedLocalCombinationAvailable()) {
    const profileLabel = PROFILES.find((item) => item.id === profile)?.label || profile;
    const languageLabel = LANGUAGES.find((item) => item.code === el('sel-lang').value)?.label;
    status.textContent = `${profileLabel} · ${languageLabel} · local traineddata`;
    status.dataset.state = 'ready';
  } else {
    status.textContent = OCR_MODEL_NOT_INSTALLED_MESSAGE;
    status.dataset.state = 'missing';
  }
  syncOcrControls();
}

function syncSourceControls() {
  const isPdf = state.source.kind === 'pdf';
  if (isPdf) {
    state.ocrEngine = 'server';
    el('sel-engine').value = 'server';
  }
  el('sel-engine').disabled = isPdf || state.ocrBusy;
  const serverOption = Array.from(el('sel-engine').options)
    .find((option) => option.value === 'server');
  if (serverOption) serverOption.disabled = !authenticated();
  el('sel-model').hidden = false;
  el('sel-model').closest('.field').hidden = !(!isPdf && state.ocrEngine === 'browser');
  el('pdf-options').hidden = !isPdf;
  paintOcrAvailability();
}

function syncOcrControls() {
  const browserMode = state.source.kind !== 'pdf' && state.ocrEngine === 'browser';
  const hasSource = state.source.kind === 'pdf'
    ? Boolean(state.source.file)
    : Boolean(state.processed);
  el('sel-engine').disabled = state.source.kind === 'pdf' || state.ocrBusy;
  el('sel-model').disabled = state.ocrBusy || (
    browserMode && !PROFILES.some(
      (profile) => availableLanguagesForProfile(state.ocrManifest, profile.id).length,
    )
  );
  el('sel-lang').disabled = state.ocrBusy || (
    browserMode && availableLanguagesForProfile(
      state.ocrManifest,
      el('sel-model').value,
    ).length === 0
  );
  el('btn-ocr').disabled = state.ocrBusy
    || !hasSource
    || (!browserMode && !authenticated())
    || (browserMode && !selectedLocalCombinationAvailable());
  el('pdf-preprocessing').disabled = state.ocrBusy;
  el('pdf-threshold').disabled = state.ocrBusy;
  el('pdf-password').disabled = state.ocrBusy;
}

async function loadOcrModels() {
  const availability = await loadOcrAvailability();
  state.ocrManifest = availability.manifest;
  paintOcrAvailability();
}

el('sel-engine').addEventListener('change', async (event) => {
  cancelOcrRequest();
  state.ocrBusy = false;
  state.ocrEngine = event.target.value;
  state.ocr = null;
  invalidateAnalysis();
  syncSourceControls();
  syncTextState();
  if (state.ocrEngine === 'server') await shutdown();
});
el('sel-model').addEventListener('change', async () => {
  paintOcrAvailability({ selectFirstLanguage: true });
  state.ocr = null;
  invalidateAnalysis();
  await releaseWorkerForSelection(el('sel-model').value, el('sel-lang').value);
  syncOcrControls();
});
el('sel-lang').addEventListener('change', async () => {
  state.ocr = null;
  invalidateAnalysis();
  if (state.ocrEngine === 'browser' && state.source.kind !== 'pdf') {
    paintOcrAvailability();
    await releaseWorkerForSelection(el('sel-model').value, el('sel-lang').value);
  } else {
    syncOcrControls();
  }
  syncTextState();
});
el('pdf-preprocessing').addEventListener('change', (event) => {
  el('pdf-threshold-field').hidden = event.target.value !== 'threshold';
});

el('btn-ocr').addEventListener('click', runOcr);

async function runOcr() {
  if ((state.source.kind === 'pdf' || state.ocrEngine === 'server') && !authenticated()) {
    notice('Sign in before using server OCR.', 'error');
    openAuthDialog('login');
    return;
  }
  if (state.source.kind === 'pdf') {
    await runPdfOcr();
  } else if (state.ocrEngine === 'server') {
    await runServerImageOcr();
  } else {
    await runBrowserImageOcr();
  }
}

function beginOcr() {
  cancelOcrRequest();
  const revision = state.ocrRevision;
  const sourceRevision = state.sourceRevision;
  state.ocrBusy = true;
  state.ocr = null;
  invalidateAnalysis();
  syncOcrControls();
  showProgress(0, 'Preparing OCR…');
  return { revision, sourceRevision };
}

function ocrRunIsCurrent(run) {
  return run.revision === state.ocrRevision && run.sourceRevision === state.sourceRevision;
}

function finishOcr(run) {
  if (!ocrRunIsCurrent(run)) return;
  state.ocrBusy = false;
  state.ocrController = null;
  hideProgress();
  syncOcrControls();
}

function applyOcrText(text, snapshot) {
  state.ocr = snapshot;
  state.editorProvenance = provenanceForOcrSource(snapshot?.source);
  el('ocr-text').value = typeof text === 'string' ? text : '';
  invalidateAnalysis();
  syncTextState();
}

async function runBrowserImageOcr() {
  if (!state.processed) {
    notice('Choose an image before starting OCR.', 'error');
    return;
  }
  const run = beginOcr();
  const language = el('sel-lang').value;
  const profile = el('sel-model').value;
  try {
    const result = await recognize(state.processed, {
      lang: language,
      profile,
      onProgress: ({ status, progress }) => {
        if (ocrRunIsCurrent(run)) showProgress(progress, status);
      },
    });
    if (!ocrRunIsCurrent(run)) return;
    applyOcrText(result.text, mapBrowserOcr(result));
    if (!result.text) {
      notice('No text was recognised. Try cropping tighter, or turn on grayscale + threshold.', 'error');
    } else {
      notice(
        `Recognised ${result.words} words with ${result.profileLabel} (${result.languageLabel}).`,
        'ok',
      );
    }
  } catch (error) {
    if (!ocrRunIsCurrent(run)) return;
    notice(
      error instanceof OcrModelError
        ? error.message
        : `OCR failed: ${error.message || 'Unknown OCR error.'}`,
      'error',
    );
  } finally {
    finishOcr(run);
  }
}

async function processedPngFile() {
  if (!state.processed) throw new Error('Choose an image before starting OCR.');
  if (state.processed.width * state.processed.height > CONFIG.maxImagePixels) {
    throw new Error('The processed image exceeds the pixel limit.');
  }
  const blob = await IU.canvasToBlob(state.processed, 'image/png');
  if (!blob) throw new Error('The browser could not encode the processed image as PNG.');
  if (blob.size > CONFIG.maxImageBytes) {
    throw new Error(`The processed PNG exceeds ${formatMegabytes(CONFIG.maxImageBytes)}.`);
  }
  const stem = (state.source.name || 'processed').replace(/\.[^.]+$/, '');
  return new File([blob], `${stem}.png`, { type: 'image/png' });
}

async function runServerImageOcr() {
  if (!state.processed) {
    notice('Choose an image before starting OCR.', 'error');
    return;
  }
  const run = beginOcr();
  const requestAuth = beginProtectedRequest();
  const controller = new AbortController();
  state.ocrController = controller;
  showProgress(0, 'Uploading processed image to server OCR…');
  try {
    const file = await processedPngFile();
    if (!ocrRunIsCurrent(run) || !protectedRequestIsCurrent(requestAuth)) return;
    const result = await api.recognizeImage(file, {
      language: el('sel-lang').value,
      signal: controller.signal,
    });
    if (!ocrRunIsCurrent(run) || !protectedRequestIsCurrent(requestAuth)) return;
    markBackendReachable();
    applyOcrText(result.text, mapServerImageOcr(result));
    notice(`Server OCR recognised ${result.words} words.`, result.text ? 'ok' : 'error');
  } catch (error) {
    if (!ocrRunIsCurrent(run) || error?.kind === 'cancelled') return;
    if (handleProtectedApiError(error, requestAuth)) return;
    notice(`Server OCR failed: ${error.message || 'Unknown error.'}`, 'error');
  } finally {
    finishOcr(run);
  }
}

async function runPdfOcr() {
  if (!state.source.file) {
    notice('Choose a PDF before starting OCR.', 'error');
    return;
  }
  const preprocessing = el('pdf-preprocessing').value;
  let threshold;
  try {
    threshold = parsePdfThreshold(preprocessing, el('pdf-threshold').value);
  } catch (error) {
    notice(error.message, 'error');
    return;
  }
  const run = beginOcr();
  const requestAuth = beginProtectedRequest();
  const controller = new AbortController();
  state.ocrController = controller;
  showProgress(0, 'Uploading PDF; OCR runs sequentially by page…');
  try {
    const result = await api.recognizePdf(state.source.file, {
      language: el('sel-lang').value,
      preprocessing,
      threshold,
      password: el('pdf-password').value,
      signal: controller.signal,
    });
    if (!ocrRunIsCurrent(run) || !protectedRequestIsCurrent(requestAuth)) return;
    markBackendReachable();
    const mapped = mapServerPdfOcr(result);
    applyOcrText(result.text, mapped.snapshot);
    el('pdf-password').value = '';
    notice(
      `Server OCR completed ${mapped.pageCount} page${mapped.pageCount === 1 ? '' : 's'} and recognised ${mapped.snapshot.words} words.`,
      result.text ? 'ok' : 'error',
    );
  } catch (error) {
    if (!ocrRunIsCurrent(run) || error?.kind === 'cancelled') return;
    if (handleProtectedApiError(error, requestAuth)) return;
    notice(`PDF OCR failed: ${error.message || 'Unknown error.'}`, 'error');
  } finally {
    finishOcr(run);
  }
}

function showProgress(progress, label) {
  el('progress').hidden = false;
  el('progress-bar').style.width = `${Math.round(IU.clamp(progress, 0, 1) * 100)}%`;
  el('progress-label').textContent =
    `${label}${progress ? ` — ${Math.round(progress * 100)}%` : ''}`;
}

function hideProgress() {
  el('progress').hidden = true;
  el('progress-bar').style.width = '0%';
}

/* ── editable text and AI ────────────────────────────────────────────── */

function currentAnalysisContext(text = el('ocr-text').value) {
  return {
    sourceRevision: state.sourceRevision,
    filename: state.source.name || 'untitled',
    language: el('sel-lang').value,
    text,
  };
}

function paintAiFreshness() {
  if (!state.ai) return;
  const current = state.ai.fingerprint === analysisFingerprint(currentAnalysisContext())
    && state.ai.analyzedText === el('ocr-text').value;
  const provider = typeof state.ai.result?.provider === 'string'
    ? state.ai.result.provider.trim()
    : '';
  el('ai-source').textContent = `${provider ? `AI provider: ${provider}` : 'AI provider: not reported'}${current ? '' : ' · stale'}`;
  el('ai-source').className = `tag ${current ? 'tag-accent-2' : 'tag-neutral'}`;
}

el('ocr-text').addEventListener('input', () => {
  invalidateAnalysis();
  syncTextState();
});

function syncTextState() {
  const rawText = el('ocr-text').value;
  const text = rawText.trim();
  const words = text ? text.split(/\s+/).length : 0;
  const details = state.ocr
    ? [
      state.ocr.engine,
      state.ocr.confidence != null ? `OCR ${Math.round(state.ocr.confidence)}%` : '',
      state.ocr.profile,
      LANGUAGES.find((item) => item.code === state.ocr.language)?.label || state.ocr.language,
    ].filter(Boolean).join(' · ')
    : '';
  el('text-meta').textContent = text
    ? `${words} words · ${rawText.length} characters${details ? ` · ${details}` : ''}`
    : 'no text yet';
  el('btn-analyze').disabled = !text
    || state.aiBusy
    || !authenticated()
    || state.health.aiAvailable === false;
  el('btn-copy').disabled = !text;
  el('btn-save').disabled = !text || state.saveBusy || !authenticated();
  paintAiFreshness();
}

el('btn-copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(el('ocr-text').value);
    notice('Text copied to the clipboard.', 'ok');
  } catch {
    notice('The browser could not copy the text.', 'error');
  }
});

el('btn-analyze').addEventListener('click', async () => {
  const text = el('ocr-text').value;
  if (!text.trim()) {
    notice('Extract or enter text before requesting AI analysis.', 'error');
    return;
  }
  if (!authenticated()) {
    notice('Sign in before requesting AI analysis.', 'error');
    openAuthDialog('login');
    return;
  }
  if (state.health.aiAvailable === false) {
    notice('AI analysis is not configured on the backend.', 'error');
    return;
  }

  state.aiRevision += 1;
  const revision = state.aiRevision;
  state.aiController?.abort();
  const controller = new AbortController();
  state.aiController = controller;
  const requestAuth = beginProtectedRequest();
  const context = currentAnalysisContext(text);
  const fingerprint = analysisFingerprint(context);
  state.aiBusy = true;
  syncTextState();
  el('btn-analyze').textContent = 'Working…';

  try {
    const result = await api.analyze({
      filename: context.filename,
      text,
      language: context.language,
    }, { signal: controller.signal });
    if (
      revision !== state.aiRevision
      || !protectedRequestIsCurrent(requestAuth)
      || fingerprint !== analysisFingerprint(currentAnalysisContext())
      || text !== el('ocr-text').value
    ) return;
    markBackendReachable();
    state.ai = { result, analyzedText: text, fingerprint };
    paintAI(result);
  } catch (error) {
    if (revision !== state.aiRevision || error?.kind === 'cancelled') return;
    if (handleProtectedApiError(error, requestAuth)) return;
    notice(`AI analysis is unavailable: ${error.message} Your OCR text is unchanged.`, 'error');
  } finally {
    if (revision === state.aiRevision) {
      state.aiBusy = false;
      state.aiController = null;
      el('btn-analyze').textContent = 'Classify & summarise';
      syncTextState();
    }
  }
});

function paintAI(result) {
  el('ai').hidden = false;
  el('ai-class').textContent = result.classification || 'unclassified';
  el('ai-conf').textContent = typeof result.confidence === 'number'
    ? `confidence ${Math.round(result.confidence * 100)}%`
    : '';
  el('ai-summary').textContent = result.summary || '—';
  const fields = Array.isArray(result.fields) ? result.fields : [];
  el('ai-fields-wrap').hidden = fields.length === 0;
  el('ai-fields').innerHTML = fields
    .map((field) => `<div class="field-pair"><dt>${escapeHtml(field.label)}</dt><dd>${escapeHtml(field.value)}</dd></div>`)
    .join('');
  const tags = Array.isArray(result.tags) ? result.tags : [];
  el('ai-tags-wrap').hidden = tags.length === 0;
  el('ai-tags').innerHTML = tags
    .map((tag) => `<span class="tag tag-accent">${escapeHtml(tag)}</span>`)
    .join('');
  paintAiFreshness();
}

/* ── save to server archive ──────────────────────────────────────────── */

el('btn-save').addEventListener('click', saveCurrentScan);

async function saveCurrentScan() {
  const text = el('ocr-text').value;
  if (!text.trim() || state.saveBusy) return;
  if (!authenticated()) {
    notice('Sign in before saving to the server archive.', 'error');
    openAuthDialog('login');
    return;
  }
  const context = currentAnalysisContext(text);
  let payload;
  try {
    payload = buildScanPayload({
      filename: state.source.name || 'untitled',
      text,
      ai: state.ai,
      analysisContext: context,
      ocr: state.ocr,
    });
  } catch (error) {
    if (!(error instanceof ArchiveContractError) || !state.ai) {
      notice(error.message, 'error');
      return;
    }
    const saveWithoutAnalysis = confirm(
      `${error.message}\n\nSave this scan without AI analysis? The visible AI result will not be changed.`,
    );
    if (!saveWithoutAnalysis) return;
    payload = buildScanPayload({
      filename: state.source.name || 'untitled',
      text,
      ai: state.ai,
      analysisContext: context,
      ocr: state.ocr,
      omitAnalysis: true,
    });
  }

  state.saveRevision += 1;
  const revision = state.saveRevision;
  const requestAuth = beginProtectedRequest();
  const controller = new AbortController();
  state.saveController = controller;
  state.saveBusy = true;
  syncTextState();
  el('btn-save').textContent = 'Saving…';
  try {
    const saved = await api.createScan(payload, { signal: controller.signal });
    if (revision !== state.saveRevision || !protectedRequestIsCurrent(requestAuth)) return;
    markBackendReachable();
    const savedId = saved?.id || 'unknown ID';
    state.savedEditor = { sourceRevision: state.sourceRevision, text };
    notice(`Saved to the server archive as ${savedId}.`, 'ok');
    const reloaded = await loadArchive({ clearCache: true, quiet: true });
    if (revision !== state.saveRevision || !protectedRequestIsCurrent(requestAuth)) return;
    if (!reloaded) {
      notice(
        `Saved to the server archive as ${savedId}, but the archive view could not be refreshed. Do not save a duplicate; retry the archive reload.`,
        'error',
      );
    }
  } catch (error) {
    if (
      revision !== state.saveRevision
      || error?.kind === 'cancelled'
      || handleProtectedApiError(error, requestAuth)
    ) return;
    const uncertain = error instanceof ApiError && (
      ['network', 'timeout'].includes(error.kind)
      || (error.status >= 200 && error.status < 300)
    );
    notice(
      uncertain
        ? `The save could not be confirmed: ${error.message} Check the archive before retrying.`
        : `Could not save this scan: ${error.message}`,
      'error',
    );
  } finally {
    if (revision === state.saveRevision) {
      state.saveBusy = false;
      state.saveController = null;
      el('btn-save').textContent = 'Save to server archive';
      syncTextState();
    }
  }
}

/* ── server archive list ─────────────────────────────────────────────── */

el('filter-class').innerHTML = [
  '<option value="all">All</option>',
  ...CLASSIFICATIONS.map((classification) => (
    `<option value="${classification}">${classificationLabel(classification)}</option>`
  )),
].join('');

function cancelArchiveList() {
  state.archive.revision += 1;
  state.archive.controller?.abort();
  state.archive.controller = null;
  state.archive.busy = false;
}

async function loadArchive({ clearCache = false, quiet = false } = {}) {
  if (!authenticated()) {
    resetArchiveState();
    renderArchive();
    return false;
  }
  cancelArchiveList();
  const revision = state.archive.revision;
  const requestAuth = beginProtectedRequest();
  const controller = new AbortController();
  state.archive.controller = controller;
  state.archive.busy = true;
  state.archive.error = '';
  if (clearCache) state.archive.detailCache.clear();
  renderArchive();
  try {
    const requestedOffset = state.archive.offset;
    let response = await api.listScans(listQuery(state.archive), {
      signal: controller.signal,
    });
    if (
      revision !== state.archive.revision
      || !protectedRequestIsCurrent(requestAuth)
    ) return false;
    markBackendReachable();
    validateArchiveListResponse(response, requestedOffset);

    const correctedOffset = validPageOffset(
      requestedOffset,
      response.limit,
      response.total,
    );
    if (correctedOffset !== requestedOffset) {
      state.archive.offset = correctedOffset;
      response = await api.listScans(listQuery(state.archive), {
        signal: controller.signal,
      });
      if (
        revision !== state.archive.revision
        || !protectedRequestIsCurrent(requestAuth)
      ) return false;
      markBackendReachable();
      validateArchiveListResponse(response, correctedOffset);
    }
    state.archive.items = response.items;
    state.archive.total = response.total;
    state.archive.limit = response.limit;
    state.archive.offset = response.offset;
    return true;
  } catch (error) {
    if (revision !== state.archive.revision || error?.kind === 'cancelled') return false;
    if (handleProtectedApiError(error, requestAuth)) return false;
    state.archive.error = `Archive could not be loaded: ${error.message}`;
    if (!quiet) notice(state.archive.error, 'error');
    return false;
  } finally {
    if (revision === state.archive.revision) {
      state.archive.busy = false;
      state.archive.controller = null;
      renderArchive();
    }
  }
}

function validateArchiveListResponse(response, requestedOffset) {
  if (
    !response
    || !Array.isArray(response.items)
    || !Number.isInteger(response.total)
    || !Number.isInteger(response.limit)
    || !Number.isInteger(response.offset)
    || response.offset !== requestedOffset
  ) {
    throw new TypeError('The archive list response is invalid.');
  }
}

let searchTimer = null;
el('q').addEventListener('input', (event) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.archive.query = event.target.value;
    state.archive.offset = 0;
    loadArchive();
  }, 300);
});
el('filter-class').addEventListener('change', (event) => {
  state.archive.classification = event.target.value;
  state.archive.offset = 0;
  loadArchive();
});
el('page-size').addEventListener('change', (event) => {
  state.archive.limit = Number(event.target.value);
  state.archive.offset = 0;
  loadArchive();
});
el('page-prev').addEventListener('click', () => {
  state.archive.offset = previousPageOffset(state.archive.offset, state.archive.limit);
  loadArchive();
});
el('page-next').addEventListener('click', () => {
  state.archive.offset = nextPageOffset(
    state.archive.offset,
    state.archive.limit,
    state.archive.total,
  );
  loadArchive();
});
$$('.sortable').forEach((heading) => {
  heading.addEventListener('click', () => {
    if (state.archive.exportBusy) return;
    const sort = heading.dataset.key;
    if (state.archive.sort === sort) {
      state.archive.order = state.archive.order === 'asc' ? 'desc' : 'asc';
    } else {
      state.archive.sort = sort;
      state.archive.order = sort === 'scanned_at' ? 'desc' : 'asc';
    }
    state.archive.offset = 0;
    loadArchive();
  });
});

function renderArchive() {
  const archive = state.archive;
  const signedIn = authenticated();
  const visibleItems = signedIn ? archive.items : [];
  const visibleTotal = signedIn ? archive.total : 0;
  const first = visibleTotal ? archive.offset + 1 : 0;
  const last = Math.min(archive.offset + visibleItems.length, visibleTotal);
  el('tab-count').textContent = visibleTotal;
  el('results-count').textContent =
    `${first}–${last} of ${visibleTotal} document${visibleTotal === 1 ? '' : 's'}`;
  el('results-status').textContent = !signedIn
    ? 'Sign in to load the server archive.'
    : archive.busy
    ? 'Loading server archive…'
    : archive.error;
  el('results-status').dataset.state = archive.error ? 'missing' : 'ready';
  el('results-empty').hidden = !signedIn
    || archive.busy || archive.items.length > 0 || Boolean(archive.error);
  el('btn-clear').disabled = !signedIn
    || archive.mutationBusy || archive.exportBusy || archive.total === 0;
  el('btn-export').disabled = !signedIn || archive.exportBusy || archive.total === 0;
  el('q').disabled = !signedIn || archive.exportBusy;
  el('filter-class').disabled = !signedIn || archive.exportBusy;
  el('page-size').disabled = !signedIn || archive.exportBusy;
  el('page-prev').disabled = !signedIn
    || archive.busy || archive.exportBusy || archive.offset === 0;
  el('page-next').disabled = !signedIn
    || archive.busy
    || archive.exportBusy
    || archive.offset + archive.items.length >= archive.total;
  const pages = Math.max(1, Math.ceil(visibleTotal / archive.limit));
  const page = Math.min(pages, Math.floor(archive.offset / archive.limit) + 1);
  el('page-label').textContent = `Page ${page} of ${pages}`;
  $$('.sortable').forEach((heading) => {
    heading.dataset.dir = heading.dataset.key === archive.sort ? archive.order : '';
  });

  el('results-body').innerHTML = visibleItems.map((scan) => {
    const analysis = scan.analysis;
    const ocr = scan.ocr;
    const classification = analysis?.classification || 'unclassified';
    const ocrLabel = ocr
      ? [
        ocr.source,
        ocr.engine,
        LANGUAGES.find((item) => item.code === ocr.language)?.label || ocr.language,
        ocr.profile,
      ].filter(Boolean).join(' · ')
      : '';
    return `
      <tr data-id="${escapeHtml(scan.id)}">
        <td class="cell-file">
          <button class="link-btn" data-act="open" data-id="${escapeHtml(scan.id)}">${escapeHtml(scan.filename)}</button>
          ${ocrLabel ? `<span class="cell-sub text-muted">${escapeHtml(ocrLabel)}</span>` : ''}
        </td>
        <td class="cell-date">${formatDate(scan.scanned_at)}</td>
        <td class="cell-snippet">${escapeHtml(scan.snippet || '')}</td>
        <td><span class="tag ${classification === 'unclassified' ? 'tag-neutral' : 'tag-accent'}">${escapeHtml(classification)}</span></td>
        <td class="cell-summary">${analysis?.summary ? escapeHtml(snippet(analysis.summary, 130)) : '<span class="text-muted">—</span>'}</td>
        <td class="cell-actions">
          <button class="btn btn-ghost" data-act="open" data-id="${escapeHtml(scan.id)}" ${archive.exportBusy ? 'disabled' : ''}>View</button>
          <button class="btn btn-ghost btn-danger" data-act="delete" data-id="${escapeHtml(scan.id)}" ${archive.exportBusy ? 'disabled' : ''}>Delete</button>
        </td>
      </tr>`;
  }).join('');
}

el('results-body').addEventListener('click', (event) => {
  const button = event.target.closest('[data-act]');
  if (!button) return;
  if (button.dataset.act === 'open') openDetail(button.dataset.id);
  else deleteScan(button.dataset.id);
});

async function deleteScan(id) {
  if (state.archive.mutationBusy || state.archive.exportBusy) return;
  const scan = state.archive.items.find((item) => item.id === id);
  if (!confirm(`Delete “${scan?.filename || 'this scan'}” from the server archive?`)) return;
  cancelArchiveList();
  state.archive.mutationRevision += 1;
  const revision = state.archive.mutationRevision;
  const requestAuth = beginProtectedRequest();
  state.archive.mutationBusy = true;
  renderArchive();
  try {
    await api.deleteScan(id);
    if (
      revision !== state.archive.mutationRevision
      || !protectedRequestIsCurrent(requestAuth)
    ) return;
    markBackendReachable();
    state.archive.detailCache.clear();
    if (state.detail.id === id) closeDetail();
    state.archive.offset = offsetAfterDelete(
      state.archive.offset,
      state.archive.limit,
      Math.max(0, state.archive.total - 1),
    );
    notice('The scan was deleted from the server archive.', 'ok');
    const reloaded = await loadArchive({ quiet: true });
    if (
      revision !== state.archive.mutationRevision
      || !protectedRequestIsCurrent(requestAuth)
    ) return;
    if (!reloaded) {
      notice('The scan was deleted, but the archive view could not be refreshed.', 'error');
    }
  } catch (error) {
    if (revision !== state.archive.mutationRevision || error?.kind === 'cancelled') return;
    if (handleProtectedApiError(error, requestAuth)) return;
    if (error instanceof ApiError && error.status === 404) {
      state.archive.detailCache.clear();
      if (state.detail.id === id) closeDetail();
      notice('That scan was already absent. Reloading the server archive.', 'info');
      const reloaded = await loadArchive({ quiet: true });
      if (
        revision !== state.archive.mutationRevision
        || !protectedRequestIsCurrent(requestAuth)
      ) return;
      if (!reloaded) {
        notice('The scan was already absent, but the archive view could not be refreshed.', 'error');
      }
      return;
    }
    notice(`Could not delete the scan: ${error.message}`, 'error');
  } finally {
    if (revision === state.archive.mutationRevision) {
      state.archive.mutationBusy = false;
      renderArchive();
    }
  }
}

el('btn-clear').addEventListener('click', async () => {
  if (!state.archive.total || state.archive.mutationBusy || state.archive.exportBusy) return;
  if (!confirm(`Delete all ${state.archive.total} records from the server archive?`)) return;
  cancelArchiveList();
  state.archive.mutationRevision += 1;
  const revision = state.archive.mutationRevision;
  const requestAuth = beginProtectedRequest();
  state.archive.mutationBusy = true;
  renderArchive();
  try {
    const response = await api.clearScans();
    if (
      revision !== state.archive.mutationRevision
      || !protectedRequestIsCurrent(requestAuth)
    ) return;
    markBackendReachable();
    cancelArchiveList();
    state.archive.items = [];
    state.archive.total = 0;
    state.archive.offset = 0;
    state.archive.detailCache.clear();
    closeDetail();
    renderArchive();
    notice(`Deleted ${response?.deleted || 0} server archive record(s).`, 'ok');
  } catch (error) {
    if (revision !== state.archive.mutationRevision || error?.kind === 'cancelled') return;
    if (handleProtectedApiError(error, requestAuth)) return;
    notice(`Could not clear the server archive: ${error.message}`, 'error');
  } finally {
    if (revision === state.archive.mutationRevision) {
      state.archive.mutationBusy = false;
      renderArchive();
    }
  }
});

/* ── archive detail and exports ──────────────────────────────────────── */

async function openDetail(id) {
  state.detail.revision += 1;
  const revision = state.detail.revision;
  state.detail.controller?.abort();
  state.detail.controller = null;
  state.detail.id = id;
  el('detail').hidden = false;
  el('detail-title').textContent =
    state.archive.items.find((item) => item.id === id)?.filename || 'Scan details';
  el('detail-status').textContent = 'Loading complete server record…';
  el('detail-content').hidden = true;

  const cached = state.archive.detailCache.get(id);
  if (cached) {
    paintDetail(cached);
    return;
  }
  const controller = new AbortController();
  state.detail.controller = controller;
  const requestAuth = beginProtectedRequest();
  try {
    const record = await api.getScan(id, { signal: controller.signal });
    if (
      revision !== state.detail.revision
      || state.detail.id !== id
      || !protectedRequestIsCurrent(requestAuth)
    ) return;
    markBackendReachable();
    state.archive.detailCache.set(id, record);
    paintDetail(record);
  } catch (error) {
    if (revision !== state.detail.revision || error?.kind === 'cancelled') return;
    if (handleProtectedApiError(error, requestAuth)) return;
    if (error instanceof ApiError && error.status === 404) {
      closeDetail();
      notice('That scan no longer exists. Reloading the archive.', 'error');
      loadArchive({ clearCache: true });
      return;
    }
    el('detail-status').textContent = `Could not load details: ${error.message}`;
  } finally {
    if (revision === state.detail.revision) state.detail.controller = null;
  }
}

function paintDetail(record) {
  if (state.detail.id !== record.id) return;
  const analysis = record.analysis;
  el('detail-kicker').textContent =
    `${analysis?.classification || 'unclassified'} · ${formatDate(record.scanned_at)}`;
  el('detail-title').textContent = record.filename;
  el('detail-summary').textContent =
    analysis?.summary || 'No AI analysis was saved for this scan.';
  el('detail-text').textContent = record.text || '';
  el('detail-fields').innerHTML = (analysis?.fields || [])
    .map((field) => `<div class="field-pair"><dt>${escapeHtml(field.label)}</dt><dd>${escapeHtml(field.value)}</dd></div>`)
    .join('');
  el('detail-status').textContent = '';
  el('detail-content').hidden = false;
}

function closeDetail() {
  state.detail.revision += 1;
  state.detail.controller?.abort();
  state.detail.controller = null;
  state.detail.id = null;
  el('detail').hidden = true;
}

el('detail-close').addEventListener('click', closeDetail);
el('detail').addEventListener('click', (event) => {
  if (event.target === el('detail')) closeDetail();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !el('detail').hidden) closeDetail();
  if (event.key === 'Escape' && !el('auth-dialog').hidden) closeAuthDialog();
});

el('btn-export').addEventListener('click', async () => {
  if (state.archive.exportBusy || !state.archive.total) return;
  clearTimeout(searchTimer);
  state.archive.exportRevision += 1;
  const revision = state.archive.exportRevision;
  const requestAuth = beginProtectedRequest();
  const controller = new AbortController();
  state.archive.exportController = controller;
  state.archive.exportBusy = true;
  renderArchive();
  try {
    const records = await collectArchiveForExport({
      listScans: (query) => api.listScans(query, { signal: controller.signal }),
      getScan: (id) => api.getScan(id, { signal: controller.signal }),
      pageSize: 200,
      concurrency: 4,
    });
    if (
      revision !== state.archive.exportRevision
      || !protectedRequestIsCurrent(requestAuth)
    ) return;
    markBackendReachable();
    downloadJson(records, 'visual-scan-server-archive.json');
    notice(`Exported ${records.length} complete server record(s).`, 'ok');
  } catch (error) {
    if (
      revision !== state.archive.exportRevision
      || error?.kind === 'cancelled'
      || handleProtectedApiError(error, requestAuth)
    ) return;
    notice(`Export stopped without creating a partial file: ${error.message}`, 'error');
  } finally {
    if (revision === state.archive.exportRevision) {
      state.archive.exportBusy = false;
      state.archive.exportController = null;
      renderArchive();
    }
  }
});

function paintLegacyBanner() {
  const count = legacyStore.count();
  el('legacy-banner').hidden = count === 0;
  el('legacy-count').textContent = count;
}

el('btn-legacy-export').addEventListener('click', () => {
  const records = legacyStore.all();
  if (!records.length) {
    paintLegacyBanner();
    return;
  }
  downloadJson(records, 'visual-scan-legacy-browser-archive.json');
  notice(`Exported ${records.length} legacy browser record(s).`, 'ok');
});
el('btn-legacy-clear').addEventListener('click', () => {
  const count = legacyStore.count();
  if (!count || !confirm(`Permanently delete ${count} legacy browser record(s)?`)) return;
  try {
    legacyStore.clear();
    paintLegacyBanner();
    notice('Legacy browser records were deleted.', 'ok');
  } catch (error) {
    const message = error instanceof StorageError
      ? error.message
      : 'Browser storage is unavailable.';
    notice(`Could not delete legacy records: ${message}`, 'error');
  }
});

/* ── authentication and pre-auth server archive ─────────────────────── */

let authMode = 'login';

function setAuthMode(mode) {
  authMode = mode === 'register' ? 'register' : 'login';
  const registering = authMode === 'register';
  el('auth-title').textContent = registering ? 'Register' : 'Sign in';
  el('auth-submit').textContent = registering ? 'Create account' : 'Sign in';
  el('auth-mode-login').className = `btn ${registering ? 'btn-ghost' : 'btn-secondary'}`;
  el('auth-mode-register').className = `btn ${registering ? 'btn-secondary' : 'btn-ghost'}`;
  el('auth-password').autocomplete = registering ? 'new-password' : 'current-password';
  el('auth-error').hidden = true;
}

function openAuthDialog(mode = 'login') {
  if (state.auth.busy) return;
  setAuthMode(mode);
  el('auth-dialog').hidden = false;
  el('auth-username').focus();
}

function closeAuthDialog() {
  if (state.auth.busy) return;
  el('auth-dialog').hidden = true;
  el('auth-password').value = '';
  el('auth-password').type = 'password';
  el('auth-show-password').textContent = 'Show';
  el('auth-error').hidden = true;
}

function paintAuthBusy() {
  for (const id of [
    'auth-username',
    'auth-password',
    'auth-submit',
    'auth-mode-login',
    'auth-mode-register',
    'auth-close',
  ]) {
    el(id).disabled = state.auth.busy;
  }
  el('auth-submit').textContent = state.auth.busy
    ? (authMode === 'register' ? 'Creating…' : 'Signing in…')
    : (authMode === 'register' ? 'Create account' : 'Sign in');
}

el('btn-sign-in').addEventListener('click', () => openAuthDialog('login'));
el('btn-register').addEventListener('click', () => openAuthDialog('register'));
el('btn-archive-sign-in').addEventListener('click', () => openAuthDialog('login'));
el('auth-mode-login').addEventListener('click', () => setAuthMode('login'));
el('auth-mode-register').addEventListener('click', () => setAuthMode('register'));
el('auth-close').addEventListener('click', closeAuthDialog);
el('auth-dialog').addEventListener('click', (event) => {
  if (event.target === el('auth-dialog')) closeAuthDialog();
});
el('auth-show-password').addEventListener('click', () => {
  const showing = el('auth-password').type === 'text';
  el('auth-password').type = showing ? 'password' : 'text';
  el('auth-show-password').textContent = showing ? 'Show' : 'Hide';
});

el('auth-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.auth.busy) return;
  let credentials;
  try {
    credentials = {
      username: normalizeUsername(el('auth-username').value),
      password: validatePassword(el('auth-password').value),
    };
  } catch (error) {
    el('auth-error').textContent = error instanceof AuthContractError
      ? error.message
      : 'Check the account fields.';
    el('auth-error').hidden = false;
    return;
  }

  cancelAuthVerification();
  state.auth.revision += 1;
  const revision = state.auth.revision;
  state.auth.controller?.abort();
  const controller = new AbortController();
  state.auth.controller = controller;
  state.auth.busy = true;
  el('auth-error').hidden = true;
  paintAuthBusy();
  paintAuthState();
  try {
    const response = authMode === 'register'
      ? await api.register(credentials, { signal: controller.signal })
      : await api.login(credentials, { signal: controller.signal });
    if (!isAuthRevisionCurrent(state.auth, revision)) return;
    const session = normalizeAuthSession(response);
    markBackendReachable();
    applyAuthenticatedSession(session);
    publishAuthIdentity();
    state.auth.controller = null;
    el('auth-dialog').hidden = true;
    el('auth-password').value = '';
    notice(authMode === 'register' ? 'Account created and signed in.' : 'Signed in.', 'ok');
    const reloaded = await loadArchive({ clearCache: true, quiet: true });
    if (!reloaded && authenticated()) {
      notice('Signed in, but the server archive could not be refreshed.', 'error');
    }
    await loadServerLegacyStatus();
  } catch (error) {
    if (!isAuthRevisionCurrent(state.auth, revision) || error?.kind === 'cancelled') return;
    applyApiReachability(error);
    el('auth-error').textContent = error.message || 'Authentication failed.';
    el('auth-error').hidden = false;
  } finally {
    if (isAuthRevisionCurrent(state.auth, revision)) {
      state.auth.busy = false;
      state.auth.controller = null;
      paintAuthBusy();
      paintAuthState();
    }
  }
});

async function verifyAuthenticatedSession() {
  const requestAuth = beginProtectedRequest();
  if (!protectedRequestIsCurrent(requestAuth)) return;

  state.auth.verificationRevision += 1;
  const verificationRevision = state.auth.verificationRevision;
  state.auth.verificationController?.abort();
  const controller = new AbortController();
  state.auth.verificationController = controller;

  try {
    const response = await api.authSession({ signal: controller.signal });
    if (
      verificationRevision !== state.auth.verificationRevision
      || !protectedRequestIsCurrent(requestAuth)
    ) return;
    markBackendReachable();
    const session = normalizeAuthSession(response);
    if (session.status === 'authenticated' && session.user.id === requestAuth.userId) {
      state.auth.user = session.user;
      state.auth.csrfToken = session.csrfToken;
      state.auth.verificationUnavailable = false;
      api.setCsrfToken(session.csrfToken);
      paintAuthState();
      return;
    }

    if (session.status === 'anonymous') {
      becomeAnonymous();
      return;
    }

    cancelAuthVerification();
    state.auth.revision += 1;
    cancelAuthBoundRequests();
    applyAuthenticatedSession(session);
    await loadArchive({ clearCache: true, quiet: true });
    await loadServerLegacyStatus();
  } catch (error) {
    if (
      verificationRevision !== state.auth.verificationRevision
      || !protectedRequestIsCurrent(requestAuth)
      || error?.kind === 'cancelled'
    ) return;
    applyApiReachability(error);
    const failure = planAuthVerificationFailure(state.auth);
    if (failure.preserveIdentity) {
      const firstFailure = !state.auth.verificationUnavailable;
      state.auth.verificationUnavailable = true;
      api.setCsrfToken(state.auth.csrfToken);
      paintAuthState();
      if (firstFailure) {
        notice('Session verification is unavailable. Your current work was preserved.', 'error');
      }
    } else if (failure.clearServerDerived) {
      becomeAnonymous();
    }
  } finally {
    if (verificationRevision === state.auth.verificationRevision) {
      state.auth.verificationController = null;
    }
  }
}

async function restoreAuthSession({
  revalidation = false,
  identityHint = NO_IDENTITY_HINT,
} = {}) {
  const previousUser = state.auth.user;
  const previousUserId = previousUser?.id || null;
  const clearForHint = identityHint !== NO_IDENTITY_HINT
    && previousUserId !== identityHint;
  cancelAuthVerification();
  state.auth.revision += 1;
  const revision = state.auth.revision;
  state.auth.controller?.abort();
  if (revalidation) cancelAuthBoundRequests();
  const controller = new AbortController();
  state.auth.controller = controller;
  state.auth.status = 'checking';
  state.auth.busy = true;
  if (clearForHint) clearServerDerivedState();
  paintAuthState();
  try {
    const response = await api.authSession({ signal: controller.signal });
    if (!isAuthRevisionCurrent(state.auth, revision)) return;
    markBackendReachable();
    const session = normalizeAuthSession(response);
    if (session.status === 'authenticated') {
      const changedIdentity = identityChanged(previousUser, session.user);
      applyAuthenticatedSession(session);
      if (!revalidation || changedIdentity || clearForHint) {
        await loadArchive({ clearCache: true, quiet: true });
        await loadServerLegacyStatus();
      }
    } else {
      api.clearCsrfToken();
      state.auth.status = 'anonymous';
      state.auth.user = null;
      state.auth.csrfToken = null;
      state.auth.verificationUnavailable = false;
      clearServerDerivedState();
    }
  } catch (error) {
    if (!isAuthRevisionCurrent(state.auth, revision) || error?.kind === 'cancelled') return;
    applyApiReachability(error);
    api.clearCsrfToken();
    state.auth.status = 'anonymous';
    state.auth.user = null;
    state.auth.csrfToken = null;
    state.auth.verificationUnavailable = false;
    clearServerDerivedState();
  } finally {
    if (isAuthRevisionCurrent(state.auth, revision)) {
      state.auth.busy = false;
      state.auth.controller = null;
      paintAuthState();
    }
  }
}

el('btn-logout').addEventListener('click', async () => {
  if (!authenticated() || state.auth.busy) return;
  const text = el('ocr-text').value;
  const unsaved = text.trim() && (
    !state.savedEditor
    || state.savedEditor.sourceRevision !== state.sourceRevision
    || state.savedEditor.text !== text
  );
  if (unsaved && !confirm(
    'Log out with unsaved editor text? Account-derived results will be cleared; browser/manual text remains local.',
  )) return;

  cancelAuthVerification();
  state.auth.revision += 1;
  const revision = state.auth.revision;
  cancelAuthBoundRequests();
  state.auth.busy = true;
  const controller = new AbortController();
  state.auth.controller = controller;
  paintAuthState();
  try {
    await api.logout({ signal: controller.signal });
    if (!isAuthRevisionCurrent(state.auth, revision)) return;
    markBackendReachable();
    becomeAnonymous();
    publishAuthIdentity();
    notice('Signed out. Browser OCR and manual text remain available.', 'ok');
  } catch (error) {
    if (!isAuthRevisionCurrent(state.auth, revision) || error?.kind === 'cancelled') return;
    applyApiReachability(error);
    notice(`Could not sign out: ${error.message}`, 'error');
  } finally {
    if (isAuthRevisionCurrent(state.auth, revision)) {
      state.auth.busy = false;
      state.auth.controller = null;
      paintAuthState();
    }
  }
});

function paintServerLegacy() {
  const legacy = state.auth.legacy;
  const visible = authenticated()
    && state.auth.user.isInitialUser
    && legacy.count > 0;
  el('server-legacy-banner').hidden = !visible;
  el('server-legacy-count').textContent = legacy.count;
  el('btn-server-legacy-claim').disabled = legacy.busy || !legacy.claimable;
}

async function loadServerLegacyStatus() {
  const legacy = state.auth.legacy;
  legacy.revision += 1;
  const revision = legacy.revision;
  legacy.controller?.abort();
  legacy.controller = null;
  if (!authenticated() || !state.auth.user.isInitialUser) {
    legacy.count = 0;
    legacy.claimable = false;
    paintServerLegacy();
    return;
  }
  const requestAuth = beginProtectedRequest();
  const controller = new AbortController();
  legacy.controller = controller;
  try {
    const response = await api.legacyScans({ signal: controller.signal });
    if (
      state.auth.legacy !== legacy
      || revision !== legacy.revision
      || !protectedRequestIsCurrent(requestAuth)
    ) return;
    markBackendReachable();
    legacy.count = Number.isInteger(response?.count) ? response.count : 0;
    legacy.claimable = response?.claimable === true;
  } catch (error) {
    if (
      state.auth.legacy !== legacy
      || revision !== legacy.revision
      || error?.kind === 'cancelled'
    ) return;
    if (!handleProtectedApiError(error, requestAuth) && error?.status !== 403) {
      notice(`Could not check the pre-auth server archive: ${error.message}`, 'error');
    }
  } finally {
    if (state.auth.legacy === legacy && revision === legacy.revision) {
      legacy.controller = null;
      paintServerLegacy();
    }
  }
}

el('btn-server-legacy-claim').addEventListener('click', async () => {
  const legacy = state.auth.legacy;
  if (!authenticated() || !legacy.claimable || legacy.busy) return;
  if (!confirm(`Claim ${legacy.count} pre-auth server record(s) for this account?`)) return;
  legacy.revision += 1;
  const revision = legacy.revision;
  legacy.controller?.abort();
  const controller = new AbortController();
  legacy.controller = controller;
  const requestAuth = beginProtectedRequest();
  legacy.busy = true;
  paintServerLegacy();
  try {
    const response = await api.claimLegacyScans({ signal: controller.signal });
    if (
      state.auth.legacy !== legacy
      || revision !== legacy.revision
      || !protectedRequestIsCurrent(requestAuth)
    ) return;
    markBackendReachable();
    const claimed = Number.isInteger(response?.claimed) ? response.claimed : 0;
    legacy.count = 0;
    legacy.claimable = false;
    notice(`Claimed ${claimed} pre-auth server record(s).`, 'ok');
    const reloaded = await loadArchive({ clearCache: true, quiet: true });
    if (
      state.auth.legacy !== legacy
      || revision !== legacy.revision
      || !protectedRequestIsCurrent(requestAuth)
    ) return;
    if (!reloaded && authenticated()) {
      notice(`Claimed ${claimed} record(s), but the archive view could not be refreshed.`, 'error');
    }
  } catch (error) {
    if (
      state.auth.legacy !== legacy
      || revision !== legacy.revision
      || error?.kind === 'cancelled'
    ) return;
    if (!handleProtectedApiError(error, requestAuth)) {
      notice(`Could not claim the pre-auth server archive: ${error.message}`, 'error');
    }
  } finally {
    if (state.auth.legacy === legacy && revision === legacy.revision) {
      legacy.busy = false;
      legacy.controller = null;
      paintServerLegacy();
    }
  }
});

function downloadJson(value, filename) {
  const blob = new Blob([JSON.stringify(value, null, 2)], {
    type: 'application/json',
  });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 2_000);
}

/* ── misc and startup ────────────────────────────────────────────────── */

let noticeTimer = null;
function notice(message, tone = 'info') {
  const target = el('notice');
  target.hidden = false;
  target.textContent = message;
  target.dataset.tone = tone;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { target.hidden = true; }, 12_000);
}

function escapeHtml(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (character) => (
    {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[character]
  ));
}

function formatMegabytes(bytes) {
  return `${Math.round((bytes / (1024 * 1024)) * 10) / 10} MB`;
}

function formatMegapixels(pixels) {
  return `${Math.round((pixels / 1_000_000) * 10) / 10} megapixels`;
}

function classificationLabel(value) {
  const words = String(value).replaceAll('_', ' ');
  return `${words.charAt(0).toUpperCase()}${words.slice(1)}`;
}

el('dateline-date').textContent = new Date().toLocaleDateString(undefined, {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
  year: 'numeric',
});

authSync = createAuthSync(({ userId }) => {
  scheduleAuthRevalidation(userId);
});

window.addEventListener('focus', () => scheduleAuthRevalidation());
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') scheduleAuthRevalidation();
});

window.addEventListener('beforeunload', () => {
  clearTimeout(authRevalidationTimer);
  authSync?.close();
  state.auth.verificationController?.abort();
  state.ocrController?.abort();
  state.aiController?.abort();
  state.archive.controller?.abort();
  state.detail.controller?.abort();
  shutdown();
  stopCamera();
});

paintConnection();
paintAiAvailability();
paintLegacyBanner();
renderArchive();
syncTextState();
paintOcrAvailability();
paintServerLegacy();
paintAuthState();
loadOcrModels();
checkBackend();
restoreAuthSession();
