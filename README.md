# OneBridge

OneBridge is a modular AI production control plane. It turns a user request into a durable task, routes work through replaceable adapters, stores immutable artifacts, records approvals, and keeps an auditable execution trail.

The core intentionally does **not** embed upstream products. OpenClaw, Flowise, Open Design, Hermes, and future tools connect through adapters.

## MVP status

The repository now contains a runnable foundation with substantial infrastructure extracted and generalized from sibling projects:

- Task Contract v1 with Pydantic validation.
- Durable SQL task/project/artifact/approval records.
- Replaceable adapter protocol, mock fallbacks, and a configurable real Flowise Prediction adapter.
- Validated adapter registry with duplicate/configuration rejection.
- Immutable content-addressed local/S3-compatible artifact storage.
- SQLite and PostgreSQL durable worker queues with claim/finish/fail/requeue semantics.
- Strong sandbox boundary plus bounded process supervisor and subprocess-adapter primitive.
- Dependency-aware workflow graph and digest-validated checkpoints.
- Generic revision snapshots and rollback.
- Compatibility matrix with active/candidate promotion.
- Tamper-evident, secret-redacted hash-chain audit log.
- Workspace/API-key identity store with optional bearer authentication and tenant isolation.
- Artifact/release SHA-256 integrity verification.
- Approval-gated release manifest builder.
- Mock end-to-end workflow: content -> design -> code/test -> review.
- FastAPI endpoints for submit/status/artifacts/run/revise/compare/approve/release-gate/release/retry/cancel.
- CI tests, security/SBOM workflow, and local infrastructure compose for PostgreSQL + MinIO.

Flowise, Open Design MCP, and Hermes command execution can switch from mocks to real adapters through environment configuration. The repository also ships a native OpenClaw tool-plugin package, verified LINE webhook/push transport, bounded ContextForge, and explicit adapter/context plugin loading.

See:

- `docs/ARCHITECTURE.md`
- `docs/EXTRACTION_MAP.md`
- `docs/MVP_STATUS.md`
- `docs/REAL_ADAPTERS.md`
- `docs/CHANNELS_AND_SDK.md`
- `docs/PROVENANCE.md`

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
onebridge init-db
onebridge serve
```

In another terminal:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tasks \
  -H 'content-type: application/json' \
  -d '{
    "operation": "project.create",
    "input": {
      "goal": "Build a launch page",
      "required_outputs": ["content", "design", "code", "test_report"]
    },
    "context": {
      "tenant_id": "local",
      "user_id": "demo-user",
      "channel": "api"
    },
    "policy": {
      "approval": "before_publish"
    }
  }'
```

Run the task with the mock adapter set:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tasks/<task_id>/run
```

## Architecture boundary

```text
OpenClaw / API / LINE
        |
        v
  OneBridge Contract
        |
        v
 OneBridge Control Plane
  | state | artifacts | approval | audit |
        |
        +--> Flowise Adapter
        +--> Open Design Adapter
        +--> Hermes Adapter
        +--> future adapters
```

OneBridge owns control-plane truth. Upstream-specific names and payloads stay inside adapters.

## Provenance

The current foundation deliberately reuses and generalizes patterns from the owner's other repositories:

- Vera: approval, sandbox, recovery/checkpoints, audit boundaries.
- CutPilot: queue, worker, object-store, workspace identity.
- FlowCraft-AI: revisions and rollback.
- LyricGuard: provider capability/registry contracts.
- MiniMax-H3: deterministic workflow validation patterns.
- SONICRAFT AI Strings: SHA-256 release integrity and provenance gates.

See `docs/EXTRACTION_MAP.md` for the detailed mapping.


## Real Flowise adapter

Set both values to replace the Flowise mock:

```bash
export ONEBRIDGE_FLOWISE_BASE_URL=http://127.0.0.1:3000
export ONEBRIDGE_FLOWISE_CHATFLOW_ID=<chatflow-id>
# optional:
export ONEBRIDGE_FLOWISE_API_KEY=<chatflow-api-key>
```

For a remote HTTPS Flowise URL, OneBridge derives a narrow egress rule for the configured host and the `/api/v1` path. Arbitrary `overrideConfig` keys are blocked unless explicitly listed in `ONEBRIDGE_FLOWISE_ALLOWED_OVERRIDE_KEYS`.


## Review and release

Generated text/HTML/JSON artifacts can be revised without overwriting history. A new human revision supersedes the previous latest revision, returns to `awaiting_review`, and must be explicitly approved. Two artifact revisions can be compared through the bounded compare endpoint.

Release is fail closed:

```text
latest required artifacts
        -> all present
        -> all approved
        -> SHA-256 shape valid
        -> task succeeded
        -> release manifest artifact
```

Useful endpoints:

```text
POST /api/v1/tasks/{task_id}/artifacts/{artifact_id}/revisions
GET  /api/v1/tasks/{task_id}/artifacts/{left_id}/compare/{right_id}
GET  /api/v1/tasks/{task_id}/release-gate
POST /api/v1/tasks/{task_id}/release
GET  /api/v1/adapters
```

## OpenClaw shim

`OpenClawOneBridgeShim` is intentionally stateless. It exposes the planned OneBridge tool surface—submit, status, artifacts, approve, retry, and cancel—without reading OneBridge's database or invoking workers directly. OpenClaw-native registration remains a thin integration layer on top of this client.


## Real Open Design / Hermes / telemetry

See `docs/REAL_ADAPTERS.md` for the MCP result contract, Hermes request/result
manifest, sandbox behavior, and optional OTLP/HTTP telemetry configuration.


## Review Portal

Open a task review surface at:

```text
/review/<task_id>
```

The portal uses the same-origin API and keeps any pasted bearer key in page memory only. It supports artifact listing, text/HTML/JSON loading, bounded PNG/JPEG/WebP/GIF/PDF previews, human revisions, approve/reject, revision comparison, release-gate checks, and release creation.

## Compatibility qualification and promotion

Configured real adapters can be registered and qualified before activation:

```text
POST /api/v1/adapters/{adapter_id}/compatibility/candidate
POST /api/v1/adapters/{adapter_id}/qualify
POST /api/v1/adapters/{adapter_id}/compatibility/{version}/promote
GET  /api/v1/compatibility
```

Set `ONEBRIDGE_REQUIRE_QUALIFIED_ADAPTERS=true` for production startup to fail
closed unless every configured adapter is non-mock, active, and backed by a
passing stored qualification.

## OpenClaw and LINE

`integrations/openclaw-onebridge` is a native OpenClaw TypeScript tool plugin for submit/status/artifacts/approve/retry/cancel. LINE now has raw-body signature verification, webhook task ingress, reply/push transport, `/status <task_id>`, and channel-safe progress messages without forwarding provider error dumps.


## ContextForge and plugin SDK

Tasks can request bounded knowledge through `policy.knowledge_scopes`. Explicitly
configured context-provider entry points feed ContextForge, which de-duplicates
content by SHA-256 and enforces visible item/byte budgets before adding a
`context_bundle` to adapter input.

Python adapter plugins use the `onebridge.adapters` entry-point group and are
loaded only when listed in `ONEBRIDGE_ADAPTER_PLUGINS`. Context providers use
`onebridge.context_providers` and `ONEBRIDGE_CONTEXT_PLUGINS`. Existing
artifact kinds can be routed to custom adapters through
`ONEBRIDGE_OUTPUT_ROUTES_JSON`.
