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

The module map is the navigation source for agents and maintainers. A
dependency graph generator will be considered after OCR, analysis, and scans
features exist; with only health reporting it would add no useful information.
