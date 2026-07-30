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
  → OCR pipeline
  → in-memory preprocessing
  → Tesseract provider
```

- The router reads at most the configured byte limit plus one byte, maps
  feature errors to HTTP responses, and moves synchronous OCR work to
  Starlette's thread pool.
- The service is the public feature entry point and owns request-level
  invariants such as filename normalization and conditional threshold
  validation.
- The pipeline coordinates one preprocessing pass and one provider call.
- Preprocessing validates the declared MIME type against the decoded image,
  checks pixel limits before full decoding, verifies the image, applies EXIF
  orientation, and returns an in-memory Pillow image.
- The provider owns the single `pytesseract.image_to_data()` call and
  normalizes Tesseract availability and timeout failures.

The application does not persist uploaded originals and does not create its
own temporary files. FastAPI's multipart parser may use system temporary
storage through `SpooledTemporaryFile`.

The module map is the navigation source for agents and maintainers. A
dependency graph generator will be considered after OCR, analysis, and scans
features exist; the current feature set is still too small for that generator
to add useful information.
