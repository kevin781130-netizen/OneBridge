# OneBridge

OneBridge is a modular AI production control plane. It turns a user request into a durable task, routes work through replaceable adapters, stores immutable artifacts, records approvals, and keeps an auditable execution trail.

The core intentionally does **not** embed upstream products. OpenClaw, Flowise, Open Design, Hermes, and future tools connect through adapters.

## MVP status

The repository now contains a runnable foundation with substantial infrastructure extracted and generalized from sibling projects:

- Task Contract v1 with Pydantic validation.
- Durable SQL task/project/artifact/approval records.
- Replaceable adapter protocol and mock adapters.
- Validated adapter registry with duplicate/configuration rejection.
- Immutable content-addressed local/S3-compatible artifact storage.
- SQLite durable worker queue with claim/finish/fail/requeue semantics.
- Strong sandbox boundary for worker execution.
- Dependency-aware workflow graph and digest-validated checkpoints.
- Generic revision snapshots and rollback.
- Compatibility matrix with active/candidate promotion.
- Tamper-evident hash-chain audit log.
- Workspace/API-key identity store.
- Artifact/release SHA-256 integrity verification.
- Approval-gated release manifest builder.
- Mock end-to-end workflow: content -> design -> code/test -> review.
- FastAPI endpoints for submit/status/artifacts/run/approve/retry/cancel.
- CI tests and a local infrastructure compose file for PostgreSQL + MinIO.

The foundation is intentionally separated from real upstream adapters. Real Flowise, Open Design, Hermes, and OpenClaw integrations are the next milestones.

See:

- `docs/ARCHITECTURE.md`
- `docs/EXTRACTION_MAP.md`
- `docs/MVP_STATUS.md`
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
