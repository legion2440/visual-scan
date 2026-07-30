# Visual Scan agent guide

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
