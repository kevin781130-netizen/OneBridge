# OneBridge

OneBridge is a modular AI production control plane. It turns a user request into a durable task, routes work through replaceable adapters, stores immutable artifacts, records approvals, and keeps an auditable execution trail.

The core intentionally does **not** embed upstream products. OpenClaw, Flowise, Open Design, Hermes, and future tools connect through adapters.

## MVP status

The repository now contains the first runnable foundation:

- Task Contract v1 with Pydantic validation.
- Durable SQL task/project/artifact/approval records.
- Replaceable adapter protocol and mock adapters.
- Immutable content-addressed local/S3-compatible artifact storage.
- Mock end-to-end workflow: content -> design -> code/test -> review.
- Human approval gate.
- Retry and cancel state handling.
- FastAPI endpoints for submit/status/artifacts/run/approve/retry/cancel.
- CI tests and a local infrastructure compose file for PostgreSQL + MinIO.

This is the first foundation pass, not the finished 12-week product. Real OpenClaw/Flowise/Open Design/Hermes adapters and compatibility/blue-green automation remain separate milestones.

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

This foundation deliberately reuses and generalizes patterns already developed in the owner's other repositories, especially Vera (approval/recovery/sandbox boundaries) and CutPilot (queue/object-store/worker patterns). See `docs/PROVENANCE.md`.
