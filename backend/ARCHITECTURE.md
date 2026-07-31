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
- The factory installs the application lifespan. Startup creates one
  lightweight app-local `SQLiteDatabase`, auth service, scans service, and the
  analysis initialization lock. A single shared schema bootstrap runs in
  Starlette's thread pool before requests are accepted. Shutdown closes a
  lazily created analysis service and its HTTP client. No provider connection
  or database file is created during application construction or health
  reporting.
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
- `auth/dependencies.py` and auth principal schemas are the public security
  boundary used by feature routers. The principal contains user identity only;
  session-token and CSRF digests remain private auth service/dependency state.
  Other features do not import auth service, repository, security, outcomes,
  session resolution, or error internals.
- `app/storage` is shared infrastructure, not an HTTP feature. It owns SQLite
  lifecycle and schema migration; feature repositories own only their SQL.
- Routers must not contain provider, persistence, or multi-step orchestration
  logic.
- Pipelines coordinate; providers and repositories perform boundary work.
- New or moved features must be reflected in `backend/module-map.json`.

## Authentication request flow

```text
HTTP cookie + Origin/CSRF
  → auth dependency/router
  → synchronous auth service in Starlette thread pool
  → password/token security adapter + auth repository
  → shared SQLiteDatabase
```

- Authentication uses opaque server-side sessions. The browser receives a
  random token only in an HttpOnly, SameSite=Lax cookie; SQLite stores a
  32-byte SHA-256 digest. Login and registration rotate only the session
  presented by that browser, while logout revokes only the current session.
- Successful login rotates the presented session, creates the replacement,
  and clears the account rate-limit bucket under one `BEGIN IMMEDIATE`. A
  failure in any of those writes rolls back all of them.
- A stable CSRF token is derived with a domain-separated HMAC from the raw
  session token, returned by the session endpoint, and held only in frontend
  memory. SQLite stores only its digest. This lets reload and multiple tabs
  recover the same CSRF value without persisting raw CSRF material.
- Unsafe requests require an exact configured Origin. Authenticated mutations
  additionally require `X-CSRF-Token`; preflight OPTIONS is handled by CORS
  middleware before route dependencies.
- Password hashing and verification use pwdlib Argon2 in the thread pool. A
  fixed valid dummy Argon2 hash keeps unknown-user verification on the same
  expensive path without heavy import-time or per-request hash generation.
- Session absolute expiry, idle expiry, and bounded touch writes are enforced
  against UTC timestamps. Username/IP rate-limit keys use domain-separated
  HMAC-SHA-256; raw addresses and usernames are not stored in rate buckets.
- Generic protected 401 responses never emit a session-cookie deletion header:
  an old in-flight response must not delete a newer cookie with the same name
  and path. Session inspection is read-only and returns the exact anonymous
  response for an invalid cookie without changing it. Only logout retains the
  explicit revoke-and-delete contract.
- The fixed SameSite=Lax topology requires frontend and backend to remain
  same-site. The default development pair is `localhost:5500` and
  `localhost:8000`; `127.0.0.1:5500` is intentionally not advertised as an
  allowed default. Production HTTPS enables the Secure cookie setting. Trusted
  proxy address processing and cross-site cookies are outside this version.

Frontend protected operations capture both auth revision and user ID. Identity
changes cancel long-running save/export/legacy-claim and server-processing
requests, while stale completions and 401 responses are ignored. The HTTP
transport similarly versions its in-memory CSRF value. Editor provenance is
independent of mutable OCR display metadata, so server-derived text is still
removed after selectors invalidate the visible OCR snapshot.

Because the HttpOnly cookie is shared across tabs while JavaScript state is
not, successful auth changes publish only a public user-ID-or-null hint through
a versioned `BroadcastChannel`. Every hint, window focus, and transition to a
visible document revalidates `GET /api/auth/session`. The local auth revision is
advanced before the request so in-flight protected responses cannot cross that
boundary; a confirmed identity mismatch clears account-derived state before
loading the replacement owner's archive. BroadcastChannel absence degrades to
focus/visibility revalidation rather than disabling authentication.

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

The scans feature stores immutable OCR results and metadata per authenticated
owner, independently from the browser archive:

```text
JSON request
  → scans router
  → synchronous scans service in Starlette thread pool
  → owner-scoped SQLite repository
  → shared SQLiteDatabase
```

- The service is the public feature entry point. It sanitizes filenames,
  preserves accepted non-empty text exactly, enforces the configured text
  limit, generates UUID4 identifiers and UTC timestamps, and builds compact
  list snippets. Request contracts reject lone Unicode surrogates across all
  persisted strings and embedded null characters in text. List responses omit
  full text and structured fields.
- The repository owns owner-filtered SQL and row mapping. Every create, get,
  list, delete, clear, search, total, and export query receives the authenticated
  user ID; cross-owner identifiers are indistinguishable from missing records.
- Shared storage owns connection configuration and explicit transactions.
  Each operation opens and closes its own connection in the same worker thread;
  no process-wide connection is stored in application state.
- Every connection uses `isolation_level=None`, foreign keys, a bounded busy
  timeout, `synchronous=FULL`, and a deterministic Unicode `casefold()`
  function. Writes use `BEGIN IMMEDIATE`; list count and page queries share one
  read transaction and therefore one snapshot.
- Startup creates the configured parent directory, enables and verifies WAL
  outside a transaction, runs `PRAGMA quick_check`, then creates or validates
  schema version 2. The exact version-one global scans schema is migrated under
  `BEGIN IMMEDIATE`: old rows move unchanged to `legacy_scans`, while new scans
  require a user owner. Only the initial user may explicitly and atomically
  claim those rows. There is no silent assignment or deletion.
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

The module map is the navigation source for agents and maintainers. Run
`python backend/scripts/generate_dependency_graph.py --check` to validate
declared imports and ensure `backend/DEPENDENCY_GRAPH.md` is current.
