# Visual Scan

Visual Scan combines a standalone Vanilla JavaScript document-scanning
frontend with a minimal FastAPI backend foundation. The frontend accepts a
document image or camera capture, lets the user prepare the page on a
`<canvas>`, and extracts editable text with Tesseract.js in the browser.

The backend currently reports health and establishes the module boundaries for
future OCR, analysis, and storage features. Server-side OCR, AI analysis,
database storage, authentication, and Docker are not implemented yet.

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
- FastAPI application factory with CORS and `GET /api/health`.
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

`shutdown()` is graceful cleanup, not OCR cancellation. It is placed in the
same operation queue as worker creation and recognition. If `createWorker()` or
`recognize()` is already running, that operation finishes first; the active
worker is terminated immediately afterward. User-initiated cancellation is
reserved for a future Cancel control.

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

`--force` replaces the local file but does not invalidate traineddata already
stored by Tesseract.js in the browser's IndexedDB cache. Before the next OCR
run, either clear site data for the frontend origin or change
`CONFIG.ocr.cachePrefix` in `frontend/config.js`. Otherwise the browser may
continue using the previous cached model.

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

## Backend setup and run

Python 3.11 or newer is required. From the repository root, install the backend
and its development tools:

```bash
python -m pip install -e "./backend[dev]"
```

`backend/.env` is optional. When present, it is always loaded relative to the
backend package rather than the current working directory. Start the API:

```bash
python -m uvicorn app.main:app --app-dir backend --reload
```

Check the health contract:

```bash
curl http://localhost:8000/api/health
```

Expected response:

```json
{
  "status": "ok",
  "ai_available": false,
  "provider": null
}
```

## Frontend run

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

With both servers running, the frontend connection indicator reports
`Backend: reachable`.

## Backend configuration

Backend settings use the `VISUAL_SCAN_` environment prefix. Copy values from
`backend/.env.example` into `backend/.env` when overrides are needed. Available
settings cover:

- application name and version;
- environment name;
- API prefix;
- CORS origins;
- documented host and port.

`VISUAL_SCAN_CORS_ORIGINS` is a JSON array:

```dotenv
VISUAL_SCAN_CORS_ORIGINS=["http://localhost:5500","http://127.0.0.1:5500"]
```

The default allowed frontend origins are `http://localhost:5500` and
`http://127.0.0.1:5500`.

## Frontend configuration

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

## Backend API

The implemented endpoint is:

```http
GET /api/health
```

Example response:

```json
{
  "status": "ok",
  "ai_available": false,
  "provider": null
}
```

`POST /api/ai/analyze` is intentionally absent. Until the analysis feature is
implemented, that request receives HTTP 404. The frontend treats a received
HTTP response as proof that the backend is reachable and keeps local image/OCR
processing available.

## Tests and checks

Run backend tests and static checks from the repository root:

```bash
python -m pytest backend/tests
python -m ruff check backend
python -m ruff format --check backend
python -m compileall backend/app backend/tests
```

The module-map tests reject duplicate JSON keys, absolute or non-POSIX paths,
parent traversal, paths that resolve outside the repository, and references to
missing files.

## Structure

```text
visual-scan/
├── AGENTS.md
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── router.py
│   │   ├── core/
│   │   │   └── config.py
│   │   ├── features/
│   │   │   └── health/
│   │   │       ├── router.py
│   │   │       └── schemas.py
│   │   ├── factory.py
│   │   └── main.py
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_health.py
│   │   └── test_module_map.py
│   ├── .env.example
│   ├── ARCHITECTURE.md
│   ├── module-map.json
│   └── pyproject.toml
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
├── tests/
│   └── ocr.test.mjs
├── public/
│   └── sample-docs/
├── package.json
├── .gitignore
└── README.md
```

### Module responsibilities

- `backend/app/factory.py` owns the side-effect-free FastAPI application factory.
- `backend/app/main.py` creates the production ASGI application for Uvicorn.
- `backend/app/api/router.py` composes public feature routers.
- `backend/app/core/config.py` owns environment-backed settings.
- `backend/app/features/health` owns the health contract and endpoint.
- `backend/module-map.json` is the backend navigation and ownership index.
- `app.js` connects the interface, state, and user actions.
- `config.js` contains browser/runtime URLs and safety limits.
- `ocrProfiles.js` is the shared pure OCR registry.
- `imageUtils.js` contains Canvas image operations.
- `ocr.js` owns availability, the active Tesseract worker, and OCR progress.
- `api.js` is the only backend HTTP transport module.
- `store.js` owns the browser-local results archive, filtering, and sorting.
