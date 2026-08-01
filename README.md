# Visual Scan

A browser-based document scanner with Canvas preprocessing, client-side and server-side OCR, optional AI analysis, and a persistent FastAPI + SQLite backend.

Visual Scan accepts images, camera captures, and PDFs. Images can be corrected directly in the browser before OCR, recognized either by Tesseract.js or server Tesseract, analyzed by an OpenAI-compatible provider, edited, and saved to a per-user results archive.

· [Русская версия](README_RU.md)

## 📋 TOC

- [🚀 Quick start](#-quick-start)
- [📝 About](#-about)
- [✨ Features](#-features)
- [🔄 Workflow](#-workflow)
- [🔎 OCR engines](#-ocr-engines)
- [🤖 AI analysis](#-ai-analysis)
- [🔐 Authentication and archive](#-authentication-and-archive)
- [📄 Demo documents](#-demo-documents)
- [🌐 Backend API](#-backend-api)
- [🧰 Technology stack](#-technology-stack)
- [🧪 Tests and checks](#-tests-and-checks)
- [📁 Project structure](#-project-structure)
- [⚠️ Notes](#️-notes)
- [🧑‍💻 Author](#-author)

## 🚀 Quick start

### Prerequisites

- Python `3.11+`
- Tesseract `5` for server-side OCR
- a modern browser
- Node.js `18+` only for OCR model scripts and frontend tests
- an OpenAI-compatible endpoint and API key only if AI analysis is enabled

The repository already contains the Fast English and Russian browser OCR models. No model download is required for the default Browser OCR profile.

### Clone and install

#### Windows (Git Bash)

```bash
git clone https://01.tomorrow-school.ai/git/nyestaye/visual-scan
cd visual-scan

python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
```

#### macOS / Linux

```bash
git clone https://01.tomorrow-school.ai/git/nyestaye/visual-scan
cd visual-scan

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Install native Tesseract

Ubuntu / Debian:

```bash
sudo apt install tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-rus \
    tesseract-ocr-deu \
    tesseract-ocr-fra \
    tesseract-ocr-spa
```

macOS:

```bash
brew install tesseract tesseract-lang
```

On Windows, install a Tesseract 5 build and add its directory to `PATH`. If the executable is not on `PATH`, set its full path in `backend/.env`:

```dotenv
VISUAL_SCAN_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

Check the installation:

```bash
tesseract --version
tesseract --list-langs
```

### Start the backend

```bash
python -m uvicorn app.main:app --app-dir backend --reload
```

The API is available at:

```text
http://localhost:8000
http://localhost:8000/docs
```

### Start the frontend

Open another terminal from the repository root:

```bash
python -m http.server 5500
```

Then open:

```text
http://localhost:5500/frontend/index.html
```

Do not open `frontend/index.html` through `file://`; browser ES modules require an HTTP origin.

### First run

Browser OCR works without an account. Server OCR, PDF OCR, AI analysis, saving, and the Scanned Results archive require a local Visual Scan account.

1. Open **Upload & Scan**.
2. Drop an image, select a file, use a demo document, or capture from the camera.
3. Adjust rotation, deskew, crop, grayscale, threshold, or invert if needed.
4. Run Browser OCR immediately, or register/sign in and use Server OCR.
5. Edit the recognized text.
6. Optionally run AI analysis.
7. Save the result.
8. Open **Scanned Results** to search, filter, inspect, export, or delete saved scans.

Registration is local to the application and does not require an external account.

## 📝 About

Visual Scan separates browser document handling from backend processing.

Images stay interactive in the frontend: the original image is loaded into Canvas, transformations are applied locally, and Browser OCR can run without the backend. Server OCR receives the processed Canvas image.

PDFs use a separate server pipeline. The original PDF is validated, preflighted, rendered page by page with PDFium, optionally preprocessed with Pillow, and recognized sequentially with Tesseract.

The backend also provides:

- optional OpenAI-compatible document classification and summarization;
- structured field extraction;
- local registration and session authentication;
- an owner-scoped SQLite results archive;
- search, filtering, sorting, pagination, export, and deletion;
- strict validation and bounded processing limits.

The backend uses feature-oriented modules instead of a flat `routes/` directory. Persistent results are stored in SQLite instead of a JSON log file.

## ✨ Features

### Document input and preprocessing

- JPEG, PNG, WebP, and PDF upload;
- drag-and-drop input;
- camera capture;
- bundled synthetic demo documents;
- 90° rotation;
- fine deskew;
- crop;
- grayscale;
- binary threshold;
- invert;
- editable OCR text.

### OCR

- Tesseract.js Browser OCR;
- system Tesseract Server OCR through `pytesseract`;
- server-side PDF OCR through `pypdfium2`;
- English;
- Russian;
- English + Russian;
- German;
- French;
- Spanish;
- Fast, Standard, and Best browser model profiles;
- OCR confidence, word count, language, engine, and profile metadata.

### AI and results

- OpenAI-compatible classification;
- concise document summary;
- confidence value returned by the model;
- tags;
- structured label/value fields;
- fixed document taxonomy;
- SQLite results archive;
- text search;
- classification filter;
- deterministic sorting and pagination;
- detail view;
- JSON export;
- single-record and full-archive deletion.

### Security and reliability

- Argon2 password hashing;
- opaque server-side sessions;
- HttpOnly session cookie;
- CSRF protection for authenticated mutations;
- per-user scan ownership;
- cross-user `404` isolation;
- request deadlines and input limits;
- stale-request and identity-change guards;
- safe provider and storage errors without leaking secrets or local paths.

### Extra features

- server-side OCR in addition to browser OCR;
- persistent SQLite storage;
- structured field extraction;
- multilingual OCR;
- PDF OCR;
- user authentication and owner-scoped results;
- verified synthetic sample corpus;
- automated backend and frontend quality checks.

## 🔄 Workflow

```text
                              +----------------------+
                              |  Image / camera      |
                              +----------+-----------+
                                         |
                                         v
                              +----------------------+
                              | Canvas preprocessing |
                              | rotate / crop / etc. |
                              +-----+-----------+----+
                                    |           |
                      Browser OCR   |           |   Server OCR
                                    v           v
                            +-----------+   +-----------+
                            |Tesseract.js|   | FastAPI   |
                            +-----+-----+   | Tesseract |
                                  |         +-----+-----+
                                  +-----+---------+
                                        |
                                        v
                              +----------------------+
                              | Editable OCR text    |
                              +----------+-----------+
                                         |
                                 optional|
                                         v
                              +----------------------+
                              | AI analysis          |
                              | class / summary /    |
                              | tags / fields        |
                              +----------+-----------+
                                         |
                                         v
                              +----------------------+
                              | SQLite scan archive  |
                              +----------------------+

    PDF -> FastAPI -> PDFium render -> Pillow preprocessing -> Tesseract
                                                |
                                                +-> editable text -> AI -> archive
```

Browser-only image work remains usable when the backend is unavailable. Protected server operations require an authenticated session.

## 🔎 OCR engines

| Property            | Browser OCR                         | Server OCR                             |
| ------------------- | ----------------------------------- | -------------------------------------- |
| Engine              | Tesseract.js `5.1.1`                | system Tesseract through `pytesseract` |
| Backend required    | no                                  | yes                                    |
| Sign-in required    | no                                  | yes                                    |
| Image input         | current processed Canvas            | current processed Canvas PNG           |
| PDF input           | not used                            | original PDF                           |
| Image preprocessing | interactive Canvas tools            | already processed by Canvas            |
| PDF preprocessing   | —                                   | none, grayscale, or threshold          |
| Language data       | local frontend `traineddata`        | system-installed Tesseract languages   |
| Image preview       | interactive Canvas                  | same frontend Canvas                   |
| PDF preview         | filename and document metadata card | pages are rendered server-side for OCR |

### Browser OCR profiles

| Profile    | Tesseract data source         | Intended use                                  |
| ---------- | ----------------------------- | --------------------------------------------- |
| `fast`     | `tesseract-ocr/tessdata_fast` | smallest and fastest; default profile         |
| `standard` | `tesseract-ocr/tessdata`      | larger general-purpose LSTM data              |
| `best`     | `tesseract-ocr/tessdata_best` | largest model, slower startup and recognition |

The repository tracks:

```text
frontend/assets/tessdata/fast/eng.traineddata
frontend/assets/tessdata/fast/rus.traineddata
frontend/assets/tessdata/manifest.json
```

A fresh clone therefore supports Fast English, Russian, and English + Russian Browser OCR immediately.

Optional models can be installed with:

```bash
npm run ocr:download -- standard eng rus
npm run ocr:download -- best eng rus
```

Verify the local model inventory:

```bash
npm run ocr:verify
```

Optional Standard and Best model files are ignored by Git. The verifier regenerates `manifest.json` from all locally installed models, so running it with additional local models can modify the tracked manifest in the working tree.

The browser model data is local, but the pinned Tesseract.js script, worker, and WebAssembly core are loaded from jsDelivr.

## 🤖 AI analysis

AI analysis is optional and disabled by default.

Copy the example environment file:

```bash
cp backend/.env.example backend/.env
```

Configure an OpenAI-compatible provider:

```dotenv
VISUAL_SCAN_AI_ENABLED=true
VISUAL_SCAN_AI_BASE_URL=https://provider.example/v1
VISUAL_SCAN_AI_API_KEY=replace-with-your-key
VISUAL_SCAN_AI_MODEL=document-model
VISUAL_SCAN_AI_PROVIDER_NAME=openai-compatible
VISUAL_SCAN_AI_RESPONSE_FORMAT=json_object
```

For a local provider without authentication, `VISUAL_SCAN_AI_API_KEY` may be empty.

The backend sends:

- sanitized filename;
- OCR language;
- OCR text.

The original image or PDF is not sent to the AI provider.

The provider must return one JSON object containing:

```json
{
  "classification": "contract",
  "confidence": 0.93,
  "summary": "Short document summary.",
  "tags": ["legal", "employment"],
  "fields": [
    {
      "label": "Effective date",
      "value": "2026-07-30"
    }
  ]
}
```

Supported classifications:

```text
invoice
receipt
contract
letter
form
report
statement
identity_document
certificate
business_card
note
other
```

`json_object` sends the OpenAI-compatible JSON response-format parameter. `prompt_only` is available for providers that do not implement it.

Analysis is never saved automatically. The user can review or edit OCR text first, run analysis, then explicitly save the current result.

## 🔐 Authentication and archive

Visual Scan uses opaque server-side sessions rather than JWTs.

Registration and login create an HttpOnly, SameSite=Lax session cookie. The raw session token is never stored in SQLite; only its SHA-256 digest is persisted. Passwords are hashed with Argon2.

Authenticated unsafe requests also require an in-memory CSRF token. Server OCR, PDF OCR, AI analysis, saving, archive mutations, and legacy-archive claim are protected operations.

The archive stores:

```text
filename
scanned_at
full OCR text
AI classification / confidence / summary / tags / fields
OCR engine / language / profile / confidence / word count
```

It does not store uploaded originals, PDFs, or thumbnails.

Every scan belongs to one user. A valid scan UUID owned by another account is exposed as the same `404` as a missing scan.

The **Scanned Results** view supports:

- full-text search;
- classification filtering;
- sorting;
- pagination;
- asynchronous detail loading;
- JSON export;
- delete one;
- clear current user's archive.

Legacy browser-only records are kept separate and can only be exported or deleted explicitly. Old pre-auth SQLite records use a separate one-time claim flow.

## 📄 Demo documents

The repository includes six synthetic documents under `public/sample-docs/`.

| Sample                  | Format | Language          | Main purpose                       |
| ----------------------- | ------ | ----------------- | ---------------------------------- |
| Clean invoice           | PNG    | English           | high-contrast Browser OCR          |
| Compressed receipt      | JPEG   | English           | compression and image adjustments  |
| Skewed Russian contract | PNG    | Russian           | deskew and Russian OCR             |
| Bilingual letter        | PNG    | English + Russian | combined-language OCR              |
| Low-contrast note       | PNG    | English           | grayscale and threshold correction |
| Two-page statement      | PDF    | English           | sequential multi-page Server OCR   |

All names, organizations, identifiers, dates, amounts, and transactions in the corpus are fictional. Provenance is documented in `public/sample-docs/SOURCES.md`.

The fixtures use the same validation and file-loading path as user uploads. Loading a sample does not automatically run OCR, AI analysis, or save anything.

Verify the corpus:

```bash
npm run samples:verify
```

## 🌐 Backend API

FastAPI Swagger UI:

```text
http://localhost:8000/docs
```

### Endpoints

| Method   | Endpoint                  | Auth          | Purpose                                     |
| -------- | ------------------------- | ------------- | ------------------------------------------- |
| `GET`    | `/api/health`             | no            | health and configured AI availability       |
| `POST`   | `/api/auth/register`      | no            | create a local user and session             |
| `POST`   | `/api/auth/login`         | no            | authenticate and rotate the session         |
| `GET`    | `/api/auth/session`       | session check | inspect current session                     |
| `POST`   | `/api/auth/logout`        | yes           | invalidate current session                  |
| `POST`   | `/api/ocr/recognize`      | yes           | OCR a JPEG, PNG, or WebP image              |
| `POST`   | `/api/ocr/pdf/recognize`  | yes           | OCR a PDF sequentially                      |
| `POST`   | `/api/ai/analyze`         | yes           | classify and summarize OCR text             |
| `POST`   | `/api/scans`              | yes           | save one scan                               |
| `GET`    | `/api/scans`              | yes           | search, filter, sort, and paginate scans    |
| `GET`    | `/api/scans/{scan_id}`    | yes           | load full scan details                      |
| `DELETE` | `/api/scans/{scan_id}`    | yes           | delete one owned scan                       |
| `DELETE` | `/api/scans`              | yes           | clear the current user's archive            |
| `GET`    | `/api/scans/legacy`       | yes           | inspect claimable pre-auth archive metadata |
| `POST`   | `/api/scans/legacy/claim` | yes           | claim legacy records once                   |

Authenticated mutations require the session cookie, exact allowed `Origin`, and `X-CSRF-Token`.

### Health

```http
GET /api/health
```

```json
{
  "status": "ok",
  "ai_available": true,
  "provider": "openai-compatible"
}
```

Health reports configuration only; it does not call the external AI provider.

### Server image OCR

```http
POST /api/ocr/recognize
Content-Type: multipart/form-data
```

Fields:

```text
file
language=eng|rus|eng+rus|deu|fra|spa
preprocessing=none|grayscale|threshold
threshold=0..255
```

Example response:

```json
{
  "filename": "invoice.png",
  "text": "Recognized text",
  "confidence": 91.25,
  "words": 2,
  "language": "eng",
  "preprocessing": "none",
  "threshold": null,
  "width": 1240,
  "height": 1754,
  "format": "PNG",
  "engine": "tesseract"
}
```

### PDF OCR

```http
POST /api/ocr/pdf/recognize
Content-Type: multipart/form-data
```

Additional optional field:

```text
password
```

The response contains joined document text plus page-level text, confidence, word count, and rendered dimensions.

### AI analysis

```http
POST /api/ai/analyze
Content-Type: application/json
```

Example request:

```json
{
  "filename": "contract.jpg",
  "text": "Recognized document text...",
  "language": "eng"
}
```

### Results archive

```http
POST   /api/scans
GET    /api/scans
GET    /api/scans/{scan_id}
DELETE /api/scans/{scan_id}
DELETE /api/scans
```

List parameters:

```text
q
classification
sort=scanned_at|filename|classification|confidence
order=asc|desc
limit=1..200
offset
```

SQLite is stored by default at:

```text
backend/data/visual-scan.db
```

The database directory is created automatically. SQLite uses WAL and `synchronous=FULL`.

## 🧰 Technology stack

| Layer          | Technology                                            |
| -------------- | ----------------------------------------------------- |
| Frontend       | HTML, CSS, Vanilla JavaScript ES modules              |
| Image tools    | Canvas API                                            |
| Browser OCR    | Tesseract.js `5.1.1`                                  |
| Backend        | Python `3.11+`, FastAPI, Uvicorn                      |
| Server OCR     | Tesseract, `pytesseract`, Pillow                      |
| PDF rendering  | `pypdfium2`                                           |
| AI transport   | `httpx` with an OpenAI-compatible `/chat/completions` |
| Validation     | Pydantic / `pydantic-settings`                        |
| Storage        | SQLite with WAL                                       |
| Authentication | Argon2, opaque HttpOnly sessions, CSRF                |
| Backend tests  | pytest, Ruff                                          |
| Frontend tests | Node.js built-in test runner                          |
| CI             | GitHub Actions                                        |

There is no frontend build step and no npm runtime dependency installation.

## 🧪 Tests and checks

### Development installation

```bash
python -m pip install -e "./backend[dev]"
```

`requirements.txt` contains runtime dependencies. `backend/pyproject.toml` contains the backend package metadata, the same runtime constraints, and development extras. A regression test keeps the runtime dependency lists synchronized.

### Backend

```bash
python -m pytest backend/tests
python -m ruff check backend
python -m ruff format --check backend
python -m compileall backend/app backend/tests
python backend/scripts/generate_dependency_graph.py --check
```

### Frontend and fixtures

```bash
npm run ocr:verify
npm run samples:verify
npm test
```

The backend suite covers API contracts, validation, OCR pipelines, PDF rendering, SQLite schema and concurrency, authentication, ownership, AI response parsing, and dependency boundaries.

AI provider protocol tests use `httpx.MockTransport`. They verify request payloads, Authorization handling, response parsing, timeouts, status mapping, and malformed responses without calling a live external endpoint or requiring an API key.

Frontend tests cover API transport, archive behavior, authentication state transitions, cross-tab identity synchronization, camera races, OCR model availability, samples, and legacy storage behavior.

GitHub Actions runs backend tests on Python `3.11` and `3.14`, backend quality checks on Python `3.11`, and frontend/model/sample checks on Node.js `24`.

## 📁 Project structure

```text
visual-scan/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── router.py
│   │   ├── core/
│   │   │   └── config.py
│   │   ├── features/
│   │   │   ├── analysis/
│   │   │   ├── auth/
│   │   │   ├── health/
│   │   │   ├── ocr/
│   │   │   └── scans/
│   │   ├── storage/
│   │   ├── factory.py
│   │   └── main.py
│   ├── data/
│   ├── tests/
│   ├── .env.example
│   ├── ARCHITECTURE.md
│   ├── DEPENDENCY_GRAPH.md
│   ├── module-map.json
│   └── pyproject.toml
├── frontend/
│   ├── assets/
│   │   └── tessdata/
│   │       ├── fast/
│   │       ├── standard/
│   │       ├── best/
│   │       └── manifest.json
│   ├── utils/
│   ├── app.js
│   ├── config.js
│   ├── index.html
│   ├── intakeContract.js
│   ├── module-map.json
│   ├── ocrProfiles.js
│   └── styles.css
├── public/
│   └── sample-docs/
├── scripts/
│   ├── download-ocr-models.mjs
│   ├── verify-ocr-models.mjs
│   └── verify-sample-docs.mjs
├── tests/
├── .github/
│   └── workflows/
│       └── ci.yml
├── AGENTS.md
├── package.json
├── requirements.txt
├── README.md
└── README_RU.md
```

Backend responsibilities are feature-local: each feature owns its router, schemas, service layer, implementation details, and errors. `backend/app/api/router.py` only composes public routers.

Frontend state and UI orchestration remain in `frontend/app.js`; reusable transport, auth, OCR, image, sample, camera, and archive logic lives in `frontend/utils/`.

`backend/module-map.json` and `frontend/module-map.json` document ownership and dependency boundaries. `backend/DEPENDENCY_GRAPH.md` is generated from the backend map and checked for drift.

## ⚠️ Notes

- Browser OCR ships Fast English and Russian model data, but the Tesseract.js runtime and WebAssembly core are loaded from pinned jsDelivr URLs.
- Standard and Best browser models are optional local files and are not committed.
- Server OCR requires the native Tesseract executable and the selected language packs.
- PDF files use a metadata card in the frontend rather than client-side page rendering; PDF pages are rendered on the server for OCR.
- Server OCR, PDF OCR, AI analysis, saving, and the server results archive require sign-in.
- AI is disabled until a provider is configured in `backend/.env`.
- The AI provider receives OCR text and metadata, not the source document image.
- Uploaded originals and thumbnails are not stored by the application.
- New results are stored in the owner-scoped SQLite archive, not in browser `localStorage`.
- `backend/.env`, the SQLite database, and optional local OCR models are ignored by Git.
- The documented local browser topology uses `localhost` for both frontend and backend; avoid mixing `localhost` and `127.0.0.1` in the same authenticated browser session.

## 🧑‍💻 Author
Nazar Yestayev (@nyestaye)
