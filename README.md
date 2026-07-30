# Visual Scan — frontend

Visual Scan is a standalone Vanilla JavaScript document-scanning frontend. It
accepts a document image or camera capture, lets the user prepare the page on a
`<canvas>`, and extracts editable text with Tesseract.js in the browser.

This repository currently contains the frontend only. Backend analysis,
server-side OCR, database storage, authentication, and Docker are outside this
step.

## Features

- JPEG, PNG, and WebP upload through a file picker or drag-and-drop.
- Camera capture on supported browsers.
- Canvas preview with 90° rotation, fine deskew, crop, grayscale, threshold,
  and invert controls.
- Client-side OCR with explicit `fast`, `standard`, and `best` local model
  profiles.
- English, Russian, English + Russian, German, French, and Spanish selections.
- Editable extracted text.
- Optional AI analysis request to a configured backend.
- Local results archive with sorting, search, classification filtering,
  detail view, deletion, and JSON export.
- Two views: **Upload & Scan** and **Scanned Results**.

The results archive uses `localStorage`. It is not synchronized with a backend.
If the browser storage quota is reached, Visual Scan retries the new record
without its image preview. Existing records and previews are never removed
automatically.

## OCR model profiles

| Profile | Official repository | Intended trade-off |
| --- | --- | --- |
| `fast` | `tesseract-ocr/tessdata_fast` | Smallest and fastest integer LSTM data; the default profile. |
| `standard` | `tesseract-ocr/tessdata` | Integer LSTM plus legacy data; Visual Scan uses its LSTM engine through OEM 1. |
| `best` | `tesseract-ocr/tessdata_best` | Larger floating-point LSTM data for the highest available recognition quality, with slower startup and OCR. |

Only one Tesseract worker is kept in memory. Repeating OCR with the same
profile and language reuses it. Changing either selection terminates the
worker; a later worker uses the profile-specific browser cache
(`visual-scan-v5-fast`, `visual-scan-v5-standard`, or
`visual-scan-v5-best`).

The application never silently changes the selected profile, downloads
traineddata from a CDN, or claims that another profile was used. If a selected
combination is absent, it displays:

```text
The selected OCR model is not installed.
Run the model download script or choose an available profile.
```

## Install OCR models

Node.js 18 or newer is required for the setup scripts. There are no package
dependencies and no build step.

Download one or more uncompressed `.traineddata` files from the official
Tesseract repositories:

```bash
node scripts/download-ocr-models.mjs fast eng
node scripts/download-ocr-models.mjs standard rus
node scripts/download-ocr-models.mjs best eng rus
```

The equivalent npm command is:

```bash
npm run ocr:download -- fast eng rus
```

Existing files are retained. Add `--force` to replace them explicitly:

```bash
node scripts/download-ocr-models.mjs fast eng --force
```

Models are installed under:

```text
frontend/assets/tessdata/
├── fast/
├── standard/
└── best/
```

The model files and generated `manifest.json` are machine-local and ignored by
Git. After every download, the downloader runs the verifier and regenerates
the manifest. You can also run it directly:

```bash
node scripts/verify-ocr-models.mjs
```

or:

```bash
npm run ocr:verify
```

The manifest lists installed base languages only. `eng+rus` is available at
runtime only when both `eng.traineddata` and `rus.traineddata` exist in the
same profile directory. A missing manifest does not break image loading,
Canvas tools, local storage, or optional backend calls; OCR selectors remain
unavailable until models are installed and the manifest is generated.

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

Browser/runtime settings have one source of truth:

```text
frontend/config.js
```

The backend URL defaults to `http://localhost:8000`. Change
`CONFIG.backendUrl` when the backend runs elsewhere. Every application backend
request is implemented in `frontend/utils/api.js`.

The environment-neutral OCR registry lives in `frontend/ocrProfiles.js`.
Browser code and both Node.js setup scripts import it, so profile identifiers,
official repositories, directories, and supported languages cannot drift
between runtime and setup.

Static OCR assets are intentionally handled by `frontend/utils/ocr.js`:
`manifest.json` and local traineddata are not backend API requests.

## Image limits and errors

The frontend accepts:

- MIME types `image/jpeg`, `image/png`, and `image/webp`;
- files up to 20 MB;
- decoded images up to 25 megapixels.

The interface reports unsupported formats, oversized or invalid images,
camera errors, OCR failures, missing model combinations, and an unavailable
backend. Backend availability does not affect upload, camera capture, Canvas
preprocessing, browser OCR, or the local results archive.

The backend indicator reports network reachability only. A received HTTP error
still means the backend is reachable; only a network failure or timeout marks
it as unavailable. AI request errors are displayed separately.

## Pinned browser dependencies

The Tesseract browser script and worker use `5.1.1`; the WebAssembly core uses
`tesseract.js-core` `5.1.1`. All CDN URLs specify exact versions:

```text
https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js
https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/worker.min.js
https://cdn.jsdelivr.net/npm/tesseract.js-core@5.1.1
```

Traineddata never falls back to a CDN. It is loaded uncompressed from the
selected local profile with `gzip: false` and OEM 1.

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
│   ├── assets/
│   │   └── tessdata/
│   │       ├── fast/
│   │       ├── standard/
│   │       └── best/
│   ├── utils/
│   │   ├── imageUtils.js
│   │   ├── ocr.js
│   │   ├── api.js
│   │   └── store.js
│   ├── index.html
│   ├── styles.css
│   ├── app.js
│   ├── config.js
│   └── ocrProfiles.js
├── scripts/
│   ├── download-ocr-models.mjs
│   └── verify-ocr-models.mjs
├── public/
│   └── sample-docs/
├── package.json
├── .gitignore
└── README.md
```

### Module responsibilities

- `app.js` connects the interface, state, and user actions.
- `config.js` contains browser/runtime URLs and safety limits.
- `ocrProfiles.js` is the shared pure OCR registry.
- `imageUtils.js` contains Canvas image operations.
- `ocr.js` owns availability, the active Tesseract worker, and OCR progress.
- `api.js` is the only backend HTTP transport module.
- `store.js` owns the browser-local results archive, filtering, and sorting.
