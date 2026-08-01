# Visual Scan agent guide

Choose the navigation map before reading implementation files.

For backend work:

1. Open `backend/module-map.json`.
2. Read `backend/ARCHITECTURE.md`.
3. Locate the target feature entry point in the module map.
4. Read only that feature's schemas, router, optional service/pipeline,
   provider/repository adapters, and scoped tests unless a declared dependency
   requires more context.
5. Do not import or depend on another feature's internal modules. Use its
   public entry point or contract.
6. When adding or moving a feature, update `backend/module-map.json`.
7. Run the feature's scoped tests first, then the complete backend test suite.

Create service, pipeline, provider, and repository layers only when the feature
has logic that needs them. Do not add placeholder modules.

For frontend, browser integration, OCR-model tooling, or demo-corpus work:

1. Open `frontend/module-map.json`.
2. Locate the owning module and read its entrypoint, contracts, implementation,
   and scoped tests. Read `composition` only when integration wiring changes.
3. Treat `frontend/app.js` as orchestration; keep reusable lifecycle and
   validation rules in the owning utility module.
4. Keep all backend HTTP requests in `frontend/utils/api.js`.
5. When adding or moving a frontend module, script, contract, or scoped test,
   update `frontend/module-map.json`.
6. Run the owning Node test file first, then `npm test` and the module-map test.

In the frontend map, `depends_on` is the exact set of direct repo-local static
imports from a module's entrypoint and implementation files. Do not use it for
application-level wiring; declare that under `composition` instead.

For a change that crosses frontend and backend boundaries, consult both maps
before editing. Keep the backend dependency graph generated only from the
backend map.
