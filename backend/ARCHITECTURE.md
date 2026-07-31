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
- `app/main.py` is the production ASGI entry point. It imports the factory and
  creates `app` for Uvicorn.
- `app/api/router.py` composes public feature routers.
- Runtime settings injected by the factory are read from
  `request.app.state.settings`. Feature dependencies must not reload global
  settings and bypass an explicitly configured application instance.

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

The module map is the navigation source for agents and maintainers. A
dependency graph generator will be considered after OCR, analysis, and scans
features exist; the current feature set is still too small for that generator
to add useful information.
