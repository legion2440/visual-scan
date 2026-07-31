# Visual Scan

Visual Scan combines a standalone Vanilla JavaScript document-scanning
frontend with a FastAPI backend. The frontend accepts a
document image or camera capture, lets the user prepare the page on a
`<canvas>`, and extracts editable text with Tesseract.js in the browser.

The backend reports health and provides independent server-side OCR with
in-memory Pillow preprocessing, PDFium rendering, and the system Tesseract
executable. It also provides optional AI document classification, summaries,
tags, and structured fields through a configured OpenAI-compatible provider.
It also exposes a SQLite-backed API for saving scan text and analysis/OCR
metadata. Authentication and Docker are not implemented yet.

## Features

- JPEG, PNG, and WebP upload through a file picker or drag-and-drop.
- Camera capture on supported browsers.
- Canvas preview with 90° rotation, fine deskew, crop, grayscale, threshold,
  and invert controls.
- Client-side OCR with explicit `fast`, `standard`, and `best` local model
  profiles.
- English, Russian, English + Russian, German, French, and Spanish selections.
- Editable extracted text.
- Optional AI analysis through a configurable OpenAI-compatible provider.
- FastAPI application factory with CORS and `GET /api/health`.
- Independent server-side Tesseract OCR for JPEG, PNG, and WebP uploads.
- Sequential server-side PDF OCR with page-level text and metadata.
- Server preprocessing modes: none, grayscale, and binary threshold.
- SQLite scans API with search, filtering, deterministic sorting, pagination,
  detail retrieval, deletion, and archive cleanup.
- Local results archive with sorting, search, classification filtering,
  detail view, deletion, and JSON export.
- Two views: **Upload & Scan** and **Scanned Results**.

The current frontend results archive still uses `localStorage`; frontend
integration with the server archive is reserved for the next step. If browser
storage quota is reached, Visual Scan retries the new record without its image
preview. Existing records and previews are never removed automatically.

## Browser OCR and server OCR

| | Browser OCR | Server OCR |
| --- | --- | --- |
| Engine | Tesseract.js worker | System Tesseract through pytesseract |
| Input | Current Canvas image | Multipart JPEG, PNG, WebP, or PDF upload |
| Preprocessing | Interactive Canvas controls | Requested none, grayscale, or threshold mode |
| Models | Local frontend `traineddata` profiles | Languages installed with system Tesseract |
| Backend required | No | Yes |

The existing frontend continues to use browser OCR and is not yet wired to the
server OCR endpoints. The two paths are intentionally independent in this
step.

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

The Python package installs Pillow and pytesseract, but pytesseract does not
bundle the native Tesseract executable or language data. Install Tesseract
separately:

```bash
# Ubuntu/Debian, including the languages exposed by the API
sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus \
  tesseract-ocr-deu tesseract-ocr-fra tesseract-ocr-spa

# macOS
brew install tesseract tesseract-lang
```

On Windows, use a Tesseract 5 installer such as the builds referenced by the
[official Tesseract installation guide](https://tesseract-ocr.github.io/tessdoc/Installation.html).
Add the installation directory to `PATH`, or set
`VISUAL_SCAN_TESSERACT_CMD` to the full executable path. Ensure the `eng`,
`rus`, `deu`, `fra`, and `spa` traineddata files are installed for the
languages you intend to use.

Verify the executable and installed language packs before starting the API:

```bash
tesseract --version
tesseract --list-langs
```

If `VISUAL_SCAN_TESSERACT_CMD` points to an executable outside `PATH`, run the
same checks with that full executable path.

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
- documented host and port;
- optional Tesseract executable path;
- per-call image OCR timeout;
- image upload byte and decoded pixel limits;
- PDF upload byte, page, per-page pixel, and total pixel limits;
- PDF render DPI and whole-document timeout;
- optional OpenAI-compatible AI endpoint, model, API key, provider label,
  response mode, deadline, and input/output limits;
- SQLite archive path, bounded busy timeout, and maximum stored text length.

`VISUAL_SCAN_CORS_ORIGINS` is a JSON array:

```dotenv
VISUAL_SCAN_CORS_ORIGINS=["http://localhost:5500","http://127.0.0.1:5500"]
VISUAL_SCAN_TESSERACT_CMD=
VISUAL_SCAN_OCR_TIMEOUT_SECONDS=45
VISUAL_SCAN_MAX_IMAGE_BYTES=20971520
VISUAL_SCAN_MAX_IMAGE_PIXELS=25000000
VISUAL_SCAN_MAX_PDF_BYTES=52428800
VISUAL_SCAN_MAX_PDF_PAGES=20
VISUAL_SCAN_MAX_PDF_PAGE_PIXELS=25000000
VISUAL_SCAN_MAX_PDF_TOTAL_PIXELS=200000000
VISUAL_SCAN_PDF_RENDER_DPI=300
VISUAL_SCAN_PDF_TIMEOUT_SECONDS=180
VISUAL_SCAN_AI_ENABLED=false
VISUAL_SCAN_AI_BASE_URL=
VISUAL_SCAN_AI_API_KEY=
VISUAL_SCAN_AI_MODEL=
VISUAL_SCAN_AI_PROVIDER_NAME=openai-compatible
VISUAL_SCAN_AI_TIMEOUT_SECONDS=45
VISUAL_SCAN_AI_MAX_INPUT_CHARS=50000
VISUAL_SCAN_AI_MAX_OUTPUT_TOKENS=1200
VISUAL_SCAN_AI_RESPONSE_FORMAT=json_object
VISUAL_SCAN_SCANS_DATABASE_PATH=data/visual-scan.db
VISUAL_SCAN_SCANS_DATABASE_BUSY_TIMEOUT_MS=5000
VISUAL_SCAN_SCANS_MAX_TEXT_CHARS=250000
```

The default allowed frontend origins are `http://localhost:5500` and
`http://127.0.0.1:5500`.

## AI document analysis

AI is disabled by default. To use a local OpenAI-compatible server, configure
`backend/.env` with its base API path and model:

```dotenv
VISUAL_SCAN_AI_ENABLED=true
VISUAL_SCAN_AI_BASE_URL=http://127.0.0.1:1234/v1
VISUAL_SCAN_AI_API_KEY=
VISUAL_SCAN_AI_MODEL=local-document-model
VISUAL_SCAN_AI_PROVIDER_NAME=local-llm
VISUAL_SCAN_AI_RESPONSE_FORMAT=json_object
```

For a remote provider, use its HTTPS base URL and API key:

```dotenv
VISUAL_SCAN_AI_ENABLED=true
VISUAL_SCAN_AI_BASE_URL=https://provider.example/v1
VISUAL_SCAN_AI_API_KEY=replace-with-a-secret
VISUAL_SCAN_AI_MODEL=document-model
VISUAL_SCAN_AI_PROVIDER_NAME=remote-provider
```

When AI is enabled, the base URL and model are required. The API key remains
optional for local servers and is stored as a Pydantic `SecretStr`.
`json_object` sends `response_format={"type":"json_object"}`;
`prompt_only` supports servers that do not implement that parameter. Visual
Scan never retries automatically or silently changes response mode.
The base URL is canonicalized during settings validation and must not contain
credentials, whitespace or control characters, a query, or a fragment.

The provider receives the sanitized filename, OCR language, and OCR text. It
does not receive the source image. Analysis results are returned to the
frontend and are not saved automatically; a client can explicitly include
them in a later `POST /api/scans` request. The returned confidence is the
model's self-assessment, not a statistically calibrated probability.

The fixed classification taxonomy is:

```text
invoice, receipt, contract, letter, form, report, statement,
identity_document, certificate, business_card, note, other
```

The 45-second backend deadline covers the whole provider request. The frontend
waits 60 seconds so a backend 504 response arrives before the browser aborts
the request. Health reports configured availability only and never probes the
external provider.

## Frontend configuration

Browser/runtime settings have one source of truth:

```text
frontend/config.js
```

The backend URL defaults to `http://localhost:8000`. Change
`CONFIG.backendUrl` when the backend runs elsewhere. Every application backend
request is implemented in `frontend/utils/api.js`. The general request timeout
is 60 seconds; health uses its separate four-second timeout.

The environment-neutral OCR registry lives in `frontend/ocrProfiles.js`.
Browser code and both Node.js setup scripts import it, so profile identifiers,
official repositories, directories, and supported languages cannot drift
between runtime and setup.

Static OCR assets are intentionally handled by `frontend/utils/ocr.js`:
`manifest.json` and local traineddata are not backend API requests.

## Input limits and errors

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

The server OCR endpoint applies its own configurable byte and pixel limits. It
also rejects an empty or corrupt upload, an unsupported MIME type, and any
mismatch between the declared MIME type and the decoded image format. Pillow
decompression-bomb warnings and errors are returned as HTTP 413.

The PDF endpoint accepts only a normalized `application/pdf` MIME type. Its
defaults are 50 MB, 20 pages, 25 million rendered pixels per page, 200 million
rendered pixels across the document, 300 DPI, and a 180-second whole-document
deadline. Page dimensions must be finite and positive. PDF preflight validates
all page and total limits before the first OCR call; each rendered image must
then match its preflight dimensions exactly. Both stages compute
`scale = dpi / 72` once and use `ceil(points * scale)`.

Visual Scan does not save uploaded originals or create application-managed
temporary files. FastAPI's multipart parser and pytesseract may use system
temporary storage; pytesseract cleans up the files it creates after OCR.
Strict zero-disk upload handling is outside this step.

AI analysis accepts at most 50,000 OCR-text characters by default and rejects
whitespace-only input. Provider rate limits return HTTP 429, malformed or
schema-invalid successful responses return 502, unavailable or rejected
provider configurations return 503, and provider deadlines return 504.
Client-facing failures do not expose the API key, provider response body, base
URL, model internals, traceback, or full OCR text.

The scans API rejects whitespace-only text, embedded null characters, and
strings that are not strict UTF-8/Unicode scalar sequences with HTTP 422. Text
beyond the configured archive limit returns 413. Missing records return 404.
Locked, unavailable, schema-invalid, or corrupt SQLite storage returns a
generic 503 without exposing the database path, SQL, stored row, or full OCR
text. Validation responses do not reflect rejected input values.

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

Health:

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

When AI is enabled and configured, health returns the configured provider
label without making an external request:

```json
{
  "status": "ok",
  "ai_available": true,
  "provider": "local-llm"
}
```

AI document analysis:

```http
POST /api/ai/analyze
Content-Type: application/json
```

Example:

```bash
curl -X POST http://localhost:8000/api/ai/analyze \
  -H "Content-Type: application/json" \
  -d '{"filename":"contract.jpg","text":"Recognized document text...","language":"eng"}'
```

Example response:

```json
{
  "filename": "contract.jpg",
  "classification": "contract",
  "confidence": 0.93,
  "summary": "Employment agreement between the named parties.",
  "tags": ["legal", "employment"],
  "fields": [
    {
      "label": "Effective date",
      "value": "2026-07-30"
    }
  ],
  "provider": "local-llm"
}
```

Supported language identifiers are `eng`, `rus`, `eng+rus`, `deu`, `fra`, and
`spa`. The provider must return exactly one JSON object with a taxonomy value,
confidence from 0 through 1, a non-empty summary of at most 1200 characters,
at most 10 tags, and at most 20 string label/value fields. Markdown fences,
surrounding text, unknown classifications, and extra properties are rejected.

Saved scans:

```http
POST /api/scans
Content-Type: application/json
```

Example:

```bash
curl -i -X POST http://localhost:8000/api/scans \
  -H "Content-Type: application/json" \
  -d '{"filename":"contract.jpg","text":"Full edited OCR text.","analysis":null,"ocr":null}'
```

The response is `201 Created`, includes a relative `Location` header, and
returns the server-generated UUID4 and UTC timestamp:

```json
{
  "id": "a11aa4fd-3354-4af1-81b5-740ef31afad2",
  "filename": "contract.jpg",
  "scanned_at": "2026-07-31T06:30:00Z",
  "text": "Full edited OCR text.",
  "analysis": null,
  "ocr": null
}
```

`analysis` may contain the normal classification, confidence, summary, tags,
structured fields, and a provider label. `ocr` may contain `source`
(`browser` or `server`), engine, language, optional profile, confidence, and
word count. Neither object is required. Images, PDFs, thumbnails, client IDs,
timestamps, and snippets are not accepted.

Archive metadata has storage-specific limits: filename 255 characters,
provider and each tag 100, structured-field label 200, structured-field value
5000, OCR engine 100, and OCR profile 50. These limits do not alter the
`visual-scan-analysis-v1` provider contract used by `/api/ai/analyze`; the AI
contract continues to enforce tag and field counts without adding unprompted
per-value length constraints.

Archive operations:

```http
GET /api/scans
GET /api/scans/{scan_id}
DELETE /api/scans/{scan_id}
DELETE /api/scans
```

List parameters are `limit` (1–200, default 50), `offset` (0 through
9223372036854775807), optional `q`, optional taxonomy `classification` or
`unclassified`, `sort` (`scanned_at`, `filename`, `classification`, or
`confidence`), and `order` (`asc` or `desc`). List items contain a
server-calculated snippet but omit full text and structured fields; the detail
endpoint returns both. Deleting an unknown identifier returns 404, while
clearing the archive returns `{"deleted": <count>}`.

The default database is `backend/data/visual-scan.db`. Relative paths are
resolved from `backend/`; absolute deployment paths are allowed. Startup
creates the parent directory, enables WAL with `synchronous=FULL`, runs an
integrity check, and strictly validates schema version 1 and its indexes.
SQLite stores only result text and metadata—never originals or thumbnails.
The archive endpoints are unauthenticated in this step, so the default backend
binding remains `127.0.0.1`; public deployment is unsupported until
authentication is added.

Server-side OCR:

```http
POST /api/ocr/recognize
Content-Type: multipart/form-data
```

Multipart fields:

- `file` — matching JPEG, PNG, or WebP content;
- `language` — `eng`, `rus`, `eng+rus`, `deu`, `fra`, or `spa`; default
  `eng`;
- `preprocessing` — `none`, `grayscale`, or `threshold`; default `none`;
- `threshold` — optional integer from 0 through 255, valid only in threshold
  mode; its threshold-mode default is 160.

Example:

```bash
curl -X POST http://localhost:8000/api/ocr/recognize \
  -F "file=@public/sample-docs/invoice.jpg;type=image/jpeg" \
  -F "language=eng" \
  -F "preprocessing=grayscale"
```

Example response:

```json
{
  "filename": "invoice.jpg",
  "text": "Recognized text",
  "confidence": 91.25,
  "words": 2,
  "language": "eng",
  "preprocessing": "grayscale",
  "threshold": null,
  "width": 1240,
  "height": 1754,
  "format": "JPEG",
  "engine": "tesseract"
}
```

Server-side PDF OCR:

```http
POST /api/ocr/pdf/recognize
Content-Type: multipart/form-data
```

Multipart fields:

- `file` - content declared as `application/pdf`;
- `language` - the same values as image OCR; default `eng`;
- `preprocessing` - `none`, `grayscale`, or `threshold`; default `none`;
- `threshold` - the same conditional value and default as image OCR;
- `password` - optional PDF password.

Example:

```bash
curl -X POST http://localhost:8000/api/ocr/pdf/recognize \
  -F "file=@document.pdf;type=application/pdf" \
  -F "language=eng+rus" \
  -F "preprocessing=grayscale"
```

Example response:

```json
{
  "filename": "document.pdf",
  "text": "First page\n\nSecond page",
  "page_count": 2,
  "language": "eng+rus",
  "preprocessing": "grayscale",
  "threshold": null,
  "render_dpi": 300,
  "pages": [
    {
      "page": 1,
      "text": "First page",
      "confidence": 91.25,
      "words": 2,
      "width": 2480,
      "height": 3508
    },
    {
      "page": 2,
      "text": "Second page",
      "confidence": null,
      "words": 2,
      "width": 3508,
      "height": 2480
    }
  ],
  "format": "PDF",
  "engine": "tesseract"
}
```

PDF pages are preflighted and processed in one-based page order. The endpoint
runs one `pytesseract.image_to_data()` call per page, preserves successful
blank pages with empty text, and joins document text with one blank line
between page results. Rendering uses a white background and includes PDF
annotations. `init_forms()` is intentionally not called, so unflattened
AcroForm and XFA field values may be absent from the rendered image.

PDFium is not thread-safe. Preflight opens and closes its native document
inside one process-wide lock. Each page render separately reopens the
document, detaches an RGB image, and closes all native resources inside one
lock. Pillow preprocessing and Tesseract run after that lock is released.
Every lock wait, the bounded first Tesseract version probe, rendering,
preprocessing, and recognition consume the whole-document deadline.
Non-interruptible in-process PDFium and Pillow calls are checked immediately
before and after their boundaries rather than forcibly terminated mid-call.

Each image request runs one `pytesseract.image_to_data()` recognition call.
The API does not query installed languages before recognition and never falls
back to another language. Before the first recognition, the provider runs a
bounded `tesseract --version` probe and primes pytesseract's version cache.
Consequently, this contract promises one recognition call but not exactly one
native subprocess on the first OCR request.

Expected OCR failures use HTTP 400 for empty or corrupt content, 413 for byte,
page, or pixel limits, 415 for unsupported or mismatched formats, 422 for
invalid parameters or unsupported PDF security, 503 for a missing Tesseract
binary or language data, and 504 for a processing deadline. Unexpected
internal render or provider failures return a generic 500 response without
local paths or tracebacks.

## Tests and checks

Run backend tests and static checks from the repository root:

```bash
python -m pytest backend/tests
python -m ruff check backend
python -m ruff format --check backend
python -m compileall backend/app backend/tests
npm test
```

The module-map tests reject duplicate JSON keys, absolute or non-POSIX paths,
parent traversal, paths that resolve outside the repository, and references to
missing files. OCR API tests replace the service dependency, pipeline tests
use in-memory Pillow images and fake providers, PDF renderer tests combine
instrumented PDFium doubles with generated in-memory PDFs, and provider tests
mock `pytesseract.image_to_data()`. Analysis HTTP tests use app-local fake
services, while provider tests use `httpx.MockTransport`; they never require a
real AI server or API key. The test suite never requires the system Tesseract
binary. Scans tests use only SQLite databases under pytest `tmp_path`, exercise
the real application lifespan, and cover schema validation, WAL concurrency,
transactions, Unicode search, deterministic pagination, and safe failures.

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
│   │   │   ├── analysis/
│   │   │   │   ├── router.py
│   │   │   │   ├── schemas.py
│   │   │   │   ├── service.py
│   │   │   │   ├── pipeline.py
│   │   │   │   ├── provider.py
│   │   │   │   ├── prompts.py
│   │   │   │   └── errors.py
│   │   │   ├── health/
│   │   │   │   ├── router.py
│   │   │   │   └── schemas.py
│   │   │   ├── ocr/
│   │   │   │   ├── router.py
│   │   │   │   ├── schemas.py
│   │   │   │   ├── service.py
│   │   │   │   ├── pipeline.py
│   │   │   │   ├── pdf_pipeline.py
│   │   │   │   ├── pdf_renderer.py
│   │   │   │   ├── preprocessing.py
│   │   │   │   ├── provider.py
│   │   │   │   └── errors.py
│   │   │   └── scans/
│   │   │       ├── router.py
│   │   │       ├── schemas.py
│   │   │       ├── service.py
│   │   │       ├── repository.py
│   │   │       ├── schema.py
│   │   │       └── errors.py
│   │   ├── factory.py
│   │   └── main.py
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_analysis_api.py
│   │   ├── test_analysis_pipeline.py
│   │   ├── test_health.py
│   │   ├── test_module_map.py
│   │   ├── test_openai_compatible_provider.py
│   │   ├── test_ocr_api.py
│   │   ├── test_ocr_pipeline.py
│   │   ├── test_pdf_ocr_api.py
│   │   ├── test_pdf_ocr_pipeline.py
│   │   ├── test_pdf_renderer.py
│   │   ├── test_scans_api.py
│   │   ├── test_scans_service.py
│   │   ├── test_sqlite_scan_repository.py
│   │   └── test_tesseract_provider.py
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
- `backend/app/features/analysis` owns AI document analysis and the
  OpenAI-compatible provider boundary.
- `backend/app/features/health` owns the health contract and endpoint.
- `backend/app/features/ocr` owns server-side image and PDF validation,
  serialized PDFium rendering, preprocessing, and Tesseract recognition.
- `backend/app/features/scans` owns immutable scan contracts, archive
  invariants, search/list behavior, and SQLite persistence.
- `backend/module-map.json` is the backend navigation and ownership index.
- `app.js` connects the interface, state, and user actions.
- `config.js` contains browser/runtime URLs and safety limits.
- `ocrProfiles.js` is the shared pure OCR registry.
- `imageUtils.js` contains Canvas image operations.
- `ocr.js` owns availability, the active Tesseract worker, and OCR progress.
- `api.js` is the only backend HTTP transport module.
- `store.js` owns the browser-local results archive, filtering, and sorting.
