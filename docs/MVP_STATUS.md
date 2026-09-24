# MVP implementation status

This file maps the original OneBridge backlog to the current repository.

| Item | Current state | Notes |
|---|---|---|
| OB-001 Monorepo baseline | Implemented | Python package, tests, CI, deployment compose |
| OB-002 Task Contract v1 | Implemented foundation | Pydantic contract + task/project/artifact models |
| OB-003 Mock adapters | Implemented | Flowise, Open Design, Hermes mocks |
| OB-004 Durable workflow/checkpoint | Partial | dependency graph + digest checkpoints; LangGraph integration not yet added |
| OB-005 Object store | Implemented foundation | content-addressed local store + S3/MinIO client |
| OB-006 Flowise adapter | Implemented foundation | real Prediction API adapter with local/HTTPS policy boundary; mock fallback when unconfigured |
| OB-007 Flowise structured output | Implemented foundation | deterministic JSON/media/filename/size validation before artifact acceptance |
| OB-008 Open Design adapter | Pending | mock only |
| OB-009 Hermes sandbox worker | Partial | strong sandbox + process supervisor + disposable per-job workspace + generic worker executor + SQLite/PostgreSQL queues; real Hermes command/API bridge pending |
| OB-010 OpenClaw shim | Implemented foundation | stateless submit/status/artifacts/approve/retry/cancel HTTP shim; OpenClaw-native tool registration pending |
| OB-011 LINE progress mapping | Pending | requires OpenClaw/LINE integration |
| OB-012 Design revision loop | Implemented foundation | human text revisions, supersede lineage, bounded revision comparison, approval flow and audit; visual portal UI pending |
| OB-013 Approval/release gate | Implemented foundation | latest-required-revision approval semantics + fail-closed release evaluation + release manifest artifact |
| OB-014 Compatibility Matrix | Implemented foundation | active/candidate/blocked version registry |
| OB-015 OpenTelemetry | Pending | not wired yet |
| OB-016 Notices/SBOM | Implemented foundation | notices/provenance docs + security workflow + CycloneDX SBOM artifact + secret scan |
| OB-017 Blue/Green OpenClaw | Partial foundation | compatibility candidate promotion exists; OpenClaw-specific automation pending |
| OB-018 Tenant / identity | Implemented foundation | workspace/API-key store + optional bearer auth + tenant-scoped task isolation; RBAC/SSO pending |
| OB-019 ContextForge | Pending | no integration yet |
| OB-020 Adapter marketplace/SDK | Partial | adapter protocol and capability contract exist; packaging/discovery pending |

## Current priority

The next high-value work is to replace mocks with real adapters while preserving the already-built control-plane boundaries:

1. Qualify the real Flowise adapter against a pinned Flowise deployment.
2. Open Design bridge.
3. Hermes isolated worker bridge using the extracted sandbox/supervisor/queue stack.
4. OpenClaw tool shim.
5. OpenTelemetry and compatibility qualification around the real adapters.
