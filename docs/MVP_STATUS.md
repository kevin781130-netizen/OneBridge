# MVP implementation status

This file maps the original OneBridge backlog to the current repository.

| Item | Current state | Notes |
|---|---|---|
| OB-001 Monorepo baseline | Implemented | Python package, tests, CI, deployment compose |
| OB-002 Task Contract v1 | Implemented foundation | Pydantic contract + task/project/artifact models |
| OB-003 Mock adapters | Implemented | Flowise, Open Design, Hermes mocks |
| OB-004 Durable workflow/checkpoint | Partial | dependency graph + digest checkpoints; LangGraph integration not yet added |
| OB-005 Object store | Implemented foundation | content-addressed local store + S3/MinIO client |
| OB-006 Flowise adapter | Pending | mock only |
| OB-007 Flowise structured output | Pending | mock contract only |
| OB-008 Open Design adapter | Pending | mock only |
| OB-009 Hermes sandbox worker | Partial | sandbox + worker queue foundation; real Hermes bridge pending |
| OB-010 OpenClaw shim | Pending | no real OpenClaw bridge yet |
| OB-011 LINE progress mapping | Pending | requires OpenClaw/LINE integration |
| OB-012 Design revision loop | Partial | generic revision/rollback primitive implemented |
| OB-013 Approval gate | Implemented foundation | approval records + fail-closed approval primitive |
| OB-014 Compatibility Matrix | Implemented foundation | active/candidate/blocked version registry |
| OB-015 OpenTelemetry | Pending | not wired yet |
| OB-016 Notices/SBOM | Partial | notices and provenance docs present; SBOM generation pending |
| OB-017 Blue/Green OpenClaw | Partial foundation | compatibility candidate promotion exists; OpenClaw-specific automation pending |
| OB-018 Tenant / identity | Partial | workspace/API-key store implemented; API auth wiring pending |
| OB-019 ContextForge | Pending | no integration yet |
| OB-020 Adapter marketplace/SDK | Partial | adapter protocol and capability contract exist; packaging/discovery pending |

## Current priority

The next high-value work is to replace mocks with real adapters while preserving the already-built control-plane boundaries:

1. Flowise REST adapter.
2. Open Design bridge.
3. Hermes isolated worker bridge.
4. OpenClaw tool shim.
5. Telemetry and compatibility qualification around those real adapters.
