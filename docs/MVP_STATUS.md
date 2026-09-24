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
| OB-008 Open Design adapter | Implemented foundation | real MCP Streamable HTTP client + tool call + artifact collection; upstream tool contract qualification pending |
| OB-009 Hermes sandbox worker | Implemented foundation | real argv-only command bridge + materialized inputs + strong sandbox + bounded supervisor + multi-output code/test collection; pinned Hermes qualification pending |
| OB-010 OpenClaw shim | Implemented foundation | stateless HTTP shim + native tool schemas/dispatcher for submit/status/artifacts/approve/retry/cancel |
| OB-011 LINE progress mapping | Implemented foundation | channel-safe task/release progress codes + API endpoint; LINE webhook/push transport pending |
| OB-012 Design revision loop | Implemented foundation | human revisions, compare, approve/reject, release controls and zero-dependency Review Portal UI |
| OB-013 Approval/release gate | Implemented foundation | latest-required-revision approval semantics + fail-closed release evaluation + release manifest artifact |
| OB-014 Compatibility Matrix | Implemented foundation | persistent candidate/active/blocked registry + stored qualification evidence + promotion API |
| OB-015 OpenTelemetry | Implemented foundation | optional OTLP/HTTP traces + metrics for task lifecycle and adapter execution |
| OB-016 Notices/SBOM | Implemented foundation | notices/provenance docs + security workflow + CycloneDX SBOM artifact + secret scan |
| OB-017 Blue/Green OpenClaw | Partial foundation | evidence-gated active/candidate promotion + optional production startup gate; OpenClaw deployment switch automation pending |
| OB-018 Tenant / identity | Implemented foundation | workspace/API-key store + optional bearer auth + tenant-scoped task isolation; RBAC/SSO pending |
| OB-019 ContextForge | Pending | no integration yet |
| OB-020 Adapter marketplace/SDK | Partial | adapter protocol and capability contract exist; packaging/discovery pending |

## Current priority

The next high-value work is to replace mocks with real adapters while preserving the already-built control-plane boundaries:

1. Run the implemented `onebridge qualify` harness against pinned Flowise, Open Design, and Hermes deployments.
2. Run live qualification and promote pinned real adapter versions to active.
3. Add OpenClaw deployment registration plus LINE webhook/push transport around the implemented tool/progress contracts.
4. Add visual/binary artifact preview support to the implemented Review Portal.
5. Add ContextForge and adapter SDK/discovery after the production path is qualified.
