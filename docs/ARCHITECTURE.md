# OneBridge architecture

## Product boundary

OneBridge owns control-plane state and never depends on upstream internal Python/TypeScript types.

Core responsibilities:

1. Validate Task Contract v1.
2. Persist project/task state.
3. Route capabilities through adapters.
4. Store immutable artifacts by SHA-256.
5. Record human approval/rejection.
6. Preserve audit/recovery evidence.
7. Keep upstream compatibility changes inside adapters.

## Current foundation

```text
FastAPI
  |
  v
OneBridgeService
  |-- SQLAlchemy state (SQLite for local tests; PostgreSQL supported by URL/driver)
  |-- AdapterRegistry
  |     |-- Mock Flowise
  |     |-- Mock Open Design
  |     `-- Mock Hermes
  |-- Local/S3 object store
  `-- approval records
```

## Next adapters

The real adapters will replace mocks one at a time while keeping the same core contract:

- `adapters/flowise`: Prediction REST API + structured output.
- `adapters/open_design`: MCP/CLI bridge + artifact collection.
- `adapters/hermes`: isolated Docker/remote sandbox job.
- `adapters/openclaw`: thin tool shim for submit/status/artifacts/approve/retry/cancel.

## State rules

Task status: `queued -> running -> waiting_approval -> succeeded` with `failed`, `blocked`, and `cancelled` side paths.

Artifact status: `generated/awaiting_review -> approved|rejected`.

No release authority is inferred from model output. Approval is a separate human action.
