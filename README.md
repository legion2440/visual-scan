# Visual Scan — frontend

Visual Scan is a standalone Vanilla JavaScript document-scanning frontend. It
accepts a document image or camera capture, lets the user prepare the page on a
`<canvas>`, and extracts editable text with Tesseract.js in the browser.

This repository currently contains the frontend baseline only. Backend
analysis, server-side OCR, database storage, authentication, and local
fast/standard/best OCR model sets are intentionally outside this step.

## Features

- JPEG, PNG, and WebP upload through a file picker or drag-and-drop.
- Camera capture on supported browsers.
- Canvas preview with 90° rotation, fine deskew, crop, grayscale, threshold,
  and invert controls.
- Client-side OCR with progress feedback and six language selections.
- Editable extracted text.
- Optional AI analysis request to a configured backend.
- Local results archive with sorting, search, classification filtering,
  detail view, deletion, and JSON export.
- Two views: **Upload & Scan** and **Scanned Results**.

The results archive uses `localStorage`. It is not synchronized with a backend
in this step.

## Run locally

Serve the repository root over HTTP:

```bash
python -m http.server 5500
```

Then open:

```text
http://localhost:5500/frontend/index.html
```

Do not open `frontend/index.html` through `file://`. Browser security rules
block the ES module imports, and the page displays a setup warning when this is
detected.

Camera capture normally works on `localhost` or another secure origin. The
browser still asks the user for permission.

## Configuration

The backend address and frontend safety limits have one source of truth:

```text
frontend/config.js
```

The default backend URL is:

```text
http://localhost:8000
```

Change `CONFIG.backendUrl` in `config.js` when the backend runs elsewhere.
Every application HTTP request is implemented in `frontend/utils/api.js`.

## Image limits and errors

The baseline frontend accepts:

- MIME types `image/jpeg`, `image/png`, and `image/webp`;
- files up to 20 MB;
- decoded images up to 25 megapixels.

The interface reports unsupported formats, oversized or invalid images,
camera errors, OCR failures, and an unavailable backend. Backend availability
does not affect upload, camera capture, Canvas preprocessing, browser OCR, or
the local results archive.

## Pinned dependencies

Tesseract.js is loaded from jsDelivr with the explicit version `5.1.1`:

```text
https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js
```

No `latest` JavaScript dependency URLs are used, and the frontend has no build
step.

## Future backend contract

The frontend currently reserves two JSON endpoints:

```http
GET /api/health
```

Example response:

```json
{
  "status": "ok",
  "ai_available": true,
  "provider": "provider-name"
}
```

```http
POST /api/ai/analyze
Content-Type: application/json
```

Example request:

```json
{
  "filename": "invoice.jpg",
  "text": "Recognised document text",
  "language": "eng"
}
```

Example response:

```json
{
  "filename": "invoice.jpg",
  "classification": "invoice",
  "confidence": 0.93,
  "summary": "Short document summary.",
  "tags": ["billing"],
  "fields": [
    {
      "label": "Total",
      "value": "1012.80 GBP"
    }
  ],
  "provider": "provider-name"
}
```

The frontend displays the provider value returned by the backend. No provider
name is hard-coded in the interface.

## Structure

```text
visual-scan/
├── frontend/
│   ├── index.html
│   ├── styles.css
│   ├── app.js
│   ├── config.js
│   └── utils/
│       ├── imageUtils.js
│       ├── ocr.js
│       ├── api.js
│       └── store.js
├── public/
│   └── sample-docs/
│       ├── invoice.jpg
│       ├── contract.jpg
│       └── note.jpg
├── .gitignore
└── README.md
```

### Module responsibilities

- `app.js` connects the interface, state, and user actions.
- `config.js` contains the backend URL and client safety limits.
- `imageUtils.js` contains Canvas image operations.
- `ocr.js` owns client-side Tesseract.js workers and OCR progress.
- `api.js` is the only HTTP transport module.
- `store.js` owns the browser-local results archive, filtering, and sorting.
