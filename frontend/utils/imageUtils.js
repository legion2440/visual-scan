/**
 * imageUtils.js — Canvas API helpers for the scan pipeline.
 *
 * Pure functions: every operation takes a source (Image | Canvas) and returns
 * a NEW canvas, so the app can keep the original untouched and re-derive the
 * preview on every change.
 */

/** Create a detached canvas of the given size. */
export function makeCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.round(w));
  c.height = Math.max(1, Math.round(h));
  return c;
}

/** Load an <img> from any URL (object URL, data URL, path). */
export function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('Could not decode this image.'));
    img.src = src;
  });
}

/** Read a File/Blob from an <input type="file"> into an <img>. */
export async function fileToImage(file) {
  const url = URL.createObjectURL(file);
  try {
    return await loadImage(url);
  } finally {
    // The bitmap is decoded by now; the object URL can go.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

/** Copy any drawable source into a fresh canvas. */
export function toCanvas(src) {
  const w = src.naturalWidth || src.width;
  const h = src.naturalHeight || src.height;
  const c = makeCanvas(w, h);
  c.getContext('2d').drawImage(src, 0, 0, w, h);
  return c;
}

/** Rotate by a multiple of 90°, swapping the canvas axes when needed. */
export function rotate(src, deg) {
  const angle = ((deg % 360) + 360) % 360;
  const w = src.naturalWidth || src.width;
  const h = src.naturalHeight || src.height;
  if (angle === 0) return toCanvas(src);
  const swap = angle === 90 || angle === 270;
  const c = makeCanvas(swap ? h : w, swap ? w : h);
  const ctx = c.getContext('2d');
  ctx.translate(c.width / 2, c.height / 2);
  ctx.rotate((angle * Math.PI) / 180);
  ctx.drawImage(src, -w / 2, -h / 2, w, h);
  return c;
}

/** Fine rotation in degrees (deskew), keeping the whole frame visible. */
export function rotateFree(src, deg) {
  if (!deg) return toCanvas(src);
  const w = src.naturalWidth || src.width;
  const h = src.naturalHeight || src.height;
  const rad = (deg * Math.PI) / 180;
  const cw = Math.abs(w * Math.cos(rad)) + Math.abs(h * Math.sin(rad));
  const ch = Math.abs(w * Math.sin(rad)) + Math.abs(h * Math.cos(rad));
  const c = makeCanvas(cw, ch);
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.translate(c.width / 2, c.height / 2);
  ctx.rotate(rad);
  ctx.drawImage(src, -w / 2, -h / 2, w, h);
  return c;
}

/** Crop with a rect in SOURCE pixel coordinates: {x, y, w, h}. */
export function crop(src, rect) {
  const sw = src.naturalWidth || src.width;
  const sh = src.naturalHeight || src.height;
  const x = clamp(Math.round(rect.x), 0, sw - 1);
  const y = clamp(Math.round(rect.y), 0, sh - 1);
  const w = clamp(Math.round(rect.w), 1, sw - x);
  const h = clamp(Math.round(rect.h), 1, sh - y);
  const c = makeCanvas(w, h);
  c.getContext('2d').drawImage(src, x, y, w, h, 0, 0, w, h);
  return c;
}

/**
 * Preprocess for OCR. All steps operate on one ImageData pass.
 * @param {object} o
 * @param {boolean} o.grayscale  luminance conversion
 * @param {number}  o.contrast   1 = untouched, 1.4 = punchier
 * @param {number|null} o.threshold  0-255 → hard black/white cut (null = off)
 * @param {boolean} o.invert     white text on dark paper
 * @param {number}  o.scale      upscale factor (Tesseract likes ~300dpi input)
 */
export function preprocess(src, o = {}) {
  const { grayscale = false, contrast = 1, threshold = null, invert = false, scale = 1 } = o;
  const sw = src.naturalWidth || src.width;
  const sh = src.naturalHeight || src.height;
  const c = makeCanvas(sw * scale, sh * scale);
  const ctx = c.getContext('2d');
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(src, 0, 0, c.width, c.height);

  const needsPixels = grayscale || threshold !== null || invert || contrast !== 1;
  if (!needsPixels) return c;

  const data = ctx.getImageData(0, 0, c.width, c.height);
  const px = data.data;
  for (let i = 0; i < px.length; i += 4) {
    let r = px[i], g = px[i + 1], b = px[i + 2];
    if (grayscale || threshold !== null) {
      const lum = 0.299 * r + 0.587 * g + 0.114 * b;
      r = g = b = lum;
    }
    if (contrast !== 1) {
      r = (r - 128) * contrast + 128;
      g = (g - 128) * contrast + 128;
      b = (b - 128) * contrast + 128;
    }
    if (threshold !== null) {
      const v = r >= threshold ? 255 : 0;
      r = g = b = v;
    }
    if (invert) { r = 255 - r; g = 255 - g; b = 255 - b; }
    px[i] = clamp(r, 0, 255);
    px[i + 1] = clamp(g, 0, 255);
    px[i + 2] = clamp(b, 0, 255);
  }
  ctx.putImageData(data, 0, 0);
  return c;
}

/**
 * Mean luminance + a crude ink ratio — used to suggest a threshold and to
 * tell the user whether preprocessing is likely to help.
 */
export function analyseTone(src) {
  const sample = makeCanvas(200, 200 * ((src.height || src.naturalHeight) / (src.width || src.naturalWidth)));
  const ctx = sample.getContext('2d');
  ctx.drawImage(src, 0, 0, sample.width, sample.height);
  const px = ctx.getImageData(0, 0, sample.width, sample.height).data;
  let sum = 0, n = 0;
  for (let i = 0; i < px.length; i += 4) {
    sum += 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2];
    n++;
  }
  const mean = sum / n;
  return { mean, suggestedThreshold: Math.round(clamp(mean * 0.86, 90, 200)) };
}

/** Contain-fit box for drawing a canvas into a container. */
export function fitContain(w, h, maxW, maxH) {
  const k = Math.min(maxW / w, maxH / h, 1);
  return { w: Math.round(w * k), h: Math.round(h * k), scale: k };
}

export function canvasToBlob(canvas, type = 'image/jpeg', quality = 0.92) {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
}

export function canvasToDataURL(canvas, type = 'image/jpeg', quality = 0.75) {
  return canvas.toDataURL(type, quality);
}

/** Small JPEG data URL for the results table thumbnail. */
export function makeThumbnail(src, maxSide = 220) {
  const w = src.naturalWidth || src.width;
  const h = src.naturalHeight || src.height;
  const box = fitContain(w, h, maxSide, maxSide);
  const c = makeCanvas(box.w, box.h);
  c.getContext('2d').drawImage(src, 0, 0, box.w, box.h);
  return canvasToDataURL(c, 'image/jpeg', 0.7);
}

export function clamp(v, min, max) {
  return Math.min(max, Math.max(min, v));
}
