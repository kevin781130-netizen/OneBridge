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
| OB-009 Hermes sandbox worker | Partial | strong sandbox + process supervisor + subprocess adapter primitive + SQLite/PostgreSQL worker queues; real Hermes bridge pending |
| OB-010 OpenClaw shim | Pending | no real OpenClaw bridge yet |
| OB-011 LINE progress mapping | Pending | requires OpenClaw/LINE integration |
| OB-012 Design revision loop | Partial | generic revision/rollback primitive implemented |
| OB-013 Approval gate | Implemented foundation | approval records + fail-closed approval primitive |
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
