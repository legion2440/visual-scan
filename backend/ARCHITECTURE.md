# Backend architecture

Visual Scan is organized as a feature-oriented FastAPI application. The
intended flow for features that need every layer is:

```text
router → service → pipeline → provider/repository
```

Layers are created only when real behavior requires them. A small feature such
as health reporting can consist of only a router and its schemas.

## Application assembly

- `app/factory.py` exposes the side-effect-free `create_app()` factory. Tests
  import this module and inject explicit settings without constructing the
  production application or reading `backend/.env`.
- The factory installs the application lifespan. Startup creates the app-local
  analysis initialization lock, constructs the resource-free scans service,
  and bootstraps its SQLite schema in Starlette's thread pool. Shutdown closes
  a lazily created analysis service and its HTTP client. No provider
  connection or database file is created during application construction or
  health reporting.
- `app/main.py` is the production ASGI entry point. It imports the factory and
  creates `app` for Uvicorn.
- `app/api/router.py` composes public feature routers.
- Runtime settings injected by the factory are read from
  `request.app.state.settings`. Feature dependencies must not reload global
  settings and bypass an explicitly configured application instance.
- Request validation errors are serialized without their submitted `input` or
  validator context. This keeps malformed Unicode and sensitive document data
  out of both unsafe response encoding and client-visible validation details.

## Layer responsibilities

- **Router** owns HTTP concerns: routes, status codes, request parsing, and
  response models. It delegates business behavior.
- **Schemas** are the feature's explicit input and output contracts.
- **Service** is the public entry point for feature behavior.
- **Pipeline** coordinates multi-step work without owning external integration
  or persistence details.
- **Provider** communicates with an external system or model.
- **Repository** owns persistence and storage queries.

## Dependency rules

- `app/core` must not depend on `app/features`.
- Features must not import another feature's internal modules.
- Cross-feature collaboration must use a documented public entry point or
  shared contract.
- Routers must not contain provider, persistence, or multi-step orchestration
  logic.
- Pipelines coordinate; providers and repositories perform boundary work.
- New or moved features must be reflected in `backend/module-map.json`.

## OCR request flow

The OCR feature uses every currently defined behavior layer:

```text
HTTP multipart upload
  → OCR router
  → OCR service
    ├→ image pipeline → in-memory image validation/preprocessing → Tesseract provider
    └→ PDF pipeline → serialized PDFium preflight/rendering → preprocessing
       └→ Tesseract provider
```

- The router reads at most the configured byte limit plus one byte, maps
  feature errors to HTTP responses, and moves synchronous OCR work to
  Starlette's thread pool.
- The service is the public feature entry point and owns request-level
  invariants such as filename normalization, exact PDF MIME validation, and
  conditional threshold validation.
- The image pipeline coordinates one preprocessing pass and one provider call.
- Image preprocessing validates the declared MIME type against the decoded image,
  checks pixel limits before full decoding, verifies the image, applies EXIF
  orientation, and returns an in-memory Pillow image.
- The PDF pipeline preflights every page before the first OCR call, then
  sequentially renders, preprocesses, and recognizes one page at a time.
- PDF dimensions compute `scale = dpi / 72` once and then use
  `ceil(points * scale)`, matching pypdfium2's render helper exactly after
  finite, positive size validation. Page and document pixel limits are final
  after preflight; a render whose actual dimensions differ from preflight is
  an internal error.
- Preflight opens, validates, and closes its native PDFium document inside one
  process-wide lock. Each page render separately reopens, renders, detaches to
  Pillow RGB, and closes every native resource inside one lock. No native
  document remains open while Pillow preprocessing or Tesseract runs.
- Every PDFium lock acquisition is bounded by the whole-document deadline.
  PDFium and Pillow calls are checked before and after their non-interruptible
  in-process boundaries rather than being forcibly terminated mid-call.
- PDF rendering uses a white background and includes annotations.
  `init_forms()` is not called, so unflattened AcroForm or XFA values may be
  absent.
- The provider owns each `pytesseract.image_to_data()` call and
  normalizes Tesseract availability, version, and timeout failures. Its first
  version probe, version-cache lock wait, and recognition subprocess share
  one bounded per-call budget.

Visual Scan does not persist uploaded originals or create application-managed
temporary files. FastAPI's multipart parser and pytesseract may use system
temporary storage; pytesseract cleans up the files it creates after OCR.

## Analysis request flow

The analysis feature is independent from OCR execution. It accepts text that
the caller already extracted:

```text
JSON request
  → analysis router
  → async analysis service
  → analysis pipeline
    → versioned prompt builder
    → OpenAI-compatible HTTP provider
    → strict provider-result validation
```

- The service validates the OCR-text character limit, rejects whitespace-only
  input, sanitizes the filename, and injects the configured provider name into
  the public response.
- The prompt builder keeps filename, language, and OCR text in the user
  message. The system prompt fixes the taxonomy and treats all document text
  as untrusted data rather than instructions.
- The provider owns the OpenAI-compatible `/chat/completions` HTTP protocol,
  sends one non-streaming request without retries, and accepts exactly one JSON
  object from `choices[0].message.content`.
- The pipeline validates classification, confidence, summary, tags, and
  structured fields with strict Pydantic contracts. It never repairs an
  invalid response, silently truncates output, or changes an unknown
  classification to `other`.
- One `httpx.AsyncClient` is created lazily per application and shared between
  analysis requests. An app-local `asyncio.Lock` serializes first
  initialization only; provider calls remain concurrent.
- The configured AI timeout is a whole-call deadline enforced with
  `asyncio.timeout()`. HTTPX connect, read, write, and pool timeouts use the
  same budget. Provider timeouts map to HTTP 504.
- Health reads only validated application settings. `ai_available` means AI is
  enabled and configured; it is not a live provider check.
- Provider response bodies, API keys, model internals, and full OCR text are
  not included in client-facing errors or application logs.

## Scans request flow

The scans feature stores immutable OCR results and metadata independently from
the browser archive:

```text
JSON request
  → scans router
  → synchronous scans service in Starlette thread pool
  → SQLite repository
```

- The service is the public feature entry point. It sanitizes filenames,
  preserves accepted non-empty text exactly, enforces the configured text
  limit, generates UUID4 identifiers and UTC timestamps, and builds compact
  list snippets. Request contracts reject lone Unicode surrogates across all
  persisted strings and embedded null characters in text. List responses omit
  full text and structured fields.
- The repository owns SQL, row mapping, connection configuration, and explicit
  transactions. Each operation opens and closes its own connection in the same
  worker thread; no connection is stored in application state.
- Every connection uses `isolation_level=None`, foreign keys, a bounded busy
  timeout, `synchronous=FULL`, and a deterministic Unicode `casefold()`
  function. Writes use `BEGIN IMMEDIATE`; list count and page queries share one
  read transaction and therefore one snapshot.
- Startup creates the configured parent directory, enables and verifies WAL
  outside a transaction, runs `PRAGMA quick_check`, then creates or validates
  schema version 1. A version-zero database is adopted only when the `scans`
  table is absent. Unknown versions, malformed tables or indexes, corruption,
  a directory path, and unavailable storage prevent startup.
- Search uses parameterized `instr(casefold(...), casefold(...))` expressions,
  so Unicode case folding is supported and SQL wildcard characters remain
  literal. Dynamic sort expressions and directions come only from fixed enum
  mappings; `id ASC` is the final tie-breaker. HTTP offsets are bounded by
  SQLite's signed 64-bit integer range before repository binding.
- Stored analysis reuses the analysis feature's public `AnalysisData`
  contract and adds archive-only metadata length limits. Those limits do not
  change the version-one AI provider prompt or `ProviderAnalysisResult`.
  Stored OCR language values reuse the OCR feature's public `OcrLanguage`
  contract. The scans feature does not import either feature's service,
  pipeline, provider, or other implementation internals.
- SQLite stores text and analysis/OCR metadata only. Uploaded originals,
  rendered pages, and thumbnails are not accepted or persisted.

The module map is the navigation source for agents and maintainers. A
dependency graph generator is deferred until authentication becomes a second
consumer of SQLite; before that point it would duplicate the module map
without adding useful navigation.
